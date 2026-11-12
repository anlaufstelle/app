"""AuditLog-Pruning fuer den Retention-Pfad (#744).

Aus ``services/retention.py`` ausgekoppelt — semantisch eigenes Thema
(append-only-Trigger, separate Aufbewahrungsfrist).
"""

import calendar
import hmac
import logging
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from core.models import AuditLog, Case, Client, Event, Settings
from core.retention.legal_holds import (
    exclude_cases_with_active_holds,
    exclude_clients_with_active_holds,
    get_active_hold_target_ids,
)
from core.services.audit import audit_event
from core.services.audit.chain import _checkpoint_boundaries, advisory_lock_name, compute_entry_hash
from core.services.dashboard.release_log import RELEASE_EXPORT_Q
from core.services.dynamic_protection import lock_dynamic_protection
from core.services.dynamic_records import DYNAMIC_DATA_MODELS, dynamic_case_lookup, dynamic_client_lookup
from core.services.system import bypass_replication_triggers

logger = logging.getLogger(__name__)

# A6.3 (Refs #1024 / #1016): Aktionen, die NICHT der Routine-Retention
# unterliegen. SECURITY_VIOLATION sind Breach-Detection-Forensiknachweise;
# RETENTION_RUN_COMPLETED ist der Marker, aus dem das Compliance-Dashboard den
# letzten erfolgreichen Lauf liest (sonst „UNKNOWN"). Beide tragen
# System-Metadaten, keine Klient-PII — die längere Aufbewahrung ist gerechtfertigt.
# Refs #1070: AUDIT_PRUNE_CHECKPOINT ist der HMAC-Ketten-Tombstone selbst — er
# protokolliert die ``boundary_hash``-Grenzen geloeschter Zeilen und darf nie
# gepruned werden, sonst verloere ``verify_chain`` die Legitimation der
# Diskontinuitaet.
# Refs #1770 (K4a): Berichts-Freigaben sind der Nachweis, **welche
# Statistik-Zeitraeume wann an wen herausgegeben wurden** — die Datenbasis der
# Freigabe-Uebersicht. Wuerde die Routine-Frist sie kappen, taeuschte die
# Uebersicht Vollstaendigkeit vor: eine im vierten Jahr herausgegebene
# Jahresauswertung ueberschnitte sich mit Monatsberichten, die der Log nicht
# mehr kennt, und die Ueberlappungswarnung bliebe stumm. Ein stiller falscher
# Negativ ist hier schlechter als laengere Aufbewahrung.
#
# **Die Ausnahme ist so eng wie ihre Begruendung.** Sie haengt NICHT an der
# Aktion ``EXPORT`` — die ist eine Sammelklasse, unter der auch die
# Art.-15-Datenauskunft einer Person (``target_id`` = Klienten-UUID) und die
# DSGVO-Dokument-Downloads laufen. Deren ``detail`` ist im PII-Sweep als
# personenbeziehbar klassifiziert (``src/tests/_residue_expectations.py``); sie
# dauerhaft von der Frist zu befreien waere eine Speicherbegrenzungs-Regression
# und von der Zweckbindung unten NICHT gedeckt. Ausgenommen ist deshalb allein,
# was einen **Berichtszeitraum** traegt (``RELEASE_EXPORT_Q``).
#
# **DSGVO-Begruendung** (Zweckbindung, Art. 5 Abs. 1 lit. b + Abs. 2): Der Zweck
# ist der Nachweis herausgegebener **Statistiken**, nicht die Beobachtung von
# Beschaeftigten. Das ``detail`` dieser Teilmenge traegt Zeitraum, Format und die
# frei erfassten Empfaenger-/Zweckangaben; die Artefakte selbst sind
# k-anonymisiert, es gibt dort keine Pseudonyme und keine Personenzeilen. Die
# **Freitextfelder sind Freitext** — die Datenminimierung dort ist eine
# Betreiber-Obliegenheit (Empfehlung in der Betreiber-Doku: Organisation statt
# Person nennen), keine technische Zusicherung; laengenbegrenzt, aber nicht
# inhaltlich geprueft. Die handelnde Person steht wie in jedem Audit-Eintrag
# drin. Damit folgt die Aufbewahrung derselben Rechenschaftslogik wie
# SECURITY_VIOLATION. Festgehalten in ``docs/security-notes.md``
# § Disclosure-Analyse (K4a).
#
# Refs #1957: Die sieben Bearbeitungs-/Zuordnungs-Aktionen (``EVENT_UPDATE``,
# ``EPISODE_UPDATE``, ``GOAL_UPDATE``, ``CASE_EVENT_ASSIGNED``,
# ``CASE_EVENT_REMOVED``, ``ATTACHMENT_UPLOAD``, ``ATTACHMENT_DELETE``) stehen
# bewusst **nicht** hier: sie protokollieren Routine-Facharbeit an lebenden
# Faellen, nicht die Rechenschaft ueber eine Herausgabe oder einen
# Sicherheitsvorfall. Fuer sie gilt die regulaere 24-Monats-Frist aus
# ``auditlog_retention_months`` — eine dauerhafte Aufbewahrung waere gegenueber
# den Beschaeftigten (§ 26 BDSG) und der Speicherbegrenzung (Art. 5 Abs. 1
# lit. e DSGVO) nicht zu rechtfertigen.
#
# Refs #2065 (ADR-050 M-4): EVENT_HISTORY_REDACTED folgt derselben Logik wie
# AUDIT_PRUNE_CHECKPOINT. Er ist der Beleg dafuer, dass die Append-only-
# Garantie fuer eine bestimmte Charge legitim geoeffnet wurde — die einzige
# Spur, aus der sich spaeter rekonstruieren laesst, WER wann welche
# Historienzeilen geleert hat. Wuerde die Routine-Frist ihn kappen, bliebe die
# redigierte Zeile (Stufe 2 laeuft ab ``redacted_at``, nicht ab dem Nachweis)
# unter Umstaenden ohne jeden Beleg stehen: genau die stille Veraenderung
# append-only-geschuetzter Daten, gegen die die Trigger da sind. Das Detail
# traegt Mengen, Zeitgrenzen und Chargen-UUIDs — keine Klient-PII.
PRUNE_EXEMPT_ACTIONS = (
    AuditLog.Action.SECURITY_VIOLATION,
    AuditLog.Action.RETENTION_RUN_COMPLETED,
    AuditLog.Action.AUDIT_PRUNE_CHECKPOINT,
    AuditLog.Action.EVENT_HISTORY_REDACTED,
)

# Das vollstaendige Ausnahme-Praedikat: pauschale Aktionen ODER die eng gefasste
# Berichts-Freigabe. Beide Stellen unten (Loeschmenge und Ueberlebenden-Menge)
# MUESSEN dasselbe Praedikat verwenden — sonst meldet ``verify_chain`` falsche
# Kettenbrueche, weil ein Ueberlebender nicht als solcher erkannt wurde.
PRUNE_EXEMPT_Q = Q(action__in=PRUNE_EXEMPT_ACTIONS) | RELEASE_EXPORT_Q


def _months_before(moment: datetime, months: int) -> datetime:
    """Kalendergenaue Monats-Subtraktion (A6.4, Refs #1024 / #1016).

    Zieht ``months`` Kalendermonate von ``moment`` ab und erhaelt Uhrzeit +
    tzinfo. Der Tag wird auf den letzten gueltigen Tag des Zielmonats geklemmt
    (z.B. 31.03. - 1 Monat = 28./29.02.). Vermeidet die fruehere 30-Tage-
    Naeherung (``months * 30``), die bei 24 Monaten ~10 Tage zu frueh schnitt.
    Analog zu ``_add_months`` in ``services/case/workitems.py``, aber
    datetime-/tz-erhaltend fuer den Retention-Cutoff.
    """
    month_index = moment.month - 1 - months
    year = moment.year + month_index // 12
    month = month_index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def _held_targets(facility):
    """Protect direct holds and the record relationships covered by them."""
    held_clients = get_active_hold_target_ids(facility, "Client")
    held_cases = get_active_hold_target_ids(facility, "Case")
    held_events = get_active_hold_target_ids(facility, "Event")
    clients = Client.objects.for_facility(facility)
    cases = Case.objects.for_facility(facility)
    protected_clients = (
        set(clients.exclude(pk__in=exclude_clients_with_active_holds(clients, facility)).values_list("pk", flat=True))
        | held_clients
    )
    protected_cases = (
        set(cases.exclude(pk__in=exclude_cases_with_active_holds(cases, facility)).values_list("pk", flat=True))
        | held_cases
    )
    protected_events = (
        set(
            Event.objects.for_facility(facility)
            .filter(Q(pk__in=held_events) | Q(client_id__in=held_clients) | Q(case_id__in=held_cases))
            .values_list("pk", flat=True)
        )
        | held_events
    )
    protected = {Client: protected_clients, Case: protected_cases, Event: protected_events}
    result = Q(pk__in=[])
    for model in DYNAMIC_DATA_MODELS:
        if model not in protected:
            direct = get_active_hold_target_ids(facility, model.__name__)
            condition = Q(pk__in=direct)
            client_lookup = dynamic_client_lookup(model)
            case_lookup = dynamic_case_lookup(model)
            if client_lookup:
                condition |= Q(**{f"{client_lookup}__in": held_clients})
            if case_lookup:
                condition |= Q(**{f"{case_lookup}__in": held_cases})
            protected[model] = (
                set(model.objects.for_facility(facility).filter(condition).values_list("pk", flat=True)) | direct
            )
        result |= Q(target_type=model.__name__, target_id__in=[str(pk) for pk in protected[model]])
    return result


def _lock_audit_targets(qs, facility):
    """Lock parent anchors before all affected carriers and the audit chain."""
    ids = {model.__name__: set() for model in DYNAMIC_DATA_MODELS}
    for kind, value in qs.filter(target_type__in=ids).values_list("target_type", "target_id"):
        try:
            ids[kind].add(UUID(value))
        except (ValueError, TypeError):
            continue
    person_ids = ids["Client"].copy()
    case_ids = ids["Case"].copy()
    for model in DYNAMIC_DATA_MODELS:
        records = model.objects.for_facility(facility).filter(pk__in=ids[model.__name__])
        client_lookup = dynamic_client_lookup(model)
        case_lookup = dynamic_case_lookup(model)
        if client_lookup:
            person_ids.update(records.values_list(client_lookup, flat=True))
        if case_lookup:
            case_ids.update(records.values_list(case_lookup, flat=True))
    list(Client.objects.for_facility(facility).filter(pk__in=person_ids).order_by("pk").select_for_update())
    for model in DYNAMIC_DATA_MODELS:
        if model is Client:
            continue
        condition = Q(pk__in=case_ids if model is Case else ids[model.__name__])
        client_lookup = dynamic_client_lookup(model)
        case_lookup = dynamic_case_lookup(model)
        if client_lookup:
            condition |= Q(**{f"{client_lookup}__in": ids["Client"]})
        if case_lookup:
            condition |= Q(**{f"{case_lookup}__in": ids["Case"]})
        list(model.objects.for_facility(facility).filter(condition).order_by("pk").select_for_update(of=("self",)))


class AuditPruneIntegrityError(Exception):
    """A row the prune would delete or certify does not authenticate."""

    def __init__(self, row_id, reason: str):
        super().__init__(reason)
        self.row_id = row_id
        self.reason = reason


_AUTHENTICATE_CHUNK_SIZE = 2000


def _check_expired_links(facility, cutoff, candidates, boundary_hashes):
    """Lock, authenticate and link-check the expired rows in one pass.

    Refs #2717 (finding no. 9): a row authenticates on its own stored
    ``prev_hash``; that alone does not show that its predecessor still
    exists. If a row (even a protected one) was deleted past the trigger,
    its successor keeps a dangling ``prev_hash`` -- the only trace of the
    deletion. Pruning that successor would remove the trace and sign it as a
    boundary. Every row before the cutoff -- a prefix of the chain order --
    must therefore carry an ``entry_hash`` that its stored fields reproduce
    (the deletion criteria are hashed fields an owner-level role can rewrite
    past the trigger) and link to the row before it (chain start: ``""``) or
    to a boundary of an authenticated checkpoint, the same rule
    ``verify_chain`` applies.

    ``FOR UPDATE`` covers the checkpoints and every expired row, not only the
    candidates: a protected row deleted concurrently cannot vanish between
    this check and the deletion. It does not stop an INSERT, so the same
    statement marks the deletion candidates and only the candidates it has
    checked are returned for deletion; a row that joins the expired rows
    later is neither deleted nor signed as a boundary (a late deletion
    candidate makes the caller abort the prune).

    Every boundary hash must belong to one of the returned rows -- a boundary
    from a row outside this set would legitimise a gap nobody authenticated.

    Returns ``(candidate_pks, newest_hash)`` in chain order; raises
    :class:`AuditPruneIntegrityError` at the first row that fails.
    """
    chain = AuditLog.objects.filter(facility=facility)
    checkpoints = chain.filter(action=AuditLog.Action.AUDIT_PRUNE_CHECKPOINT).select_for_update(of=("self",))
    covered = _checkpoint_boundaries(checkpoints.order_by("timestamp", "id"))
    rows = (
        chain.filter(timestamp__lt=cutoff)
        .annotate(prunable=Exists(candidates.filter(pk=OuterRef("pk"))))
        .select_for_update(of=("self",))
        .order_by("timestamp", "id")
    )
    pending = set(boundary_hashes)
    pks = []
    newest = ""
    previous = None
    for row in rows.iterator(chunk_size=_AUTHENTICATE_CHUNK_SIZE):
        if not row.entry_hash:
            raise AuditPruneIntegrityError(row.pk, "row without entry_hash")
        if not hmac.compare_digest(compute_entry_hash(row, row.prev_hash or ""), row.entry_hash):
            raise AuditPruneIntegrityError(row.pk, "entry_hash mismatch")
        link = row.prev_hash or ""
        linked = link == previous if previous is not None else link == ""
        if not linked and link not in covered:
            raise AuditPruneIntegrityError(row.pk, "prev_hash linkage break")
        previous = row.entry_hash
        if row.prunable:
            pks.append(row.pk)
            newest = row.entry_hash
            pending.discard(row.entry_hash)
    if pending:
        raise AuditPruneIntegrityError(None, "boundary outside the authenticated rows")
    return pks, newest


def _delete_checked(candidates, pks):
    """Delete exactly the checked candidates, in chunks; returns the count."""
    deleted = 0
    for start in range(0, len(pks), _AUTHENTICATE_CHUNK_SIZE):
        chunk_deleted, _ = candidates.filter(pk__in=pks[start : start + _AUTHENTICATE_CHUNK_SIZE]).delete()
        deleted += chunk_deleted
    return deleted


def prune_auditlog(facility, settings_obj, now=None, dry_run=False):
    """Prune expired unheld rows, preserving authenticated chain boundaries.

    A zero period disables cleanup. The checkpoint and deletion commit together;
    every survivor, including held rows, contributes its predecessor boundary.

    Only rows that authenticate are deleted (Refs #2717, finding no. 9). The
    first row that does not aborts the facility's prune as a whole: nothing is
    deleted, no checkpoint is written and the break is logged with the row id
    only, so the chain check keeps reporting it. The same holds for a row that
    does not link to its predecessor: a row missing from the expired part of
    the chain is never pruned away together with its successor.
    """
    try:
        return _prune_auditlog(facility, settings_obj, now=now, dry_run=dry_run)
    except AuditPruneIntegrityError as exc:
        logger.error(
            "prune_auditlog: facility %s not pruned, row %s: %s",
            getattr(facility, "pk", None) or "system",
            exc.row_id,
            exc.reason,
        )
        return {"count": 0, "integrity_break": str(exc.row_id) if exc.row_id else exc.reason}


@transaction.atomic
def _prune_auditlog(facility, settings_obj, now=None, dry_run=False):
    if facility is not None and not dry_run:
        # Read the period in force before target locks, and keep it stable
        # through deletion and checkpoint: gate, Settings, targets (Refs #2716).
        lock_dynamic_protection(facility.pk)
        settings_obj = Settings.objects.select_for_update().filter(facility=facility).first() or settings_obj
    months = getattr(settings_obj, "auditlog_retention_months", 0) or 0
    if months <= 0:
        return {"count": 0}
    now = now or timezone.now()
    cutoff = _months_before(now, months)
    qs = AuditLog.objects.filter(facility=facility, timestamp__lt=cutoff).exclude(PRUNE_EXEMPT_Q)
    if facility is not None:
        if not dry_run:
            _lock_audit_targets(qs, facility)
        qs = qs.exclude(_held_targets(facility))
    if not dry_run and connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", [advisory_lock_name(getattr(facility, "pk", None))]
            )
    count = qs.count()
    if count == 0 or dry_run:
        return {"count": count}
    # Refs #2717 (finding no. 17): the database intersects survivor
    # predecessors with deleted hashes; only the boundaries themselves reach
    # Python, not every hash of the chain.
    boundary_hashes = sorted(
        set(
            AuditLog.objects.filter(facility=facility, prev_hash__in=qs.exclude(entry_hash="").values("entry_hash"))
            .exclude(pk__in=qs.values("pk"))
            .values_list("prev_hash", flat=True)
        )
    )
    pks, newest_boundary = _check_expired_links(facility, cutoff, qs, boundary_hashes)
    count = len(pks)
    if count == 0:
        return {"count": 0}
    if connection.vendor == "postgresql":
        with bypass_replication_triggers():
            deleted = _delete_checked(qs, pks)
    else:
        deleted = _delete_checked(qs, pks)
    # The deletion is bound to the checked primary keys; those rows are
    # locked and cannot leave the candidate set, so any other count is a
    # break. A candidate left after the deletion joined after the link check
    # (the locks do not block an INSERT); the prune aborts rather than sign a
    # boundary next to an unchecked row. Both roll back with the error.
    if deleted != count or qs.exists():
        raise AuditPruneIntegrityError(None, "candidate set changed during the prune")
    audit_event(
        AuditLog.Action.AUDIT_PRUNE_CHECKPOINT,
        user=None,
        facility=facility,
        target_type="AuditLog",
        # ``cutoff`` lets ``verify_chain`` accept a pruned anchored tail only if
        # the anchor file dates it before this cutoff (Refs #2717, finding no. 9).
        detail={
            "pruned_count": count,
            "boundary_hash": newest_boundary,
            "boundary_hashes": boundary_hashes,
            "cutoff": cutoff.isoformat(),
        },
    )
    return {"count": count}


def prune_system_auditlog(now=None, dry_run=False):
    """Apply the installation period to the facility-less chain only."""
    period = SimpleNamespace(auditlog_retention_months=settings.AUDITLOG_SYSTEM_RETENTION_MONTHS)
    return prune_auditlog(None, period, now=now, dry_run=dry_run)
