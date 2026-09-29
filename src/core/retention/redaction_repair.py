"""Repair events, persons and cases whose redaction a key rotation undid (Refs #2188, related #2177).

**The damage.** Until v0.95.0 the key rotation wrote ``data_json`` of events,
persons and cases back without a row lock and without comparing it with the
value it had read (the row lock plus compare-and-swap came with #2131 in
v0.96.0). When a redaction emptied a row while a rotation was re-encrypting
it, the rotation replaced the empty payload with a freshly encrypted copy of
the old values. The row kept its "done" state, and every current run selects
rows by that state, not by their content:

* **event** — soft-deleted (``is_deleted``) by the manual delete or the
  retention run, or a live event of an anonymised person (the anonymisation
  empties those as well). Retention filters ``is_deleted=False``, the
  anonymisation never runs twice.
* **person** — ``anonymized_at`` set; the nightly anonymisation filters
  ``anonymized_at IS NULL``.
* **case** — ``anonymized_at`` set; the case anonymisation filters the same.

**Detection** checks the content: a row is damaged when it is in its done
state and ``data_json`` still carries values. The database pre-filters on
``data_json <> '{}'``; the final word is a Python truth test, so the filter is
never narrower than the check.

**Repair** writes exactly what the regular redaction writes into the one
column the rotation touched, through the same ``save(update_fields=...)``:
``data_json = {}``, for an event together with ``search_text``, which the
``pre_save`` signal computes from the empty payload. ``deleted_at`` and
``anonymized_at`` stay as they are — they date the first erasure — and so does
``updated_at``. Each step follows the lock discipline of the redaction path it
repairs (person -> case -> event, then the hold check), re-reads the row under
the lock and repairs it only if it is still damaged (compare-and-swap), and
writes one audit entry per repaired row after all locks of the step: action
``DELETE``, as the retention run uses for these tables, with its own category.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable

from django.db import OperationalError, transaction
from django.db.models import Q

from core.models import AuditLog, Case, Client, Event
from core.retention.eventhistory import (
    BATCH_SIZE,
    MAX_ATTEMPTS,
    RETRY_BACKOFF_SECONDS,
    OwnerContextMissingError,
    _held_event_ids_unlocked,
    _is_deadlock,
    lock_and_partition_events,
)
from core.retention.eventhistory_repair import RepairResult
from core.retention.legal_holds import (
    case_has_active_hold,
    client_has_active_hold,
    exclude_cases_with_active_holds,
    exclude_clients_with_active_holds,
)
from core.services.audit import audit_retention_decision
from core.services.case.deletion import _lock_case_chain
from core.services.client.main import _lock_client_case_rows, _lock_client_event_rows
from core.services.system import current_connection_owns, has_rls_bypass_context

COMMAND_NAME = "repair_eventhistory_redaction"

EVENT_TABLE = "core_event"
CLIENT_TABLE = "core_client"
CASE_TABLE = "core_case"

CATEGORY_EVENT_REPAIRED = "event_repaired"
CATEGORY_CLIENT_REPAIRED = "client_repaired"
CATEGORY_CASE_REPAIRED = "case_repaired"

#: An event is done when it is soft-deleted or belongs to an anonymised person.
EVENT_DONE = Q(is_deleted=True) | Q(client__anonymized_at__isnull=False)

_REPAIRED, _DEFERRED, _SKIPPED = "repaired", "deferred", "skipped"


def _require_repair_context(table: str) -> None:
    """Refuse unless the connection bypasses RLS and owns *table* — before any read.

    Without a bypass the detection would see no row of other facilities and
    report a clean stock. A bypass alone is not the maintenance role either:
    any role may set ``app.is_super_admin``, and a BYPASSRLS role need not own
    the table.
    """
    if not has_rls_bypass_context():
        raise OwnerContextMissingError(
            f"Repairing {table} runs without an RLS bypass context (neither a "
            "SUPERUSER/BYPASSRLS role nor the app.is_super_admin session variable). "
            "Aborted before reading any row — detection would otherwise see no row "
            "and falsely report clean data."
        )
    if not current_connection_owns(table):
        raise OwnerContextMissingError(
            f"Repairing {table} needs a connection that owns {table} (admin/owner "
            "database role); an RLS bypass via BYPASSRLS or the app.is_super_admin "
            "session variable is not enough. Aborted before reading any row."
        )


def _damaged_pks(queryset, batch_size):
    """Damaged primary keys page by page (keyset over ``pk``, no open cursor).

    Each page is read completely before the caller writes; continuing with
    ``pk > last`` skips repaired and deferred rows alike, so no page is read
    twice.
    """
    queryset = queryset.exclude(data_json={}).order_by("pk").values_list("pk", "data_json")
    last_pk = None
    while True:
        page = queryset if last_pk is None else queryset.filter(pk__gt=last_pk)
        rows = list(page[:batch_size])
        if not rows:
            return
        yield [pk for pk, data in rows if data]
        last_pk = rows[-1][0]


def _with_deadlock_retry(step: Callable, *args):
    """A deadlock hits the step, not the run (ADR-050 D5.5); other errors pass through."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return step(*args)
        except OperationalError as exc:
            if not _is_deadlock(exc) or attempt == MAX_ATTEMPTS:
                raise
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise AssertionError("unreachable")  # pragma: no cover


def _still_damaged(queryset):
    """Re-read under the step's locks: the rows that are still done and still carry values."""
    return [row for row in queryset.exclude(data_json={}).order_by("pk") if row.data_json]


def _empty_payload(row, *update_fields: str) -> None:
    """The redaction's own write: ``data_json`` emptied via ``save()`` (encryption pipeline)."""
    row.data_json = {}
    row.save(update_fields=["data_json", *update_fields])


def _audit(facility, target_type: str, category: str, pk) -> None:
    audit_retention_decision(
        facility,
        target_type=target_type,
        target_id=str(pk),
        action=AuditLog.Action.DELETE,
        category=category,
        command=COMMAND_NAME,
        count=1,
    )


# -- events -------------------------------------------------------------------


def _heal_event_batch(facility, pks) -> RepairResult:
    """One batch: person -> event locks and hold check (``lock_and_partition_events``),
    then compare-and-swap per event, then one audit entry per repaired event."""
    result = RepairResult()
    with transaction.atomic():
        eligible, held, _skipped = lock_and_partition_events(set(pks))
        result.deferred = sum(1 for pk in pks if pk in held)
        targets = _still_damaged(Event.objects.filter(pk__in=eligible).filter(EVENT_DONE)) if eligible else []
        for event in targets:
            # ``search_text`` as in every event redaction: the signal derives it from ``{}``.
            _empty_payload(event, "search_text")
        for event in targets:
            _audit(facility, "Event", CATEGORY_EVENT_REPAIRED, event.pk)
        result.repaired = len(targets)
        result.skipped = len(pks) - result.deferred - result.repaired
    return result


def repair_event_redaction(facility, *, dry_run=True, batch_size=BATCH_SIZE) -> RepairResult:
    """Find (and with ``dry_run=False`` repair) damaged events of *facility*."""
    _require_repair_context(EVENT_TABLE)
    total = RepairResult()
    candidates = Event.objects.filter(facility=facility).filter(EVENT_DONE)
    for pks in _damaged_pks(candidates, batch_size):
        if not pks:
            continue
        total.found += len(pks)
        if dry_run:
            # The same hold rule the batch applies under lock: event, person or case hold.
            held = _held_event_ids_unlocked(facility, set(pks))
            total.deferred += sum(1 for pk in pks if pk in held)
            continue
        total.merge(_with_deadlock_retry(_heal_event_batch, facility, pks))
    return total


# -- persons and cases ----------------------------------------------------------


def _heal_client(facility, pk) -> str:
    """Person -> cases -> events locked like the anonymisation, then hold, then swap."""
    with transaction.atomic():
        client = Client.objects.select_for_update().filter(pk=pk, facility=facility).first()
        if client is None:
            return _SKIPPED
        _lock_client_case_rows(client)
        _lock_client_event_rows(client)
        if client_has_active_hold(client):
            return _DEFERRED
        targets = _still_damaged(Client.objects.filter(pk=pk, anonymized_at__isnull=False))
        if not targets:
            return _SKIPPED
        _empty_payload(targets[0])
        _audit(facility, "Client", CATEGORY_CLIENT_REPAIRED, pk)
        return _REPAIRED


def _heal_case(facility, pk) -> str:
    """Person -> case -> its events locked like the case anonymisation, then hold, then swap."""
    with transaction.atomic():
        case = _lock_case_chain(facility.pk, pk)
        if case is None:
            return _SKIPPED
        if case_has_active_hold(case):
            return _DEFERRED
        targets = _still_damaged(Case.objects.filter(pk=pk, anonymized_at__isnull=False))
        if not targets:
            return _SKIPPED
        _empty_payload(targets[0])
        _audit(facility, "Case", CATEGORY_CASE_REPAIRED, pk)
        return _REPAIRED


def _repair_rows(facility, table, candidates, *, unheld, heal, dry_run, batch_size) -> RepairResult:
    """Shared loop for persons and cases: one transaction per row."""
    _require_repair_context(table)
    total = RepairResult()
    for pks in _damaged_pks(candidates, batch_size):
        if not pks:
            continue
        total.found += len(pks)
        if dry_run:
            free = set(unheld(pks))
            total.deferred += sum(1 for pk in pks if pk not in free)
            continue
        for pk in pks:
            outcome = _with_deadlock_retry(heal, facility, pk)
            setattr(total, outcome, getattr(total, outcome) + 1)
    return total


def repair_client_redaction(facility, *, dry_run=True, batch_size=BATCH_SIZE) -> RepairResult:
    """Find (and with ``dry_run=False`` repair) damaged persons of *facility*."""

    def unheld(pks: Iterable) -> Iterable:
        queryset = Client.objects.filter(pk__in=pks)
        return exclude_clients_with_active_holds(queryset, facility).values_list("pk", flat=True)

    return _repair_rows(
        facility,
        CLIENT_TABLE,
        Client.objects.filter(facility=facility, anonymized_at__isnull=False),
        unheld=unheld,
        heal=_heal_client,
        dry_run=dry_run,
        batch_size=batch_size,
    )


def repair_case_redaction(facility, *, dry_run=True, batch_size=BATCH_SIZE) -> RepairResult:
    """Find (and with ``dry_run=False`` repair) damaged cases of *facility*."""

    def unheld(pks: Iterable) -> Iterable:
        queryset = Case.objects.filter(pk__in=pks)
        return exclude_cases_with_active_holds(queryset, facility).values_list("pk", flat=True)

    return _repair_rows(
        facility,
        CASE_TABLE,
        Case.objects.filter(facility=facility, anonymized_at__isnull=False),
        unheld=unheld,
        heal=_heal_case,
        dry_run=dry_run,
        batch_size=batch_size,
    )


#: Repairs in the canonical lock order person -> case -> event.
REPAIRS = {
    "client": repair_client_redaction,
    "case": repair_case_redaction,
    "event": repair_event_redaction,
}
TABLES = {"client": CLIENT_TABLE, "case": CASE_TABLE, "event": EVENT_TABLE}
