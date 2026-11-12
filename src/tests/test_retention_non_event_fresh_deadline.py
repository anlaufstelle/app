"""Activities, handover notes and eventless persons: the period in force decides (Refs #2714).

The run reads the facility settings once when it starts. A period extended
after that must still protect an activity, a handover note or an eventless
identified person that the run reaches later; the deletion or anonymization is
decided on the period read under the Settings lock in the target's own
transaction. A shortened period reaches newly due targets in the next run at
the latest; the selection keeps the period the step started with.
"""

import threading
import time
from datetime import timedelta
from io import StringIO

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.db import connection, connections, transaction
from django.utils import timezone

from core.management.commands import enforce_retention as command
from core.models import Activity, AuditLog, Client, Event, LegalHold, Settings, ShiftHandoverNote
from core.retention import enforcement
from core.services.compliance import detect_mass_delete
from core.services.system.settings import update_settings
from tests._retention_race_helpers import backend_pid, waits_for_a_lock

PERIOD_FIELDS = {
    "activity": "retention_activities_days",
    "handover": "retention_handover_notes_days",
    "person": "retention_identified_days",
}
WAIT_SECONDS = 10


def _run(facility):
    output = StringIO()
    call_command("enforce_retention", facility=facility.name, stdout=output)
    return output.getvalue()


def _set_periods(settings_obj, days):
    for field in PERIOD_FIELDS.values():
        setattr(settings_obj, field, days)
    settings_obj.save()


def _target(kind, facility, client, age_days):
    """Create the target of ``kind``, last changed ``age_days`` ago."""
    old = timezone.now() - timedelta(days=age_days)
    if kind == "activity":
        return Activity.objects.create(
            facility=facility,
            verb=Activity.Verb.UPDATED,
            target_type=ContentType.objects.get_for_model(Client),
            target_id=client.pk,
            occurred_at=old,
        )
    if kind == "handover":
        note = ShiftHandoverNote.objects.create(
            facility=facility,
            shift_date=old.date(),
            window_start=old,
            window_end=old + timedelta(hours=8),
            editable_until=old + timedelta(hours=16),
            note={},
        )
        ShiftHandoverNote.objects.filter(pk=note.pk).update(updated_at=old)
        return note
    Client.objects.filter(pk=client.pk).update(updated_at=old)
    return client


def _target_kept(kind, target):
    if kind == "person":
        target.refresh_from_db()
        return target.anonymized_at is None
    return type(target).objects.filter(pk=target.pk).exists()


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


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["activity", "handover", "person"])
def test_extension_saved_after_the_event_step_preserves_the_target(
    facility, settings_obj, client_identified, admin_user, monkeypatch, kind
):
    """The run started on 30 days; 90 days saved before the step reaches the 45-day-old target."""
    _set_periods(settings_obj, 30)
    target = _target(kind, facility, client_identified, 45)
    field = PERIOD_FIELDS[kind]
    real = command.process_facility_retention
    run_started_with = []

    def extend_after_events(current_facility, run_settings, now, dry_run):
        result = real(current_facility, run_settings, now, dry_run)
        update_settings(Settings.objects.get(facility=facility), admin_user, **{field: 90})
        run_started_with.append(getattr(current_facility.settings, field))
        return result

    monkeypatch.setattr(command, "process_facility_retention", extend_after_events)
    _run(facility)

    assert run_started_with == [30]
    assert getattr(Settings.objects.get(facility=facility), field) == 90
    assert _target_kept(kind, target)


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["activity", "handover", "person"])
def test_target_past_the_period_in_force_is_still_removed(facility, settings_obj, client_identified, kind):
    """Control: without a change the fresh period decides exactly like the run's own."""
    _set_periods(settings_obj, 30)
    target = _target(kind, facility, client_identified, 45)

    _run(facility)

    assert not _target_kept(kind, target)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", ["activity", "handover", "person"])
def test_extension_committed_by_another_session_preserves_the_target(
    facility, settings_obj, client_identified, admin_user, monkeypatch, kind
):
    """A second session commits 90 days while the run processes events; the later step honours it."""
    _set_periods(settings_obj, 30)
    target = _target(kind, facility, client_identified, 45)
    field = PERIOD_FIELDS[kind]
    real = command.process_facility_retention
    state = {}

    def extend_in_another_session(current_facility, run_settings, now, dry_run):
        result = real(current_facility, run_settings, now, dry_run)
        writer = _writer(facility, admin_user, {field: 90}, state)
        writer.start()
        writer.join(30)
        state["alive"] = writer.is_alive()
        return result

    monkeypatch.setattr(command, "process_facility_retention", extend_in_another_session)
    _run(facility)

    assert "error" not in state, state.get("error")
    assert state == {"pid": state["pid"], "updated": True, "alive": False}
    assert getattr(Settings.objects.get(facility=facility), field) == 90
    assert _target_kept(kind, target)


@pytest.mark.django_db(transaction=True)
def test_settings_writer_waits_while_an_activity_is_decided(
    facility, settings_obj, client_identified, admin_user, monkeypatch
):
    """A writer starting after the Settings lock waits; the activity is decided on 30 days."""
    _set_periods(settings_obj, 30)
    activity = _target("activity", facility, client_identified, 45)
    original = enforcement._lock_activity_and_recheck_hold
    state, seen = {}, {}

    def start_writer_then_lock(current_facility, pk):
        if pk == activity.pk and "thread" not in seen:
            thread = _writer(facility, admin_user, {"retention_activities_days": 90}, state)
            seen["thread"] = thread
            thread.start()
            seen["writer_blocked"] = _poll(lambda: state.get("pid") is not None and waits_for_a_lock(state["pid"]))
            seen["updated_while_locked"] = bool(state.get("updated"))
        return original(current_facility, pk)

    monkeypatch.setattr(enforcement, "_lock_activity_and_recheck_hold", start_writer_then_lock)
    _run(facility)
    seen["thread"].join(30)

    assert "error" not in state, state.get("error")
    assert seen["writer_blocked"] is True
    assert seen["updated_while_locked"] is False
    assert not Activity.objects.filter(pk=activity.pk).exists()
    assert Settings.objects.get(facility=facility).retention_activities_days == 90


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", ["activity", "handover", "person"])
def test_extension_uncommitted_at_the_lock_governs_the_step(
    facility, settings_obj, client_identified, admin_user, monkeypatch, kind
):
    """A writer holding the Settings row when the target transaction starts: the run waits and reads 90."""
    _set_periods(settings_obj, 30)
    target = _target(kind, facility, client_identified, 45)
    field = PERIOD_FIELDS[kind]
    real = command.process_facility_retention
    state, main = {}, {}

    def commit_once_the_run_waits():
        main["blocked"] = _poll(lambda: waits_for_a_lock(main["pid"]))

    def hold_the_row_after_events(current_facility, run_settings, now, dry_run):
        result = real(current_facility, run_settings, now, dry_run)
        main["pid"] = backend_pid()
        writer = _writer(facility, admin_user, {field: 90}, state, before_commit=commit_once_the_run_waits)
        main["thread"] = writer
        writer.start()
        _poll(lambda: state.get("updated") or "error" in state)
        return result

    monkeypatch.setattr(command, "process_facility_retention", hold_the_row_after_events)
    _run(facility)
    main["thread"].join(30)

    assert "error" not in state, state.get("error")
    assert main["blocked"] is True
    assert getattr(Settings.objects.get(facility=facility), field) == 90
    assert _target_kept(kind, target)


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["event", "activity"])
def test_shortened_period_reaches_newly_due_targets_in_the_next_run(
    facility, settings_obj, client_identified, doc_type_contact, admin_user, monkeypatch, kind
):
    """90 -> 30 days before the steps: the 45-day-old target stays this run and goes in the next."""
    field = "retention_anonymous_days" if kind == "event" else "retention_activities_days"
    setattr(settings_obj, field, 90)
    settings_obj.save(update_fields=[field])
    if kind == "event":
        target = Event.objects.create(
            facility=facility,
            is_anonymous=True,
            document_type=doc_type_contact,
            occurred_at=timezone.now() - timedelta(days=45),
            data_json={"duration": 20},
        )
    else:
        target = _target("activity", facility, client_identified, 45)
    real = command.process_facility_retention

    def shorten_before_events(current_facility, run_settings, now, dry_run):
        update_settings(Settings.objects.get(facility=facility), admin_user, **{field: 30})
        return real(current_facility, run_settings, now, dry_run)

    def removed():
        if kind == "event":
            return Event.objects.get(pk=target.pk).is_deleted
        return not Activity.objects.filter(pk=target.pk).exists()

    with monkeypatch.context() as scoped:
        scoped.setattr(command, "process_facility_retention", shorten_before_events)
        _run(facility)
    assert not removed()

    _run(facility)
    assert removed()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", ["activity", "handover"])
@pytest.mark.parametrize("second_period", [30, 90])
def test_audit_counts_deletions_by_the_period_that_authorized_them(
    facility, settings_obj, client_identified, admin_user, monkeypatch, settings, kind, second_period
):
    """Each committed deletion belongs to its own decision period, even within one run."""
    field = PERIOD_FIELDS[kind]
    setattr(settings_obj, field, 30)
    settings_obj.save(update_fields=[field])
    recent = _target(kind, facility, client_identified, 45)
    older = _target(kind, facility, client_identified, 120)
    original = enforcement.lock_current_settings
    decisions = []

    def extend_before_second_decision(current_facility):
        if decisions:
            assert not type(recent).objects.filter(pk=recent.pk).exists()
            update_settings(Settings.objects.get(facility=facility), admin_user, **{field: second_period})
        current = original(current_facility)
        decisions.append(getattr(current, field))
        return current

    monkeypatch.setattr(enforcement, "lock_current_settings", extend_before_second_decision)
    run = enforcement.enforce_activities if kind == "activity" else enforcement.enforce_handover_notes
    assert run(facility, settings_obj, timezone.now(), False) == {"count": 2}
    assert decisions == [30, second_period]
    assert not type(older).objects.filter(pk=older.pk).exists()
    category = "activities" if kind == "activity" else "handover_notes"
    entries = list(AuditLog.objects.filter(facility=facility, detail__category=category))
    assert len(entries) == 1
    entry = entries[0]
    assert entry.action == AuditLog.Action.DELETE
    assert entry.detail == {
        "category": category,
        "command": "enforce_retention",
        "count": 2,
        "retention_days": (
            30 if second_period == 30 else {"30": {"count": 1, "deferred": 0}, "90": {"count": 1, "deferred": 0}}
        ),
    }
    # Real deletion audits already count; splitting evidence must not add alarm rows.
    settings.BREACH_DELETE_THRESHOLD = 1
    settings.BREACH_DELETE_THRESHOLD_LONG = 1
    findings = detect_mass_delete(facility)
    assert len(findings) == 1
    assert findings[0]["count"] == 1
    settings.BREACH_DELETE_THRESHOLD = 2
    settings.BREACH_DELETE_THRESHOLD_LONG = 2
    assert detect_mass_delete(facility) == []


@pytest.mark.django_db
@pytest.mark.parametrize("age_days, expected_deferred", [(45, 0), (120, 1)])
def test_activity_hold_counts_only_if_the_current_period_is_expired(
    facility, settings_obj, client_identified, lead_user, admin_user, monkeypatch, age_days, expected_deferred
):
    """A hold added after selection only defers targets still due under the extended period."""
    settings_obj.retention_activities_days = 30
    settings_obj.save(update_fields=["retention_activities_days"])
    target = _target("activity", facility, client_identified, age_days)
    original = enforcement.lock_current_settings

    def extend_and_hold_before_decision(current_facility):
        update_settings(Settings.objects.get(facility=facility), admin_user, retention_activities_days=90)
        LegalHold.objects.create(
            facility=facility,
            target_type="Activity",
            target_id=target.pk,
            reason="Preserve the selected activity",
            created_by=lead_user,
        )
        return original(current_facility)

    monkeypatch.setattr(enforcement, "lock_current_settings", extend_and_hold_before_decision)
    assert enforcement.enforce_activities(facility, settings_obj, timezone.now(), False) == {"count": 0}
    assert Activity.objects.filter(pk=target.pk).exists()
    details = list(
        AuditLog.objects.filter(facility=facility, detail__category="activities").values_list("detail", flat=True)
    )
    assert details == (
        [{"category": "activities", "command": "enforce_retention", "count": 0, "retention_days": 90, "deferred": 1}]
        if expected_deferred
        else []
    )


@pytest.mark.django_db(transaction=True)
def test_activity_audit_attributes_deletions_and_holds_to_their_decision_periods(
    facility, settings_obj, client_identified, lead_user, admin_user, monkeypatch
):
    """A deletion under 30 days and a deferral under 90 days remain separate evidence."""
    settings_obj.retention_activities_days = 30
    settings_obj.save(update_fields=["retention_activities_days"])
    recent = _target("activity", facility, client_identified, 45)
    held = _target("activity", facility, client_identified, 120)
    original = enforcement.lock_current_settings
    decisions = []

    def extend_and_hold_before_second_decision(current_facility):
        if decisions:
            assert not Activity.objects.filter(pk=recent.pk).exists()
            update_settings(Settings.objects.get(facility=facility), admin_user, retention_activities_days=90)
            LegalHold.objects.create(
                facility=facility,
                target_type="Activity",
                target_id=held.pk,
                reason="Preserve the selected activity",
                created_by=lead_user,
            )
        current = original(current_facility)
        decisions.append(current.retention_activities_days)
        return current

    monkeypatch.setattr(enforcement, "lock_current_settings", extend_and_hold_before_second_decision)
    assert enforcement.enforce_activities(facility, settings_obj, timezone.now(), False) == {"count": 1}
    assert decisions == [30, 90]
    assert Activity.objects.filter(pk=held.pk).exists()
    entry = AuditLog.objects.get(facility=facility, detail__category="activities")
    assert entry.detail == {
        "category": "activities",
        "command": "enforce_retention",
        "count": 1,
        "deferred": 1,
        "retention_days": {"30": {"count": 1, "deferred": 0}, "90": {"count": 0, "deferred": 1}},
    }
