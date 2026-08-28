"""Flush helper: deletes all seed-able data."""

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
    TimeFilter,
    User,
    WorkItem,
)


def flush_seed_data() -> None:
    """Delete all seed-able data.

    EventHistory/AuditLog own append-only / immutable DB triggers that need to
    be disabled temporarily so the seed command can rerun from scratch.

    Der gesamte Flush läuft in einer Transaktion: schlägt ein später Schritt
    fehl (z.B. ``ALTER TABLE`` mangels Rechten), bleiben die früheren Deletes
    nicht committed zurück. Refs #1040 (S1).
    """
    with transaction.atomic():
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
        Client.objects.all().delete()
        DocumentTypeField.objects.all().delete()
        DocumentType.objects.all().delete()
        FieldTemplate.objects.all().delete()
        TimeFilter.objects.all().delete()
        Settings.objects.all().delete()
        User.objects.all().delete()
        Facility.objects.all().delete()
        Organization.objects.all().delete()
