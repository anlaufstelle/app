"""Service layer for WorkItem-related business logic."""

import calendar
import logging
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from core.models import Activity, AuditLog, WorkItem
from core.services.audit import audit_event
from core.services.dashboard import log_activity

logger = logging.getLogger(__name__)


def _add_months(source: date, months: int) -> date:
    """Add ``months`` to ``source`` using calendar-aware month arithmetic.

    Behaviour matches ``dateutil.relativedelta(months=+n)``:
    the day is clamped to the last valid day of the target month
    (e.g. 31.01. + 1 month = 28./29.02.).
    """
    month_index = source.month - 1 + months
    target_year = source.year + month_index // 12
    target_month = month_index % 12 + 1
    last_day = calendar.monthrange(target_year, target_month)[1]
    target_day = min(source.day, last_day)
    return date(target_year, target_month, target_day)


def _next_due_date(current: date, recurrence: str) -> date | None:
    """Compute the next due date for a recurrence pattern.

    Returns ``None`` when no duplicate should be scheduled.
    """
    if recurrence == WorkItem.Recurrence.WEEKLY:
        return current + timedelta(days=7)
    if recurrence == WorkItem.Recurrence.MONTHLY:
        return _add_months(current, 1)
    if recurrence == WorkItem.Recurrence.QUARTERLY:
        return _add_months(current, 3)
    if recurrence == WorkItem.Recurrence.YEARLY:
        return _add_months(current, 12)
    return None


def _locked_client_id(workitem):
    """Frischer ``client_id``-Stand der Zeile **unter Row-Lock** (Refs #1786).

    Muster von ``changed_fields_vs_db``: ``_base_manager`` (kein Custom-Manager
    verfaelscht den Low-Level-Lookup), ``values_list`` (kein Nachladen des
    verwandten Objekts) und ``select_for_update`` in der Transaktionsklammer des
    Callers — der Lock haelt bis zum Commit, der spaetere Diff-Read greift also
    auf dieselbe gesperrte Zeile.

    ``None`` heisst „keine Person zugeordnet"; eine nicht (mehr) vorhandene Zeile
    liefert dasselbe. Das ist hier fail-safe genug: ``update_workitem`` arbeitet
    ausschliesslich auf persistierten Aufgaben, und das anschliessende ``save()``
    wuerde eine fehlende Zeile ohnehin neu schreiben.
    """
    return (
        type(workitem)
        ._base_manager.select_for_update()
        .filter(pk=workitem.pk)
        .values_list("client_id", flat=True)
        .first()
    )


def _log_workitem_update(workitem, user, changed_fields):
    """Write a single AuditLog entry for a WorkItem bulk field update."""
    audit_event(
        AuditLog.Action.WORKITEM_UPDATE,
        user=user,
        facility=workitem.facility,
        target_obj=workitem,
        detail={"changed_fields": list(changed_fields), "bulk": True},
    )


def _apply_status_transition(workitem, new_status, user, *, auto_assign: bool) -> bool:
    """Mutate ``workitem`` fields for a status transition.

    Refs #906: Gemeinsame Kern-Logik fuer Single- (``update_workitem_status``)
    und Bulk-Pfad (``bulk_update_workitem_status``). Verantwortlich nur fuer:
    Statuswechsel, optionales Auto-Assign, ``completed_at``-Pflege. **Kein**
    ``save()`` — der Caller entscheidet ueber ``update_fields`` und
    ``select_for_update``-Kontext.

    - ``auto_assign=True`` (Single-Pfad): IN_PROGRESS ohne Assignee → User
      wird zugewiesen. ``auto_assign=False`` (Bulk-Pfad): keine Zuweisung.
    - DONE/DISMISSED setzt ``completed_at = now``.
    - Jede andere Transition setzt ``completed_at = None`` (Reopen).

    Returns ``True``, wenn der Status sich tatsaechlich aendert. ``False``
    fuer den Idempotenz-Guard (gleicher Status; Caller kann frueh
    zurueckkehren ohne ``save()``).
    """
    if workitem.status == new_status:
        return False
    workitem.status = new_status
    if auto_assign and new_status == WorkItem.Status.IN_PROGRESS and not workitem.assigned_to:
        workitem.assigned_to = user
    if new_status in (WorkItem.Status.DONE, WorkItem.Status.DISMISSED):
        workitem.completed_at = timezone.now()
    elif workitem.completed_at:
        workitem.completed_at = None
    return True


def _maybe_duplicate_recurring(workitem, user, new_status) -> None:
    """Falls Statuswechsel auf DONE + Recurrence != NONE: Folgeaufgabe anlegen.

    Refs #906: Beide Pfade (Single + Bulk) brauchen dieselbe Bedingung —
    Idempotenz wird in ``duplicate_recurring_workitem`` selbst durchgesetzt
    (``recurrence_duplicated_at``-Marker, Refs #596).
    """
    if new_status != WorkItem.Status.DONE:
        return
    if not workitem.recurrence or workitem.recurrence == WorkItem.Recurrence.NONE:
        return
    duplicate_recurring_workitem(workitem, user)


def _locked_workitems(workitems):
    """Refs #1022 (B2): Items innerhalb der Transaktion per
    ``select_for_update`` neu laden+locken — deterministisch nach ``pk``
    geordnet zur Deadlock-Vermeidung —, damit die Bulk-Pfade auf frischem
    DB-Stand operieren statt auf den (potenziell veralteten) aus der View
    geladenen Instanzen. Spiegelt den Single-Pfad ``update_workitem_status``
    (Refs #129/#733). Facility-/Tenant-Isolation greift ueber RLS (GUC im
    Request-Kontext) und die View-Vorfilterung.
    """
    pks = [w.pk for w in workitems]
    return WorkItem.objects.select_for_update().filter(pk__in=pks).order_by("pk")


@transaction.atomic
def create_workitem(facility, user, *, client=None, **data):
    """Create a work item with activity and audit logging."""
    workitem = WorkItem(facility=facility, created_by=user, client=client, **data)
    workitem.save()
    log_activity(
        facility=facility,
        actor=user,
        verb=Activity.Verb.CREATED,
        target=workitem,
        summary=f"Aufgabe: {workitem.title}",
    )
    audit_event(
        AuditLog.Action.WORKITEM_CREATE,
        user=user,
        facility=facility,
        target_obj=workitem,
    )
    return workitem


@transaction.atomic
def update_workitem(workitem, user, *, expected_updated_at=None, require_version_token=False, **fields):
    """Update a work item with activity logging and audit logging.

    Accepts allowed fields (item_type, title, description, priority, due_date,
    remind_at, recurrence, assigned_to, client) and persists them — die Liste im
    Code ist die Wahrheit, dieser Satz ihr Spiegel. Writes an AuditLog entry with
    the names of changed fields (no PII values).

    ``expected_updated_at`` enables optimistic locking (Refs #531) — when the
    DB-side ``updated_at`` differs, a ``ValidationError`` is raised.

    ``require_version_token`` (Refs #1338, #1351): wird 1:1 an
    ``check_version_conflict`` durchgereicht. ``WorkItemUpdateView`` setzt es
    auf das Ergebnis von ``_wants_json_response`` — JSON-/Offline-Replay-
    Clients müssen dadurch einen Token mitschicken, während der klassische
    HTML-Formular-Pfad beim Default ``False`` bleibt (kein Verhaltensbruch).

    Ursprungs-Pinning (Refs #1786, Maintainer-Entscheid 2026-08-05): Anders
    als beim Fall (dort strikt) bleibt bei der Aufgabe das nachträgliche
    Zuordnen erlaubt — eine personenlose Aufgabe darf einer Person zugeordnet
    werden. Verboten sind Wechsel und Entfernen: ``title``/``description``
    sind Freitext und es gibt keinen nachgelagerten Prune, ein Umhängen
    schriebe also fremden Klartext an eine andere Person fest.
    """
    from core.services.audit.diff import changed_fields_vs_db
    from core.services.security import check_version_conflict

    check_version_conflict(workitem, expected_updated_at, require_token=require_version_token)
    # Allowlist (Refs #734-Muster, Refs #1786): bis hierher war
    # ``update_workitem`` eine von ZWEI der vier Update-Services ohne Allowlist —
    # jedes durchgereichte Feld landete blind im ``setattr``. Die verbliebene
    # Luecke ist ``update_event`` (dokumentierter Restpunkt in
    # ``docs/security-notes.md``, bewusst nicht in #1786 mitbehoben: fremder
    # Pfad, eigener Testbedarf). Menge = die Kwargs von
    # ``WorkItemUpdateView.post``. Läuft ZUERST, unabhängig vom Diff.
    allowed = {
        "item_type",
        "title",
        "description",
        "priority",
        "due_date",
        "assigned_to",
        "client",
        "remind_at",
        "recurrence",
    }
    for key in fields:
        if key not in allowed:
            raise ValueError(f"Feld '{key}' darf nicht aktualisiert werden.")

    if "client" in fields:
        # Refs #1786 (TOCTOU): Der Pin-Zustand wird gegen den **DB-Stand unter
        # Row-Lock** entschieden, nicht gegen ``workitem.client_id``. Die
        # Instanz kann in zwei Richtungen falsch liegen: veraltet (ein
        # parallelter Attach-POST hat die Person inzwischen gesetzt — beide
        # Requests lesen sonst „noch personenlos" und der zweite ueberschreibt
        # den ersten) oder bereits von ``_post_clean()`` mutiert (Muster #1613).
        # Der Lock haelt bis zum Commit dieser Transaktion, also auch ueber den
        # Diff-Read und das ``save()`` unten.
        db_client_id = _locked_client_id(workitem)
        new_client = fields["client"]
        new_pk = getattr(new_client, "pk", new_client)
        if db_client_id is not None:
            if new_pk != db_client_id:
                raise ValueError("Personenzuordnung einer Aufgabe kann nicht geändert oder entfernt werden.")
            # No-op: identischer Wert. Raus aus ``fields``, damit weder ein
            # leerer Audit-Diff noch ein unnötiger Schreibzugriff entsteht —
            # und der DB-Stand auf die Instanz, weil das volle ``save()`` unten
            # sonst die Zuordnung einer stale (personenlosen) Instanz
            # zurueckschriebe. Das Wegwerfen des Kwargs darf nie ein Nullen
            # bedeuten.
            fields.pop("client")
            workitem.client_id = db_client_id
    # Refs #1613: Diff gegen den DB-Stand, NICHT gegen die übergebene Instanz.
    # ``WorkItemForm(instance=workitem)`` hat die Instanz in ``_post_clean()``
    # bereits mutiert — ein ``getattr(workitem, key) != value`` sähe hier
    # ``neu == neu`` und schriebe nie ``WORKITEM_UPDATE`` (TB6). Der Helper
    # liest die noch unveränderte DB-Zeile (FK-Vergleich über ``attname``).
    changed_fields = changed_fields_vs_db(workitem, fields)
    for key, value in fields.items():
        setattr(workitem, key, value)
    workitem.save()

    if changed_fields:
        audit_event(
            AuditLog.Action.WORKITEM_UPDATE,
            user=user,
            facility=workitem.facility,
            target_obj=workitem,
            detail={"changed_fields": changed_fields},
        )

    log_activity(
        facility=workitem.facility,
        actor=user,
        verb=Activity.Verb.UPDATED,
        target=workitem,
        summary=f"Aufgabe aktualisiert: {workitem.title}",
    )

    return workitem


@transaction.atomic
def update_workitem_status(workitem, new_status, user, *, expected_updated_at=None, require_version_token=False):
    """Perform a status transition including auto-assign and completed_at management.

    - On IN_PROGRESS: auto-assign to the user if not yet assigned.
    - On DONE/DISMISSED: set completed_at.
    - On back-transition: reset completed_at.
    - On DONE with ``recurrence != NONE``: auto-duplicate the work item
      with the next due date (Refs #266).

    Concurrency (Refs #129 Teil A, Refs #733): Das WorkItem wird unter
    ``select_for_update()`` neu geladen, damit zwei zeitgleiche Klicks
    nicht denselben ``old_status`` lesen und beide einen Activity-/
    Recurrence-Folgeeintrag erzeugen. Wenn ``new_status == aktueller
    Status`` ist (Idempotenz-Guard), kehrt die Funktion sofort ohne
    ``save()`` und ohne Activity-Log zurueck.

    ``expected_updated_at``/``require_version_token`` (Refs #1419, analog
    ``update_workitem``): Optimistic-Lock-Token fuer Offline-Replay-Clients.
    Der Check laeuft auf der frisch GELOCKTEN Instanz innerhalb derselben
    Transaktion (ADR-030-Invariante) — die View setzt die Pflicht nur fuer
    Raw-JSON-Clients (``_wants_raw_json_response``), der normale HTMX-/
    HTML-Pfad bleibt beim bisherigen Status-gegen-Status-LWW (Default
    ``None``/``False``, kein Verhaltensbruch).

    Refs #906: Status-/completed_at-/auto-assign-/Recurrence-Logik teilt
    sich mit dem Bulk-Pfad ueber ``_apply_status_transition`` und
    ``_maybe_duplicate_recurring``.
    """
    from core.services.security import check_version_conflict

    # Innerhalb der Transaktion neu laden + locken — Facility-Filter
    # explizit, damit RLS- und Tenant-Isolation greifen.
    locked = WorkItem.objects.select_for_update().get(pk=workitem.pk, facility_id=workitem.facility_id)
    check_version_conflict(locked, expected_updated_at, require_token=require_version_token)
    old_status = locked.status

    if not _apply_status_transition(locked, new_status, user, auto_assign=True):
        return locked

    locked.save()

    # #1467: Unveränderliche AuditLog-Spur für JEDEN Statuswechsel — auf dem
    # gemeinsamen Post-Save-Pfad (nicht in den status-spezifischen
    # log_activity-Zweigen), damit auch IN_PROGRESS und DISMISSED erfasst
    # werden. Analog Bulk-Pfad (_log_workitem_update); erfüllt Threat-Model TB6
    # (threat-model.md:150). Die Activity bleibt zusätzlich als mutabler Feed.
    audit_event(
        AuditLog.Action.WORKITEM_UPDATE,
        user=user,
        facility=locked.facility,
        target_obj=locked,
        detail={"changed_fields": ["status"], "from": old_status, "to": new_status},
    )

    if new_status == WorkItem.Status.DONE:
        log_activity(
            facility=locked.facility,
            actor=user,
            verb=Activity.Verb.COMPLETED,
            target=locked,
            summary=f"Aufgabe erledigt: {locked.title}",
        )
    elif new_status == WorkItem.Status.OPEN and old_status == WorkItem.Status.DONE:
        log_activity(
            facility=locked.facility,
            actor=user,
            verb=Activity.Verb.REOPENED,
            target=locked,
            summary=f"Aufgabe wiedereröffnet: {locked.title}",
        )

    _maybe_duplicate_recurring(locked, user, new_status)
    return locked


@transaction.atomic
def duplicate_recurring_workitem(workitem, user):
    """Create a follow-up WorkItem for a recurring task (Refs #266, #596).

    Copies relevant fields (title, description, priority, assigned_to, client,
    item_type, facility, recurrence, remind_at) and bumps ``due_date`` by the
    recurrence interval. If the source has no ``due_date`` the interval is
    applied to today so the new item still has a valid deadline.

    Idempotency (Refs #596): If the source WorkItem already carries a
    ``recurrence_duplicated_at`` marker, the follow-up has already been
    generated during an earlier DONE transition — the call is a no-op and
    returns ``None``. This prevents duplicate follow-ups on Done→Open→Done
    toggles and on bulk DONE paths that re-process items.

    Returns the newly created WorkItem, or ``None`` if ``recurrence`` is NONE
    or unknown, or if the source is already marked as duplicated.
    """
    if not workitem.recurrence or workitem.recurrence == WorkItem.Recurrence.NONE:
        return None

    # Refs #596: Idempotency guard — skip if a follow-up was already produced.
    if workitem.recurrence_duplicated_at is not None:
        return None

    reference_date = workitem.due_date or timezone.localdate()
    new_due_date = _next_due_date(reference_date, workitem.recurrence)
    if new_due_date is None:
        return None

    new_remind_at = None
    if workitem.remind_at and workitem.due_date:
        # Preserve the reminder offset relative to the due date.
        offset = workitem.due_date - workitem.remind_at
        new_remind_at = new_due_date - offset

    new_workitem = WorkItem.objects.create(
        facility=workitem.facility,
        created_by=user,
        client=workitem.client,
        assigned_to=workitem.assigned_to,
        item_type=workitem.item_type,
        title=workitem.title,
        description=workitem.description,
        priority=workitem.priority,
        due_date=new_due_date,
        remind_at=new_remind_at,
        recurrence=workitem.recurrence,
        status=WorkItem.Status.OPEN,
    )
    log_activity(
        facility=workitem.facility,
        actor=user,
        verb=Activity.Verb.CREATED,
        target=new_workitem,
        summary=f"Wiederkehrende Folgeaufgabe: {new_workitem.title}",
    )
    # Refs #596: Mark the source so a subsequent DONE transition does not
    # duplicate again. Persist within the same atomic block.
    workitem.recurrence_duplicated_at = timezone.now()
    workitem.save(update_fields=["recurrence_duplicated_at"])
    return new_workitem


@transaction.atomic
def bulk_update_workitem_status(workitems, user, status):
    """Bulk-update the status of multiple WorkItems (Refs #267, #593).

    Writes one AuditLog entry per changed WorkItem and respects completed_at
    semantics (set on DONE/DISMISSED, cleared otherwise).

    On bulk DONE transitions (Refs #593): for items with a non-NONE recurrence,
    a follow-up WorkItem is generated via ``duplicate_recurring_workitem`` —
    analogous to the single-update path in ``update_workitem_status``. The
    duplicate call is guarded by the ``recurrence_duplicated_at`` marker
    (Refs #596), so re-processing the same batch stays idempotent.

    Returns the number of processed items.
    """
    valid_statuses = {s.value for s in WorkItem.Status}
    if status not in valid_statuses:
        raise ValueError(f"Invalid status: {status}")

    count = 0
    for workitem in _locked_workitems(workitems):
        # Refs #906: gemeinsame Transition-Logik mit dem Single-Pfad. Bulk
        # uebernimmt kein Auto-Assign (Designentscheidung, Refs #593).
        if not _apply_status_transition(workitem, status, user, auto_assign=False):
            continue
        workitem.save(update_fields=["status", "completed_at", "updated_at"])
        _log_workitem_update(workitem, user, ["status"])
        # Refs #593 / #596: Recurrence-Duplikation auch im Bulk-Pfad,
        # Idempotenz via ``recurrence_duplicated_at``-Marker in
        # ``duplicate_recurring_workitem``.
        _maybe_duplicate_recurring(workitem, user, status)
        count += 1
    return count


@transaction.atomic
def bulk_update_workitem_priority(workitems, user, priority):
    """Bulk-update the priority of multiple WorkItems (Refs #267).

    Returns the number of processed items.
    """
    valid_priorities = {p.value for p in WorkItem.Priority}
    if priority not in valid_priorities:
        raise ValueError(f"Invalid priority: {priority}")

    count = 0
    for workitem in _locked_workitems(workitems):
        if workitem.priority == priority:
            continue
        workitem.priority = priority
        workitem.save(update_fields=["priority", "updated_at"])
        _log_workitem_update(workitem, user, ["priority"])
        count += 1
    return count


@transaction.atomic
def bulk_assign_workitems(workitems, user, assignee_or_none):
    """Bulk-assign WorkItems to a user (or clear the assignment).

    ``assignee_or_none=None`` removes the assignment. Returns the processed count.
    """
    count = 0
    for workitem in _locked_workitems(workitems):
        if workitem.assigned_to_id == (assignee_or_none.pk if assignee_or_none else None):
            continue
        workitem.assigned_to = assignee_or_none
        workitem.save(update_fields=["assigned_to", "updated_at"])
        _log_workitem_update(workitem, user, ["assigned_to"])
        count += 1
    return count
