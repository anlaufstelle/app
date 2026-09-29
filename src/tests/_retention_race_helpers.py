"""Refs #2374: shared helpers for the retention concurrency guards.

Everything here observes behaviour: row locks as PostgreSQL plans them
(``tests._row_lock_helpers``), a proposal's status as the own transaction sees
it, whether the own session holds the facility's audit-chain lock
(``pg_locks``), and whether a second session can lock a row right now
(``FOR UPDATE NOWAIT``). Nothing reads the wording of a statement.
"""

import threading
import time

from django.core.exceptions import ValidationError
from django.db import OperationalError, connection, connections, transaction

from core.models import RetentionProposal
from core.services.audit.chain import advisory_lock_name
from tests._row_lock_helpers import Abfrage, Mitschnitt

AUDIT_CHAIN = "audit-chain"
JOIN_TIMEOUT = 30
#: How long a parked session waits at most for the other one to block on a lock.
PARK_SECONDS = 10
WATCHED_TABLES = ("core_client", "core_case", "core_event", "core_retentionproposal", "core_legalhold")


def holds_audit_chain(facility_id):
    """Does the own session hold the audit-chain lock of this facility right now?"""
    name = advisory_lock_name(facility_id)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM pg_locks
                WHERE locktype = 'advisory' AND pid = pg_backend_pid() AND granted AND objsubid = 1
                  AND classid::text::bigint = (hashtext(%s)::bigint >> 32) & 4294967295
                  AND objid::text::bigint = hashtext(%s)::bigint & 4294967295
            )
            """,
            [name, name],
        )
        return cursor.fetchone()[0]


def backend_pid():
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        return cursor.fetchone()[0]


def waits_for_a_lock(pid):
    with connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid = %s AND NOT granted)", [pid])
        return cursor.fetchone()[0]


def park_until_the_other_session_waits(signal, other_session):
    """Give the signal, then wait until the other session blocks on a lock (at most ``PARK_SECONDS``)."""
    signal.set()
    deadline = time.monotonic() + PARK_SECONDS
    while time.monotonic() < deadline:
        pid = other_session.get("pid")
        if pid is not None and waits_for_a_lock(pid):
            return
        time.sleep(0.05)


def session_thread(name, work, results, errors, session=None):
    """``work`` on its own connection; a ``ValidationError`` is a result, anything else an error."""

    def _run():
        connection.close()
        try:
            if session is not None:
                session["pid"] = backend_pid()
            results[name] = work()
        except ValidationError as exc:
            results[name] = exc
        except Exception as exc:  # noqa: BLE001 — reported by the test, not swallowed
            errors[name] = exc
        finally:
            connections.close_all()

    return threading.Thread(target=_run, name=name)


def run_threads(threads):
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=JOIN_TIMEOUT)
    assert not any(t.is_alive() for t in threads), "the two sessions block each other"


def immediately_lockable(rows):
    """Labels of the rows a second session can lock right now (``FOR UPDATE NOWAIT``)."""
    free = []

    def _probe():
        try:
            for label, model, pk in rows:
                try:
                    with transaction.atomic():
                        list(model._base_manager.select_for_update(nowait=True).filter(pk=pk).values_list("pk"))
                except OperationalError:
                    continue
                free.append(label)
        finally:
            connections.close_all()

    thread = threading.Thread(target=_probe, name="second-session")
    thread.start()
    thread.join(timeout=JOIN_TIMEOUT)
    assert not thread.is_alive(), "the second session did not return"
    return free


class Timeline:
    """``execute_wrapper``: row locks, own proposal status changes and the first audit-chain hold, in order.

    ``proposals`` maps a label to a proposal pk. After every statement the
    wrapper records the watched tables the statement locked (``lock <table>``),
    every proposal whose status the own transaction now sees changed
    (``<label> -> <status>``, ``None`` once the row is gone) and, once, the
    moment the session first holds the audit chain (``audit-chain``).
    ``on_audit_chain`` runs at that moment.
    """

    def __init__(self, facility_id, proposals, on_audit_chain=None):
        self.facility_id = facility_id
        self.labels = {pk: label for label, pk in proposals.items()}
        self.on_audit_chain = on_audit_chain
        self.events = []
        self._quiet = False
        self._explainer = Mitschnitt()
        self._chain_seen = False
        self._statuses = self._read_statuses()

    def _read_statuses(self):
        return dict(RetentionProposal.objects.filter(pk__in=list(self.labels)).values_list("pk", "status"))

    def __call__(self, execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if self._quiet or many:
            return result
        self._quiet = True
        try:
            query = Abfrage(self._explainer, sql, params)
            self.events.extend(f"lock {table}" for table in WATCHED_TABLES if query.sperrt(table))
            statuses = self._read_statuses()
            for pk, label in self.labels.items():
                if statuses.get(pk) != self._statuses.get(pk):
                    self.events.append(f"{label} -> {statuses.get(pk)}")
            self._statuses = statuses
            if not self._chain_seen and holds_audit_chain(self.facility_id):
                self._chain_seen = True
                self.events.append(AUDIT_CHAIN)
                if self.on_audit_chain is not None:
                    self.on_audit_chain()
        finally:
            self._quiet = False
        return result

    def compact(self):
        """The events without immediate repetitions."""
        compacted = []
        for event in self.events:
            if not compacted or compacted[-1] != event:
                compacted.append(event)
        return compacted


def record_timeline(facility_id, proposals, action, on_audit_chain=None):
    """Runs ``action`` under a :class:`Timeline`; returns ``(timeline, result)``."""
    timeline = Timeline(facility_id, proposals, on_audit_chain=on_audit_chain)
    with connection.execute_wrapper(timeline):
        result = action()
    return timeline, result


class AfterOwnStatusChange:
    """``execute_wrapper``: runs ``action`` once, right after the own transaction changed the proposal's status."""

    def __init__(self, proposal_pk, action):
        self.proposal_pk = proposal_pk
        self.action = action
        self._quiet = False
        self._done = False
        self._initial = self._status()

    def _status(self):
        return RetentionProposal.objects.filter(pk=self.proposal_pk).values_list("status", flat=True).first()

    def __call__(self, execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if self._done or self._quiet:
            return result
        self._quiet = True
        try:
            if self._status() != self._initial:
                self._done = True
                self.action()
        finally:
            self._quiet = False
        return result


class AfterFirstRead:
    """``execute_wrapper``: runs ``action`` once, right after the first statement that reads ``table``."""

    def __init__(self, table, action):
        self.table = table
        self.action = action
        self.fired = False
        self._quiet = False
        self._explainer = Mitschnitt()

    def __call__(self, execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if self.fired or self._quiet or many:
            return result
        self._quiet = True
        try:
            if Abfrage(self._explainer, sql, params).liest(self.table):
                self.fired = True
                self.action()
        finally:
            self._quiet = False
        return result
