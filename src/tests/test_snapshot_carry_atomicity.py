"""A retention run never lowers or double-counts a monthly aggregate (Refs #2425).

The monthly snapshot keeps counting events a retention run deleted (the
carry-over in ``backend_data["_deleted"]``). These tests cover the ways the
carry-over used to get lost or counted twice: a run that stops after deleting,
a failure while summarising a deleted event, two overlapping runs, and a
recomputation that read the live counts before a deletion committed.

The concurrency cases need ``django_db(transaction=True)``: two real
connections only see each other's commits. They are built to observe — one
session parks at a defined point until the other one demonstrably waits for a
lock (``pg_locks``) or has finished; no timing races.
"""

import logging
import threading
import time
from datetime import datetime
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from core.models import Event, EventHistory, Settings, StatisticsSnapshot
from core.services.dashboard import create_or_update_snapshot
from tests._retention_race_helpers import JOIN_TIMEOUT, PARK_SECONDS, session_thread, waits_for_a_lock

SAMPLE_REPORT = "sample-report-1"


def _event(facility, staff_user, doc_type, month=6):
    return Event.objects.create(
        facility=facility,
        document_type=doc_type,
        created_by=staff_user,
        occurred_at=timezone.make_aware(datetime(2024, month, 15, 12)),
        is_anonymous=True,
        data_json={},
    )


def _retention_settings(facility):
    return Settings.objects.create(
        facility=facility,
        retention_anonymous_days=1,
        retention_identified_days=3650,
        retention_qualified_days=3650,
    )


def _run_retention():
    call_command("enforce_retention", stdout=StringIO())


def _counts(facility, month=6):
    """``(total contacts, sample report total)`` of the stored monthly snapshot."""
    row = StatisticsSnapshot.objects.get(facility=facility, year=2024, month=month)
    return row.data["total_contacts"], row.backend_data[SAMPLE_REPORT]["total"]


@pytest.mark.django_db
class TestInterruptedRuns:
    def test_run_stopping_after_deletion_keeps_the_carry(self, facility, staff_user, doc_type_contact, monkeypatch):
        _retention_settings(facility)
        _event(facility, staff_user, doc_type_contact)
        create_or_update_snapshot(facility, 2024, 6)

        def stop(*args, **kwargs):
            raise RuntimeError("run stopped after deleting")

        with monkeypatch.context() as patch:
            patch.setattr("core.retention.enforcement.audit_retention_decision", stop)
            with pytest.raises(RuntimeError, match="stopped"):
                _run_retention()
        assert Event.objects.filter(facility=facility, is_deleted=True).count() == 1

        _run_retention()
        create_or_update_snapshot(facility, 2024, 6)
        assert _counts(facility) == (1, 1)

    def test_crash_between_two_events_keeps_the_month_already_deleted(
        self, facility, staff_user, doc_type_contact, monkeypatch
    ):
        _retention_settings(facility)
        for month in (6, 7):
            _event(facility, staff_user, doc_type_contact, month=month)
            create_or_update_snapshot(facility, 2024, month)
        calls = []

        def crash_on_second_event(event):
            calls.append(event.pk)
            if len(calls) == 2:
                raise RuntimeError("crash while deleting the second event")

        with monkeypatch.context() as patch:
            patch.setattr("core.retention.enforcement.delete_attachments_for", crash_on_second_event)
            with pytest.raises(RuntimeError, match="crash"):
                _run_retention()
        assert Event.objects.filter(facility=facility, is_deleted=True).count() == 1

        _run_retention()
        for month in (6, 7):
            create_or_update_snapshot(facility, 2024, month)
        assert (_counts(facility, 6), _counts(facility, 7)) == ((1, 1), (1, 1))

    def test_failed_carry_of_second_month_is_retried_without_loss(
        self, facility, staff_user, doc_type_contact, monkeypatch
    ):
        import core.services.dashboard.snapshot as snapshots

        _retention_settings(facility)
        for month in (6, 7):
            _event(facility, staff_user, doc_type_contact, month=month)
            create_or_update_snapshot(facility, 2024, month)
        summarize = snapshots.get_statistics_for_events

        def fail_for_july(events):
            if events.filter(occurred_at__month=7).exists():
                raise RuntimeError("carry of the second month failed")
            return summarize(events)

        with monkeypatch.context() as patch:
            patch.setattr(snapshots, "get_statistics_for_events", fail_for_july)
            try:
                _run_retention()
            except RuntimeError:
                pass  # stopping the run is acceptable; losing the count is not

        _run_retention()
        for month in (6, 7):
            create_or_update_snapshot(facility, 2024, month)
        assert (_counts(facility, 6), _counts(facility, 7)) == ((1, 1), (1, 1))
        assert Event.objects.filter(facility=facility, is_deleted=False).count() == 0


@pytest.mark.django_db
class TestCarryFailureIsFailClosed:
    def test_backend_failure_keeps_the_event_and_reports_it(
        self, facility, staff_user, doc_type_contact, monkeypatch, caplog
    ):
        from core.reporting.backends.sample_report_1 import SampleReport1Backend

        _retention_settings(facility)
        _event(facility, staff_user, doc_type_contact)
        create_or_update_snapshot(facility, 2024, 6)
        summarize = SampleReport1Backend.summarize_events

        def fail_for_deleted(self, events):
            if events.filter(is_deleted=True).exists():
                raise RuntimeError("temporary backend failure")
            return summarize(self, events)

        with monkeypatch.context() as patch, caplog.at_level(logging.ERROR):
            patch.setattr(SampleReport1Backend, "summarize_events", fail_for_deleted)
            _run_retention()

        # Not deleted without its count: the next run tries again.
        assert Event.objects.filter(facility=facility, is_deleted=False).count() == 1
        assert _counts(facility) == (1, 1)
        assert any(record.levelno >= logging.ERROR and record.exc_info for record in caplog.records)

        _run_retention()
        create_or_update_snapshot(facility, 2024, 6)
        row = StatisticsSnapshot.objects.get(facility=facility, year=2024, month=6)
        assert (_counts(facility), row.backend_data.get("_errors")) == ((1, 1), None)
        assert Event.objects.filter(facility=facility, is_deleted=True).count() == 1


# ---------------------------------------------------------------------------
# Two sessions
# ---------------------------------------------------------------------------


def _park_until_blocked_or_done(other_session, other_done):
    """Wait until the other session waits for a lock or has finished (at most ``PARK_SECONDS``)."""
    deadline = time.monotonic() + PARK_SECONDS
    while time.monotonic() < deadline and not other_done.is_set():
        pid = other_session.get("pid")
        if pid is not None and waits_for_a_lock(pid):
            return "blocked"
        time.sleep(0.05)
    return "done" if other_done.is_set() else "timeout"


def _only_in(thread_name, hook, original):
    """Run ``hook`` before ``original`` in the named thread only."""

    def wrapper(*args, **kwargs):
        if threading.current_thread().name == thread_name:
            hook()
        return original(*args, **kwargs)

    return wrapper


def _start_first_then_second(first, second, first_parked):
    first.start()
    assert first_parked.wait(JOIN_TIMEOUT), "the first session never reached its parking point"
    second.start()
    for thread in (first, second):
        thread.join(timeout=JOIN_TIMEOUT)
    assert not first.is_alive() and not second.is_alive(), "the two sessions block each other"


def _setup_committed(facility, staff_user, doc_type_contact, snapshot=True):
    _retention_settings(facility)
    event = _event(facility, staff_user, doc_type_contact)
    if snapshot:
        create_or_update_snapshot(facility, 2024, 6)
    return event


@pytest.mark.django_db(transaction=True)
class TestOverlappingRetentionRuns:
    def test_run_starting_before_another_run_finished_counts_each_event_once(
        self, facility, staff_user, doc_type_contact, monkeypatch
    ):
        import core.management.commands.enforce_retention as command

        _setup_committed(facility, staff_user, doc_type_contact)
        first_parked, second_done = threading.Event(), threading.Event()
        results, errors = {}, {}

        def park():
            first_parked.set()
            second_done.wait(JOIN_TIMEOUT)

        monkeypatch.setattr(
            command,
            "process_facility_retention",
            _only_in("run-a", park, command.process_facility_retention),
        )

        def second_run():
            try:
                _run_retention()
            finally:
                second_done.set()

        first = session_thread("run-a", _run_retention, results, errors)
        second = session_thread("run-b", second_run, results, errors)
        _start_first_then_second(first, second, first_parked)

        assert not errors, errors
        assert Event.objects.filter(facility=facility, is_deleted=True).count() == 1
        assert _counts(facility) == (1, 1)
        create_or_update_snapshot(facility, 2024, 6)
        assert _counts(facility) == (1, 1)

    def test_run_waiting_for_the_event_lock_counts_it_once(self, facility, staff_user, doc_type_contact, monkeypatch):
        import core.retention.enforcement as enforcement

        event = _setup_committed(facility, staff_user, doc_type_contact)
        first_parked, second_done = threading.Event(), threading.Event()
        second_session, results, errors, parked = {}, {}, {}, {}

        def park():
            first_parked.set()
            parked["a"] = _park_until_blocked_or_done(second_session, second_done)

        # Run A parks inside its deletion transaction, holding the event lock.
        monkeypatch.setattr(
            enforcement,
            "delete_attachments_for",
            _only_in("run-a", park, enforcement.delete_attachments_for),
        )

        def second_run():
            try:
                _run_retention()
            finally:
                second_done.set()

        first = session_thread("run-a", _run_retention, results, errors)
        second = session_thread("run-b", second_run, results, errors, session=second_session)
        _start_first_then_second(first, second, first_parked)

        assert not errors, errors
        assert parked["a"] == "blocked"
        assert EventHistory.objects.filter(event=event, action=EventHistory.Action.DELETE).count() == 1
        assert _counts(facility) == (1, 1)
        create_or_update_snapshot(facility, 2024, 6)
        assert _counts(facility) == (1, 1)


@pytest.mark.django_db(transaction=True)
class TestRecomputeAgainstDeletion:
    def _race(self, facility, monkeypatch):
        """Session A recomputes June and parks after reading the live counts; session B runs retention."""
        import core.services.dashboard.snapshot as snapshots

        first_parked, second_done = threading.Event(), threading.Event()
        second_session, results, errors, parked = {}, {}, {}, {}

        def park():
            first_parked.set()
            parked["a"] = _park_until_blocked_or_done(second_session, second_done)

        statistics = snapshots.get_statistics

        def read_then_park(*args, **kwargs):
            result = statistics(*args, **kwargs)
            if threading.current_thread().name == "recompute-a":
                park()
            return result

        monkeypatch.setattr(snapshots, "get_statistics", read_then_park)

        def second_run():
            try:
                _run_retention()
            finally:
                second_done.set()

        first = session_thread("recompute-a", lambda: create_or_update_snapshot(facility, 2024, 6), results, errors)
        second = session_thread("run-b", second_run, results, errors, session=second_session)
        _start_first_then_second(first, second, first_parked)
        assert not errors, errors
        return parked["a"]

    def test_deletion_waits_for_a_recompute_that_counted_the_event_live(
        self, facility, staff_user, doc_type_contact, monkeypatch
    ):
        _setup_committed(facility, staff_user, doc_type_contact)

        parked = self._race(facility, monkeypatch)

        assert Event.objects.filter(facility=facility, is_deleted=True).count() == 1
        assert _counts(facility) == (1, 1)
        assert parked == "blocked"
        create_or_update_snapshot(facility, 2024, 6)
        assert _counts(facility) == (1, 1)

    def test_first_snapshot_of_the_month_and_deletion_count_the_event_once(
        self, facility, staff_user, doc_type_contact, monkeypatch
    ):
        _setup_committed(facility, staff_user, doc_type_contact, snapshot=False)

        parked = self._race(facility, monkeypatch)

        assert StatisticsSnapshot.objects.filter(facility=facility, year=2024, month=6).count() == 1
        assert _counts(facility) == (1, 1)
        assert parked == "blocked"
        create_or_update_snapshot(facility, 2024, 6)
        assert _counts(facility) == (1, 1)
