"""Trash anonymization of persons and cases: the trash period in force decides (Refs #2714).

The run reads the facility settings once when it starts. A trash period
(``client_trash_days``) extended after that must still protect a person or a
case that sits in the trash longer than the old period but not longer than the
new one. Each target is decided on the period read under the Settings lock in
its own transaction, taken after the protection gate and before the person or
case row.
"""

import threading
import time
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection, connections, transaction
from django.utils import timezone

import core.services.case.deletion as case_deletion
import core.services.client as client_services
import core.services.client.main as client_main
from core.management.commands import enforce_retention as command
from core.models import Case, Client, Settings
from core.services.system.settings import update_settings
from tests._retention_race_helpers import backend_pid, waits_for_a_lock

KINDS = ["person", "case"]
WAIT_SECONDS = 10


def _run(facility):
    output = StringIO()
    call_command("enforce_retention", facility=facility.name, stdout=output)
    return output.getvalue()


def _trashed_target(kind, facility, settings_obj, client, staff_user, *, period_days=30, age_days=45):
    """Put a person or a case into the trash ``age_days`` ago, trash period ``period_days``."""
    settings_obj.client_trash_days = period_days
    settings_obj.save(update_fields=["client_trash_days"])
    deleted_at = timezone.now() - timedelta(days=age_days)
    if kind == "person":
        Client.objects.filter(pk=client.pk).update(is_deleted=True, deleted_at=deleted_at)
        return client
    case = Case.objects.create(facility=facility, client=client, title="Case", created_by=staff_user)
    Case.objects.filter(pk=case.pk).update(is_deleted=True, deleted_at=deleted_at)
    return case


def _anonymized(target):
    target.refresh_from_db()
    return target.anonymized_at is not None


def _poll(predicate):
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _writer(facility, admin_user, days, state, before_commit=None):
    """A settings writer on its own connection, using the regular service."""

    def _run_writer():
        connection.close()
        try:
            state["pid"] = backend_pid()
            with transaction.atomic():
                current = Settings.objects.get(facility=facility)
                update_settings(current, admin_user, client_trash_days=days)
                state["updated"] = True
                if before_commit is not None:
                    before_commit()
        except Exception as exc:  # noqa: BLE001 — reported by the test
            state["error"] = exc
        finally:
            connections.close_all()

    return threading.Thread(target=_run_writer, daemon=True)


def _patch_trash_step(monkeypatch, kind, before):
    """Run ``before()`` right before the trash step of ``kind`` starts."""
    module, name = (
        (client_services, "anonymize_eligible_soft_deleted_clients")
        if kind == "person"
        else (case_deletion, "anonymize_eligible_soft_deleted_cases")
    )
    real = getattr(module, name)

    def wrapped(*args, **kwargs):
        before()
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, wrapped)


@pytest.mark.django_db
@pytest.mark.parametrize("kind", KINDS)
def test_trash_extension_saved_after_the_event_step_preserves_the_target(
    facility, settings_obj, client_identified, staff_user, admin_user, monkeypatch, kind
):
    """The run started on 30 days; 90 days saved after the event step keeps the 45-day-old target."""
    target = _trashed_target(kind, facility, settings_obj, client_identified, staff_user)
    real = command.process_facility_retention
    run_started_with = []

    def extend_after_events(current_facility, run_settings, now, dry_run):
        result = real(current_facility, run_settings, now, dry_run)
        update_settings(Settings.objects.get(facility=facility), admin_user, client_trash_days=90)
        run_started_with.append(current_facility.settings.client_trash_days)
        return result

    monkeypatch.setattr(command, "process_facility_retention", extend_after_events)
    _run(facility)

    assert run_started_with == [30]
    assert Settings.objects.get(facility=facility).client_trash_days == 90
    assert not _anonymized(target)


@pytest.mark.django_db
@pytest.mark.parametrize("kind", KINDS)
def test_trash_target_past_the_period_in_force_is_still_anonymized(
    facility, settings_obj, client_identified, staff_user, kind
):
    """Control: without a change the fresh period decides exactly like the run's own."""
    target = _trashed_target(kind, facility, settings_obj, client_identified, staff_user)

    _run(facility)

    assert _anonymized(target)


@pytest.mark.django_db
@pytest.mark.parametrize("kind", KINDS)
def test_trash_target_inside_an_extension_below_its_age_is_still_anonymized(
    facility, settings_obj, client_identified, staff_user, admin_user, monkeypatch, kind
):
    """Control: 30 -> 40 days during the run still lets the 45-day-old target go."""
    target = _trashed_target(kind, facility, settings_obj, client_identified, staff_user)
    _patch_trash_step(
        monkeypatch,
        kind,
        lambda: update_settings(Settings.objects.get(facility=facility), admin_user, client_trash_days=40),
    )

    _run(facility)

    assert _anonymized(target)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", KINDS)
def test_trash_extension_committed_by_another_session_preserves_the_target(
    facility, settings_obj, client_identified, staff_user, admin_user, monkeypatch, kind
):
    """A second session commits 90 days before the trash step; the step honours it."""
    target = _trashed_target(kind, facility, settings_obj, client_identified, staff_user)
    state = {}

    def extend_in_another_session():
        writer = _writer(facility, admin_user, 90, state)
        writer.start()
        writer.join(30)
        state["alive"] = writer.is_alive()

    _patch_trash_step(monkeypatch, kind, extend_in_another_session)
    _run(facility)

    assert "error" not in state, state.get("error")
    assert state == {"pid": state["pid"], "updated": True, "alive": False}
    assert Settings.objects.get(facility=facility).client_trash_days == 90
    assert not _anonymized(target)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", KINDS)
def test_trash_extension_uncommitted_at_the_lock_governs_the_step(
    facility, settings_obj, client_identified, staff_user, admin_user, monkeypatch, kind
):
    """A writer holds the Settings row when the trash step starts: the run waits and reads 90."""
    target = _trashed_target(kind, facility, settings_obj, client_identified, staff_user)
    state, main = {}, {}

    def commit_once_the_run_waits():
        main["blocked"] = _poll(lambda: waits_for_a_lock(main["pid"]))

    def hold_the_row():
        main["pid"] = backend_pid()
        writer = _writer(facility, admin_user, 90, state, before_commit=commit_once_the_run_waits)
        main["thread"] = writer
        writer.start()
        _poll(lambda: state.get("updated") or "error" in state)

    _patch_trash_step(monkeypatch, kind, hold_the_row)
    _run(facility)
    main["thread"].join(30)

    assert "error" not in state, state.get("error")
    assert main["blocked"] is True
    assert Settings.objects.get(facility=facility).client_trash_days == 90
    assert not _anonymized(target)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", KINDS)
def test_settings_writer_waits_while_a_trash_target_is_decided(
    facility, settings_obj, client_identified, staff_user, admin_user, monkeypatch, kind
):
    """A writer starting after the Settings lock waits; the target is decided on 30 days."""
    target = _trashed_target(kind, facility, settings_obj, client_identified, staff_user)
    state, seen = {}, {}

    def start_writer_and_wait_for_it():
        if "thread" in seen:
            return
        thread = _writer(facility, admin_user, 90, state)
        seen["thread"] = thread
        thread.start()
        seen["writer_blocked"] = _poll(lambda: state.get("pid") is not None and waits_for_a_lock(state["pid"]))
        seen["updated_while_locked"] = bool(state.get("updated"))

    if kind == "person":
        original_person = client_main._lock_client_case_rows

        def lock_person_rows(client):
            if client.pk == target.pk:
                start_writer_and_wait_for_it()
            return original_person(client)

        monkeypatch.setattr(client_main, "_lock_client_case_rows", lock_person_rows)
    else:
        original_case = case_deletion._lock_case_chain

        def lock_case_rows(facility_id, case_pk):
            if case_pk == target.pk:
                start_writer_and_wait_for_it()
            return original_case(facility_id, case_pk)

        monkeypatch.setattr(case_deletion, "_lock_case_chain", lock_case_rows)

    _run(facility)
    seen["thread"].join(30)

    assert "error" not in state, state.get("error")
    assert seen["writer_blocked"] is True
    assert seen["updated_while_locked"] is False
    assert _anonymized(target)
    assert Settings.objects.get(facility=facility).client_trash_days == 90
