"""Flush helper: deletes all seed-able data."""

import contextlib
import os
import tempfile

from django.conf import settings
from django.db import connection, transaction

from core.models import (
    Activity,
    Attachment,
    AuditLog,
    Case,
    Client,
    DeletionRequest,
    DocumentType,
    DocumentTypeField,
    Episode,
    Event,
    EventHistory,
    Facility,
    FieldTemplate,
    ImportJob,
    LegalHold,
    Milestone,
    Organization,
    OutcomeGoal,
    QuickTemplate,
    RectificationRequest,
    RetentionProposal,
    Settings,
    ShiftHandoverNote,
    TimeFilter,
    User,
    WorkItem,
)
from core.services.audit.hwm import HighWaterMarkStore
from core.services.dynamic_protection import lock_dynamic_protection_many


def _read_anchor(path: str) -> bytes | None:
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def _restore_anchor(path: str, previous: bytes | None) -> None:
    if previous is None:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)
        return
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".audit_hwm-restore-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(previous)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


class _AuditAnchorReset:
    """Replace the external audit anchor with a signed, empty store (Refs #2708).

    The flush empties the AuditLog and deletes every facility, but the anchor
    file lives outside the database and survives. An anchor left behind points
    to chains that were removed on purpose; ``verify_audit_chain`` would then
    abort on every following run (orphaned chain, Refs #2296). Only this path
    resets the anchor -- outside the flush the orphan check stays strict.

    The file is not part of the database transaction. Entered before
    ``transaction.atomic()``, this guard leaves its block only after the
    commit: if the block fails once the replacement has started -- an error,
    a failed commit or a catchable interrupt such as ``KeyboardInterrupt`` --
    the previous file is put back, so the full audit log is never left behind
    an empty anchor. If putting it back fails as well, that error propagates
    with the original one as its context.
    """

    def __enter__(self):
        self.path = getattr(settings, "AUDIT_HWM_PATH", "") or ""
        self.previous = _read_anchor(self.path) if self.path else None
        self.replaced = False
        return self

    def reset(self) -> None:
        if self.path:
            # Set before the save: an interrupt can arrive after the file was
            # replaced but before ``save()`` returns. Putting the previous
            # bytes back when the file was not replaced is harmless.
            self.replaced = True
            HighWaterMarkStore(path=self.path, chains={}).save()

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None and self.replaced:
            _restore_anchor(self.path, self.previous)
        return False


def flush_seed_data() -> None:
    """Delete all seed-able data.

    EventHistory/AuditLog own append-only / immutable DB triggers that need to
    be disabled temporarily so the seed command can rerun from scratch.

    Der gesamte Flush läuft in einer Transaktion: schlägt ein später Schritt
    fehl (z.B. ``ALTER TABLE`` mangels Rechten), bleiben die früheren Deletes
    nicht committed zurück. Refs #1040 (S1).

    The external audit anchor is replaced as the last step inside the flush
    transaction (Refs #2708); the file itself is written before the commit and
    is not rolled back with it. If it cannot be written, the whole flush rolls
    back instead of leaving an empty AuditLog behind a stale anchor; if the
    commit fails afterwards or the run is interrupted, the previous file is put
    back. Only a hard crash between the file replacement and the commit, or a
    failure to put the previous file back, leaves an empty anchor behind the
    full audit log; the next chain check then anchors it like a first run.
    The seed command runs this flush as its outermost transaction.
    """
    with _AuditAnchorReset() as anchor, transaction.atomic():
        lock_dynamic_protection_many(Facility.objects.values_list("pk", flat=True), exclusive=True)
        Attachment.objects.all().delete()
        Activity.objects.all().delete()
        LegalHold.objects.all().delete()
        RetentionProposal.objects.all().delete()
        DeletionRequest.objects.all().delete()
        # M4 (Refs #1060): beide haengen NICHT am Kaskadenpfad der unten
        # geloeschten Objekte, sondern nur an ``Facility`` — die faellt erst
        # ganz am Ende. ``ImportJob.document_type`` ist zudem PROTECT: ein
        # liegengebliebener Lauf laesst ``DocumentType.objects.all().delete()``
        # weiter unten mit ``ProtectedError`` scheitern und macht damit den
        # kompletten ``seed --flush`` unbrauchbar — inklusive des
        # E2E-``base_url``-Fixtures, das genau darueber seinen Startzustand
        # herstellt. ``RectificationRequest`` MUSS hier stehen: seit Refs #2068
        # (c) ist ``target_client`` PROTECT statt CASCADE — die Antraege kommen
        # also nicht mehr ueber die Person mit, sondern blockieren deren
        # Loeschung weiter unten. (Vor #2068 stand die Zeile bereits hier,
        # damals als bewusste Unabhaengigkeit vom Kaskadenpfad.)
        RectificationRequest.objects.all().delete()
        ImportJob.objects.all().delete()
        # Refs #2068 (b): ``QuickTemplate.document_type`` ist seit dem
        # Hygiene-Buendel PROTECT statt CASCADE — aus demselben Grund und mit
        # derselben Folge wie bei ``ImportJob`` oben: ohne diese Zeile
        # scheitert ``DocumentType.objects.all().delete()`` weiter unten mit
        # ``ProtectedError``, und ``seed --flush`` waere unbrauchbar. Die
        # Vorlagen kamen vorher still ueber die Kaskade mit.
        QuickTemplate.objects.all().delete()
        WorkItem.objects.all().delete()
        Milestone.objects.all().delete()
        OutcomeGoal.objects.all().delete()
        Episode.objects.all().delete()
        Case.objects.all().delete()
        # EventHistory has append-only DB trigger -> temporarily disable.
        # Re-ENABLE ALWAYS (nicht ENABLE) haelt den gehaerteten Ruhezustand aus
        # Migration 0107 (A1.4, Refs #1026) -- ein einfaches ENABLE wuerde den
        # Trigger auf 'O' zurueckstufen.
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE core_eventhistory DISABLE TRIGGER eventhistory_no_delete")
        EventHistory.objects.all().delete()
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE core_eventhistory ENABLE ALWAYS TRIGGER eventhistory_no_delete")
        # AuditLog has immutable DB trigger -> temporarily disable
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE core_auditlog DISABLE TRIGGER auditlog_immutable")
        AuditLog.objects.all().delete()
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE core_auditlog ENABLE ALWAYS TRIGGER auditlog_immutable")
        Event.objects.all().delete()
        # Beim vollstaendigen Leeren aller Personen verliert der Tombstone-Verweis
        # seinen Zweck. Ausserhalb dieses Flushs bleibt PROTECT bewusst bestehen.
        Client.objects.filter(merged_into__isnull=False).update(merged_into=None)
        Client.objects.all().delete()
        DocumentTypeField.objects.all().delete()
        DocumentType.objects.all().delete()
        FieldTemplate.objects.all().delete()
        # Refs #2042: VOR den Zeitfenstern. ``ShiftHandoverNote.time_filter``
        # traegt seit dem Cross-Review ``on_delete=PROTECT`` — ein Zeitfenster
        # mit Notizen ist bewusst nicht loeschbar, damit Uebergabewissen nicht
        # an Frist und Audit-Log vorbei verschwindet. Der Seed-Flush raeumt
        # ohnehin ALLES ab; er muss die Notizen nur selbst mitnehmen, statt am
        # Schutz haengenzubleiben.
        ShiftHandoverNote.objects.all().delete()
        TimeFilter.objects.all().delete()
        Settings.objects.all().delete()
        User.objects.all().delete()
        Facility.objects.all().delete()
        Organization.objects.all().delete()
        anchor.reset()
