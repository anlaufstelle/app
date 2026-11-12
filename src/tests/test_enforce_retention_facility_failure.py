"""A failure in one facility must not stop the retention run for the others.

The run catches the error per facility, counts the facility as failed, logs it
without the exception text (which can carry personal data), goes on with the
next facility and ends with an error exit and without a success marker.
"""

import logging
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from core.management.commands import enforce_retention as command_module
from core.models import AuditLog, DocumentType, Event, Facility, Settings

pytestmark = pytest.mark.django_db

SECRET = "Jane Doe, born 1990-01-01"


def _due_anonymous_event(facility):
    Settings.objects.get_or_create(facility=facility, defaults={"retention_anonymous_days": 90})
    doc_type = DocumentType.objects.create(facility=facility, name="Contact")
    return Event.objects.create(
        facility=facility,
        document_type=doc_type,
        is_anonymous=True,
        occurred_at=timezone.now() - timedelta(days=120),
        data_json={},
    )


@pytest.fixture
def two_facilities(facility, second_facility):
    ordered = list(Facility.objects.filter(pk__in=[facility.pk, second_facility.pk]).order_by("name"))
    events = {f.pk: _due_anonymous_event(f) for f in ordered}
    return ordered[0], ordered[1], events


@pytest.fixture
def first_facility_fails(monkeypatch, two_facilities):
    failing, _, _ = two_facilities
    real = command_module.process_facility_retention
    seen = []

    def flaky(facility, *args, **kwargs):
        seen.append(facility.pk)
        if facility.pk == failing.pk:
            raise RuntimeError(SECRET)
        return real(facility, *args, **kwargs)

    monkeypatch.setattr(command_module, "process_facility_retention", flaky)
    return seen


def _run(*args):
    stdout, stderr = StringIO(), StringIO()
    with pytest.raises(CommandError) as excinfo:
        call_command("enforce_retention", *args, stdout=stdout, stderr=stderr)
    return stdout.getvalue(), stderr.getvalue(), str(excinfo.value)


def test_failing_facility_does_not_stop_the_next_one(two_facilities, first_facility_fails, caplog):
    failing, healthy, events = two_facilities
    caplog.set_level(logging.ERROR)
    stdout, stderr, error = _run()

    assert first_facility_fails == [failing.pk, healthy.pk]
    assert Event.objects.get(pk=events[healthy.pk].pk).is_deleted
    assert not Event.objects.get(pk=events[failing.pk].pk).is_deleted
    assert "Run coverage: processed=1, skipped=0, failed=1, failed_facilities=1." in stdout
    assert "1 facility/facilities aborted" in error
    assert not AuditLog.objects.filter(action=AuditLog.Action.RETENTION_RUN_COMPLETED).exists()
    # The exception text can carry personal data and is never written out.
    assert str(failing.pk) in caplog.text
    assert "RuntimeError" in caplog.text
    for text in (caplog.text, stdout, stderr, error):
        assert SECRET not in text


def test_dry_run_also_ends_with_error_after_a_facility_failure(two_facilities, first_facility_fails):
    failing, healthy, events = two_facilities
    stdout, _, error = _run("--dry-run")
    assert first_facility_fails == [failing.pk, healthy.pk]
    assert "1 facility/facilities aborted" in error
    assert not Event.objects.get(pk=events[healthy.pk].pk).is_deleted


def test_run_without_failure_still_succeeds(two_facilities):
    _, healthy, events = two_facilities
    stdout = StringIO()
    call_command("enforce_retention", stdout=stdout)
    assert "failed=0, failed_facilities=0." in stdout.getvalue()
    assert all(Event.objects.get(pk=event.pk).is_deleted for event in events.values())
