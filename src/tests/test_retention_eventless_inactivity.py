"""Eventless retention must preserve active work and require qualified approval."""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.utils import timezone

from core.models import Attachment, Case, Client, LegalHold, RetentionProposal, WorkItem
from core.retention.anonymization import anonymize_clients

pytestmark = pytest.mark.django_db


@pytest.fixture
def idle_person(client_identified, settings_obj):
    settings_obj.retention_identified_days = 365
    settings_obj.save(update_fields=["retention_identified_days"])
    Client.objects.filter(pk=client_identified.pk).update(updated_at=timezone.now() - timedelta(days=400))
    client_identified.refresh_from_db()
    return client_identified


def _run_retention(facility, path):
    if path == "command":
        call_command("enforce_retention", facility=facility.name, stdout=StringIO())
    else:
        return anonymize_clients(facility, dry_run=path == "preview")


@pytest.mark.parametrize("path", ["service", "preview", "command"])
@pytest.mark.parametrize("stage", [Client.ContactStage.IDENTIFIED, Client.ContactStage.QUALIFIED])
def test_eventless_open_case_is_preserved(idle_person, facility, staff_user, path, stage):
    Client.objects.filter(pk=idle_person.pk).update(contact_stage=stage)
    case = Case.objects.create(
        facility=facility, client=idle_person, title="Active case", description="Preserve notes", created_by=staff_user
    )
    result = _run_retention(facility, path)
    idle_person.refresh_from_db()
    case.refresh_from_db()
    assert idle_person.anonymized_at is None
    assert case.description == "Preserve notes"
    assert not RetentionProposal.objects.filter(facility=facility).exists()
    if result is not None:
        assert result["count"] == 0


@pytest.mark.parametrize("path", ["service", "preview", "command"])
def test_eventless_qualified_person_requires_approval(idle_person, facility, path):
    Client.objects.filter(pk=idle_person.pk).update(contact_stage=Client.ContactStage.QUALIFIED)
    result = _run_retention(facility, path)
    idle_person.refresh_from_db()
    assert idle_person.anonymized_at is None
    assert idle_person.pseudonym == "Test-ID-01"
    if result is not None:
        assert result["count"] == 0


@pytest.mark.parametrize("path", ["service", "preview", "command"])
def test_idle_eventless_identified_person_is_anonymized(idle_person, facility, path):
    result = _run_retention(facility, path)
    idle_person.refresh_from_db()
    assert (idle_person.anonymized_at is not None) == (path != "preview")
    if result is not None:
        assert result["count"] == 1


def _add_activity(person, facility, user, field_template, activity):
    now = timezone.now()
    old = now - timedelta(days=400)
    if activity in {"case", "case_attachment"}:
        case = Case.objects.create(
            facility=facility, client=person, title="Closed case", status=Case.Status.CLOSED, created_by=user
        )
        Case.objects.filter(pk=case.pk).update(last_activity_at=now if activity == "case" else old)
        if activity == "case":
            return
    if activity == "task":
        WorkItem.objects.create(facility=facility, client=person, title="Recent task", created_by=user)
        return
    attachment = Attachment.objects.create(
        facility=facility,
        **({"case": case} if activity == "case_attachment" else {"client": person}),
        field_template=field_template,
        storage_filename="unused.enc",
        original_filename_encrypted={},
        file_size=0,
        mime_type="text/plain",
        created_by=user,
    )
    if activity in {"attachment_deleted", "attachment_superseded"}:
        Attachment.objects.filter(pk=attachment.pk).update(
            created_at=old, **{"deleted_at" if activity == "attachment_deleted" else "superseded_at": now}
        )


@pytest.mark.parametrize("path", ["service", "preview", "command"])
@pytest.mark.parametrize(
    "activity", ["case", "task", "attachment", "case_attachment", "attachment_deleted", "attachment_superseded"]
)
def test_recent_activity_defers_eventless_anonymization(
    idle_person, facility, staff_user, field_template, path, activity
):
    _add_activity(idle_person, facility, staff_user, field_template, activity)
    result = _run_retention(facility, path)
    idle_person.refresh_from_db()
    assert idle_person.anonymized_at is None
    if result is not None:
        assert result["count"] == 0


@pytest.mark.parametrize("change", ["qualified", "open_case", "person", "case", "task", "attachment"])
def test_eventless_eligibility_is_rechecked_under_lock(idle_person, facility, staff_user, field_template, change):
    from core.retention import anonymization

    original = anonymization._lock_client_event_rows

    def concurrent_change(person):
        original(person)
        if change == "qualified":
            Client.objects.filter(pk=person.pk).update(contact_stage=Client.ContactStage.QUALIFIED)
        elif change == "open_case":
            Case.objects.create(facility=facility, client=person, title="New case", created_by=staff_user)
        elif change == "person":
            Client.objects.filter(pk=person.pk).update(updated_at=timezone.now())
        else:
            _add_activity(person, facility, staff_user, field_template, change)

    with patch.object(anonymization, "_lock_client_event_rows", side_effect=concurrent_change):
        result = anonymize_clients(facility, False)
    idle_person.refresh_from_db()
    assert idle_person.anonymized_at is None
    assert result["count"] == 0


@pytest.mark.parametrize("protection", ["hold", "restriction"])
@pytest.mark.parametrize("path", ["service", "preview", "command"])
def test_eventless_protected_person_is_preserved(idle_person, facility, lead_user, protection, path):
    if protection == "hold":
        LegalHold.objects.create(
            facility=facility,
            target_type="Client",
            target_id=idle_person.pk,
            reason="Preserve evidence",
            created_by=lead_user,
        )
    else:
        Client.objects.filter(pk=idle_person.pk).update(processing_restricted=True)
    _run_retention(facility, path)
    idle_person.refresh_from_db()
    assert idle_person.anonymized_at is None
