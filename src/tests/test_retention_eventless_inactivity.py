"""Eventless retention must preserve active work and require qualified approval."""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.urls import reverse
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


@pytest.mark.parametrize("path", ["service", "http"])
@pytest.mark.parametrize("owner_kind,removed", [("client", False), ("client", True), ("case", True)])
def test_attachment_purge_restarts_inactivity_period(
    idle_person, facility, lead_user, client, path, owner_kind, removed, django_capture_on_commit_callbacks
):
    from core.models import DocumentType
    from core.services.attachments import purge_attachment_chain
    from tests.test_attachment_purge import _attach, _doc_type, _file_template

    purged_at = timezone.now()
    old = purged_at - timedelta(days=400)
    field = _file_template(facility, "Retention file")
    doc = _doc_type(facility, "Retention record", owner_kind, file_field=field)
    owner = idle_person
    if owner_kind == DocumentType.EntityType.CASE:
        owner = Case.objects.create(
            facility=facility,
            client=idle_person,
            title="Closed case",
            status=Case.Status.CLOSED,
            created_by=lead_user,
            document_type=doc,
        )
    else:
        owner.document_type = doc
        owner.save(update_fields=["document_type", "data_json"])
    attachment = _attach(facility, owner, field, lead_user, "retention.pdf")
    Attachment.objects.filter(pk=attachment.pk).update(created_at=old, deleted_at=old if removed else None)
    if removed:
        owner.data_json = {}
        owner.save(update_fields=["data_json"])
    Client.objects.filter(pk=idle_person.pk).update(updated_at=old)
    if owner_kind == DocumentType.EntityType.CASE:
        Case.objects.filter(pk=owner.pk).update(last_activity_at=old, updated_at=old)
    assert anonymize_clients(facility, dry_run=True)["count"] == 1

    with patch("django.utils.timezone.now", return_value=purged_at), django_capture_on_commit_callbacks(execute=True):
        if path == "http":
            client.force_login(lead_user)
            response = client.post(reverse("core:attachment_delete", kwargs={"pk": attachment.pk}))
            assert response.status_code == 302
            assert response.url == reverse("core:attachment_list")
        else:
            assert purge_attachment_chain(owner, attachment.entry_id) == 1
    assert not Attachment.objects.filter(pk=attachment.pk).exists()
    owner.refresh_from_db()
    assert field.slug not in owner.data_json

    for elapsed_days in (0, 364, 366):
        with patch("django.utils.timezone.now", return_value=purged_at + timedelta(days=elapsed_days)):
            result = anonymize_clients(facility, dry_run=False)
        idle_person.refresh_from_db()
        assert result["count"] == int(elapsed_days == 366)
        assert (idle_person.anonymized_at is not None) == (elapsed_days == 366)
