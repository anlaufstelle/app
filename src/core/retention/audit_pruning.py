"""AuditLog-Pruning fuer den Retention-Pfad (#744).

Aus ``services/retention.py`` ausgekoppelt — semantisch eigenes Thema
(append-only-Trigger, separate Aufbewahrungsfrist).
"""

import calendar
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from core.models import AuditLog, Case, Client, Event
from core.retention.legal_holds import (
    exclude_cases_with_active_holds,
    exclude_clients_with_active_holds,
    get_active_hold_target_ids,
)
from core.services.audit import audit_event
from core.services.audit.chain import advisory_lock_name
from core.services.dashboard.release_log import RELEASE_EXPORT_Q
from core.services.system import bypass_replication_triggers

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
    return (
        Q(target_type="Client", target_id__in=[str(pk) for pk in protected_clients])
        | Q(target_type="Case", target_id__in=[str(pk) for pk in protected_cases])
        | Q(target_type="Event", target_id__in=[str(pk) for pk in protected_events])
    )


def _lock_audit_targets(qs, facility):
    """Take the hold anchors before the audit chain, in person/case/event order.

    Hold creation uses the same anchors. Unknown/non-record targets have no
    anchor; direct holds on missing records are still respected by the filter.
    """
    ids = {kind: set() for kind in ("Client", "Case", "Event")}
    for kind, value in qs.filter(target_type__in=ids).values_list("target_type", "target_id"):
        try:
            ids[kind].add(UUID(value))
        except (ValueError, TypeError):
            continue
    events = Event.objects.for_facility(facility).filter(pk__in=ids["Event"])
    cases = Case.objects.for_facility(facility).filter(pk__in=ids["Case"])
    person_ids = (
        ids["Client"] | set(events.values_list("client_id", flat=True)) | set(cases.values_list("client_id", flat=True))
    )
    list(Client.objects.for_facility(facility).filter(pk__in=person_ids).order_by("pk").select_for_update())
    case_ids = ids["Case"] | set(events.values_list("case_id", flat=True))
    list(
        Case.objects.for_facility(facility)
        .filter(Q(pk__in=case_ids) | Q(client_id__in=ids["Client"]))
        .order_by("pk")
        .select_for_update()
    )
    list(
        Event.objects.for_facility(facility)
        .filter(Q(pk__in=ids["Event"]) | Q(client_id__in=ids["Client"]) | Q(case_id__in=ids["Case"]))
        .order_by("pk")
        .select_for_update()
    )


@transaction.atomic
def prune_auditlog(facility, settings_obj, now=None, dry_run=False):
    """Prune expired unheld rows, preserving authenticated chain boundaries.

    A zero period disables cleanup. The checkpoint and deletion commit together;
    every survivor, including held rows, contributes its predecessor boundary.
    """
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
    deleted_hashes = {value for value in qs.values_list("entry_hash", flat=True) if value}
    newest_boundary = qs.order_by("-timestamp", "-id").values_list("entry_hash", flat=True).first() or ""
    survivor_prevs = set(
        AuditLog.objects.filter(facility=facility).exclude(pk__in=qs.values("pk")).values_list("prev_hash", flat=True)
    )
    boundary_hashes = sorted(survivor_prevs & deleted_hashes)
    if connection.vendor == "postgresql":
        with bypass_replication_triggers():
            qs.delete()
    else:
        qs.delete()
    audit_event(
        AuditLog.Action.AUDIT_PRUNE_CHECKPOINT,
        user=None,
        facility=facility,
        target_type="AuditLog",
        detail={"pruned_count": count, "boundary_hash": newest_boundary, "boundary_hashes": boundary_hashes},
    )
    return {"count": count}


def prune_system_auditlog(now=None, dry_run=False):
    """Apply the installation period to the facility-less chain only."""
    period = SimpleNamespace(auditlog_retention_months=settings.AUDITLOG_SYSTEM_RETENTION_MONTHS)
    return prune_auditlog(None, period, now=now, dry_run=dry_run)
