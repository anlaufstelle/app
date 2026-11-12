"""Event history retention: the detail and chronicle periods in force decide (Refs #2714).

The run reads the facility settings once when it starts. A detail period
(``eventhistory_detail_months``) or a chronicle period
(``eventhistory_chronicle_months``) extended after that must still protect the
history rows it covers. Each batch is decided on the period read under the
Settings lock, taken before the person and event rows of the batch.
"""

import threading
import time
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection, connections, transaction
from django.utils import timezone

from core.management.commands import enforce_retention as command
from core.models import EventHistory, Settings
from core.services.events import create_event, update_event
from core.services.system import bypass_replication_triggers
from core.services.system.settings import update_settings
from tests._retention_race_helpers import backend_pid, waits_for_a_lock

PERIOD_FIELDS = {
    "detail": "eventhistory_detail_months",
    "chronicle": "eventhistory_chronicle_months",
}
WAIT_SECONDS = 10


def _run(facility):
    output = StringIO()
    call_command("enforce_retention", facility=facility.name, stdout=output)
    return output.getvalue()


def _history_target(kind, facility, settings_obj, staff_user, doc_type_contact):
    """Return the oldest history row of a fresh event, 45 days old, period 1 month.

    ``detail``: the row's version is 45 days old and unredacted.
    ``chronicle``: the row has been redacted for 45 days.
    """
    setattr(settings_obj, PERIOD_FIELDS[kind], 1)
    settings_obj.save(update_fields=[PERIOD_FIELDS[kind]])
    event = create_event(
        facility=facility,
        user=staff_user,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={"dauer": 15, "notiz": "First"},
    )
    update_event(event, staff_user, {"dauer": 30, "notiz": "Second"})
    rows = list(EventHistory.objects.filter(event=event).order_by("changed_at", "pk"))
    old = timezone.now() - timedelta(days=45)
    with bypass_replication_triggers(tables=("core_eventhistory",)):
        if kind == "detail":
            for index, row in enumerate(rows):
                EventHistory.objects.filter(pk=row.pk).update(changed_at=old + timedelta(seconds=index))
        else:
            EventHistory.objects.filter(pk=rows[0].pk).update(
                data_after={"_redacted": True, "fields": ["dauer"]},
                redacted_at=old,
            )
    return rows[0]


def _target_kept(kind, row):
    current = EventHistory.objects.filter(pk=row.pk).first()
    if kind == "chronicle":
        return current is not None
    return current is not None and current.redacted_at is None


def _poll(predicate):
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _writer(facility, admin_user, changes, state, before_commit=None):
    """A settings writer on its own connection, using the regular service."""

    def _run_writer():
        connection.close()
        try:
            state["pid"] = backend_pid()
            with transaction.atomic():
                current = Settings.objects.get(facility=facility)
                update_settings(current, admin_user, **changes)
                state["updated"] = True
                if before_commit is not None:
                    before_commit()
        except Exception as exc:  # noqa: BLE001 — reported by the test
            state["error"] = exc
        finally:
            connections.close_all()

    return threading.Thread(target=_run_writer, daemon=True)


def _before_history_step(monkeypatch, before):
    real = command.enforce_eventhistory_retention

    def wrapped(current_facility, run_settings, now, dry_run):
        before(run_settings)
        return real(current_facility, run_settings, now, dry_run)

    monkeypatch.setattr(command, "enforce_eventhistory_retention", wrapped)


@pytest.mark.django_db
@pytest.mark.parametrize("kind", list(PERIOD_FIELDS))
def test_history_extension_saved_during_the_run_preserves_the_row(
    facility, settings_obj, staff_user, admin_user, doc_type_contact, monkeypatch, kind
):
    """The run started on 1 month; 3 months saved before the history step keep the 45-day-old row."""
    field = PERIOD_FIELDS[kind]
    row = _history_target(kind, facility, settings_obj, staff_user, doc_type_contact)
    run_started_with = []

    def extend(run_settings):
        run_started_with.append(getattr(run_settings, field))
        update_settings(Settings.objects.get(facility=facility), admin_user, **{field: 3})

    _before_history_step(monkeypatch, extend)
    _run(facility)

    assert run_started_with == [1]
    assert getattr(Settings.objects.get(facility=facility), field) == 3
    assert _target_kept(kind, row)


@pytest.mark.django_db
@pytest.mark.parametrize("kind", list(PERIOD_FIELDS))
def test_history_row_past_the_period_in_force_is_still_processed(
    facility, settings_obj, staff_user, doc_type_contact, kind
):
    """Control: without a change the fresh period decides exactly like the run's own."""
    row = _history_target(kind, facility, settings_obj, staff_user, doc_type_contact)

    _run(facility)

    assert not _target_kept(kind, row)


@pytest.mark.django_db
@pytest.mark.parametrize("kind", list(PERIOD_FIELDS))
def test_history_period_switched_off_during_the_run_keeps_the_row(
    facility, settings_obj, staff_user, admin_user, doc_type_contact, monkeypatch, kind
):
    """``0`` saved during the run means unlimited: the step keeps the row."""
    field = PERIOD_FIELDS[kind]
    row = _history_target(kind, facility, settings_obj, staff_user, doc_type_contact)
    _before_history_step(
        monkeypatch,
        lambda _run_settings: update_settings(Settings.objects.get(facility=facility), admin_user, **{field: 0}),
    )

    _run(facility)

    assert _target_kept(kind, row)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", list(PERIOD_FIELDS))
def test_history_extension_committed_by_another_session_preserves_the_row(
    facility, settings_obj, staff_user, admin_user, doc_type_contact, monkeypatch, kind
):
    """A second session commits 3 months before the history step; the step honours it."""
    field = PERIOD_FIELDS[kind]
    row = _history_target(kind, facility, settings_obj, staff_user, doc_type_contact)
    state = {}

    def extend_in_another_session(_run_settings):
        writer = _writer(facility, admin_user, {field: 3}, state)
        writer.start()
        writer.join(30)
        state["alive"] = writer.is_alive()

    _before_history_step(monkeypatch, extend_in_another_session)
    _run(facility)

    assert "error" not in state, state.get("error")
    assert state == {"pid": state["pid"], "updated": True, "alive": False}
    assert getattr(Settings.objects.get(facility=facility), field) == 3
    assert _target_kept(kind, row)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", list(PERIOD_FIELDS))
def test_history_extension_uncommitted_at_the_lock_governs_the_batch(
    facility, settings_obj, staff_user, admin_user, doc_type_contact, monkeypatch, kind
):
    """A writer holds the Settings row when the history step starts: the batch waits and reads 3."""
    field = PERIOD_FIELDS[kind]
    row = _history_target(kind, facility, settings_obj, staff_user, doc_type_contact)
    state, main = {}, {}

    def commit_once_the_run_waits():
        main["blocked"] = _poll(lambda: waits_for_a_lock(main["pid"]))

    def hold_the_row(_run_settings):
        main["pid"] = backend_pid()
        writer = _writer(facility, admin_user, {field: 3}, state, before_commit=commit_once_the_run_waits)
        main["thread"] = writer
        writer.start()
        _poll(lambda: state.get("updated") or "error" in state)

    _before_history_step(monkeypatch, hold_the_row)
    _run(facility)
    main["thread"].join(30)

    assert "error" not in state, state.get("error")
    assert main["blocked"] is True
    assert getattr(Settings.objects.get(facility=facility), field) == 3
    assert _target_kept(kind, row)
