"""Retention protection through public services and HTTP paths (Refs #2524)."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, Case, Client, DeletionRequest, EventHistory, LegalHold
from core.retention.anonymization import anonymize_clients
from core.retention.enforcement import process_facility_retention
from core.retention.eventhistory import enforce_eventhistory_retention
from core.retention.proposals import approve_proposal, create_proposal
from core.services.client import update_client
from tests._factories import make_event

pytestmark = pytest.mark.django_db


@pytest.fixture
def expired_event(facility, client_qualified, doc_type_contact, lead_user):
    return make_event(
        facility=facility,
        client=client_qualified,
        document_type=doc_type_contact,
        occurred_at=timezone.now() - timedelta(days=4000),
        data_json={"duration": 20},
        created_by=lead_user,
    )


@pytest.mark.parametrize("target_type", ["Client", "Case", "Event"])
def test_record_hold_can_be_created_and_dismissed(
    client, lead_user, expired_event, facility, settings_obj, target_type
):
    person = expired_event.client
    case = Case.objects.create(facility=facility, client=person, title="Held case", created_by=lead_user)
    expired_event.case = case
    expired_event.save(update_fields=["case"])
    targets = {"Client": ("clients", person), "Case": ("cases", case), "Event": ("events", expired_event)}
    segment, target = targets[target_type]
    url = f"/{segment}/{target.pk}/legal-hold/"
    client.force_login(lead_user)
    response = client.post(url, {"reason": "Preserve evidence", "expires_at": ""})
    assert response.status_code == 302
    hold = LegalHold.objects.get(facility=facility, target_type=target_type, target_id=target.pk)
    assert hold.is_active
    assert process_facility_retention(facility, settings_obj, timezone.now(), False)["count"] == 0
    response = client.post(f"/{segment}/{target.pk}/legal-hold/{hold.pk}/dismiss/")
    assert response.status_code == 302
    hold.refresh_from_db()
    assert hold.dismissed_by_id == lead_user.pk
    assert not hold.is_active
    assert AuditLog.objects.filter(action=AuditLog.Action.LEGAL_HOLD, target_id=target.pk).count() == 2


def test_system_hold_dismissal(client, super_admin_user, expired_event, facility, lead_user):
    hold = LegalHold.objects.create(
        facility=facility,
        target_type="Event",
        target_id=expired_event.pk,
        reason="Preserve evidence",
        created_by=lead_user,
    )
    client.force_login(super_admin_user)
    assert client.post(f"/system/legal-holds/{hold.pk}/dismiss/").status_code == 302
    hold.refresh_from_db()
    assert hold.dismissed_by_id == super_admin_user.pk


@pytest.mark.parametrize("stage", [Client.ContactStage.IDENTIFIED, Client.ContactStage.QUALIFIED])
@pytest.mark.parametrize("dry_run", [False, True])
def test_restricted_retention_defers_event(expired_event, facility, settings_obj, stage, dry_run):
    person = expired_event.client
    person.contact_stage = stage
    person.processing_restricted = True
    person.save(update_fields=["contact_stage", "processing_restricted"])
    result = process_facility_retention(facility, settings_obj, timezone.now(), dry_run)
    expired_event.refresh_from_db()
    assert not expired_event.is_deleted
    assert result["count"] == 0
    assert result["deferred"] == 1


@pytest.mark.parametrize("with_event", [False, True])
@pytest.mark.parametrize("dry_run", [False, True])
def test_restricted_anonymization_defers_person(expired_event, facility, settings_obj, with_event, dry_run):
    person = expired_event.client
    if with_event:
        expired_event.is_deleted = True
        expired_event.save(update_fields=["is_deleted"])
    else:
        expired_event.delete()
    Client.objects.filter(pk=person.pk).update(
        processing_restricted=True, updated_at=timezone.now() - timedelta(days=4000)
    )
    result = anonymize_clients(facility, dry_run)
    person.refresh_from_db()
    assert person.anonymized_at is None
    assert result["count"] == 0
    assert result["deferred"] == 1


@pytest.mark.parametrize("dry_run", [False, True])
def test_restricted_history_defers_redaction(expired_event, facility, settings_obj, dry_run):
    expired_event.is_deleted = True
    expired_event.save(update_fields=["is_deleted"])
    with patch("django.utils.timezone.now", return_value=timezone.now() - timedelta(days=4000)):
        row = EventHistory.objects.create(
            event=expired_event, action=EventHistory.Action.UPDATE, data_before={"private": "preserve"}
        )
    Client.objects.filter(pk=expired_event.client_id).update(processing_restricted=True)
    result = enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run)
    row.refresh_from_db()
    assert row.data_before == {"private": "preserve"}
    assert row.redacted_at is None
    assert result["deferred"] == 1


def test_existing_proposal_approval_rechecks_restriction(expired_event, lead_user):
    proposal, _ = create_proposal(
        expired_event.facility, "Event", expired_event.pk, timezone.localdate(), {}, "qualified"
    )
    Client.objects.filter(pk=expired_event.client_id).update(processing_restricted=True)
    with pytest.raises(ValidationError):
        approve_proposal(proposal, lead_user)
    proposal.refresh_from_db()
    assert proposal.status == "pending"


@pytest.mark.parametrize("stale_stage", [None, Client.ContactStage.IDENTIFIED])
def test_direct_downgrade_with_events_is_refused(expired_event, lead_user, stale_stage):
    person = expired_event.client
    with pytest.raises(ValidationError):
        update_client(person, lead_user, old_stage=stale_stage, contact_stage=Client.ContactStage.IDENTIFIED)
    person.refresh_from_db()
    assert person.contact_stage == Client.ContactStage.QUALIFIED


def test_downgrade_request_needs_another_confirmer(client, lead_user, admin_user, expired_event, facility):
    person = expired_event.client
    client.force_login(lead_user)
    response = client.post(f"/clients/{person.pk}/stage-downgrade/", {"reason": "Minimize stored information"})
    assert response.status_code == 302
    dr = DeletionRequest.objects.get(facility=facility, target_id=person.pk, status="pending")
    assert dr.stage_downgrade
    person.refresh_from_db()
    assert person.contact_stage == Client.ContactStage.QUALIFIED
    url = reverse("core:deletion_review", args=[dr.pk])
    client.post(url, {"action": "approve"})
    dr.refresh_from_db()
    assert dr.status == "pending"
    client.force_login(admin_user)
    assert client.post(url, {"action": "approve"}).status_code == 302
    dr.refresh_from_db()
    person.refresh_from_db()
    assert dr.status == "approved"
    assert dr.reviewed_by_id == admin_user.pk
    assert person.contact_stage == Client.ContactStage.IDENTIFIED
    assert not person.is_deleted
    expired_event.refresh_from_db()
    assert not expired_event.is_deleted
    assert (
        AuditLog.objects.filter(facility=facility, target_id=person.pk, action="client_stage_downgraded").count() == 1
    )


def test_client_form_refuses_direct_downgrade(expired_event, facility, lead_user):
    from core.forms.clients import ClientForm

    person = expired_event.client
    form = ClientForm(
        {"pseudonym": person.pseudonym, "contact_stage": "identified", "age_cluster": "unknown", "notes": ""},
        instance=person,
        facility=facility,
        user=lead_user,
    )
    assert not form.is_valid()
    assert "contact_stage" in form.errors


@pytest.mark.parametrize("protection", ["hold", "restriction"])
def test_downgrade_review_rechecks_protection(client, lead_user, admin_user, expired_event, facility, protection):
    from core.services.client.stage_downgrade import request_stage_downgrade, review_stage_downgrade

    dr = request_stage_downgrade(expired_event.client, lead_user, "Minimize information")
    if protection == "hold":
        LegalHold.objects.create(
            facility=facility,
            target_type="Client",
            target_id=expired_event.client_id,
            reason="Preserve",
            created_by=lead_user,
        )
    else:
        Client.objects.filter(pk=expired_event.client_id).update(processing_restricted=True)
    with pytest.raises(ValidationError):
        review_stage_downgrade(dr, admin_user, approve=True)
    dr.refresh_from_db()
    expired_event.client.refresh_from_db()
    assert dr.status == "pending"
    assert expired_event.client.contact_stage == "qualified"


def test_downgrade_rejection_is_audited_and_final(expired_event, lead_user, admin_user, facility):
    from core.services.client.stage_downgrade import request_stage_downgrade, review_stage_downgrade

    dr = request_stage_downgrade(expired_event.client, lead_user, "Minimize information")
    review_stage_downgrade(dr, admin_user, approve=False)
    with pytest.raises(ValidationError):
        review_stage_downgrade(dr, admin_user, approve=True)
    dr.refresh_from_db()
    expired_event.client.refresh_from_db()
    assert dr.status == "rejected"
    assert expired_event.client.contact_stage == "qualified"
    assert (
        AuditLog.objects.filter(
            facility=facility, action="stage_downgrade_rejected", target_id=expired_event.client_id
        ).count()
        == 1
    )


def test_downgrade_cannot_use_deletion_service(expired_event, lead_user, admin_user):
    from core.services.client import approve_client_deletion, reject_client_deletion
    from core.services.client.stage_downgrade import request_stage_downgrade

    dr = request_stage_downgrade(expired_event.client, lead_user, "Minimize information")
    for decide in [approve_client_deletion, reject_client_deletion]:
        with pytest.raises(ValidationError):
            decide(dr, admin_user)
    dr.refresh_from_db()
    assert dr.status == "pending"


@pytest.mark.parametrize("payload", [{}, {"reason": "   "}, {"reason": "Preserve", "expires_at": "invalid"}])
def test_record_hold_validates_form(client, lead_user, expired_event, payload):
    client.force_login(lead_user)
    response = client.post(f"/events/{expired_event.pk}/legal-hold/", payload)
    assert response.status_code == 400
    assert not LegalHold.objects.exists()


@pytest.mark.parametrize("target_type", ["Client", "Case", "Event"])
def test_expired_hold_does_not_protect_retention(expired_event, facility, settings_obj, lead_user, target_type):
    case = Case.objects.create(facility=facility, client=expired_event.client, title="Old case")
    expired_event.case = case
    expired_event.save(update_fields=["case"])
    target = {"Client": expired_event.client_id, "Case": case.pk, "Event": expired_event.pk}[target_type]
    LegalHold.objects.create(
        facility=facility,
        target_type=target_type,
        target_id=target,
        reason="Expired",
        expires_at=timezone.localdate() - timedelta(days=1),
        created_by=lead_user,
    )
    person = expired_event.client
    person.contact_stage = "identified"
    person.save(update_fields=["contact_stage"])
    assert process_facility_retention(facility, settings_obj, timezone.now(), False)["count"] == 1


@pytest.mark.parametrize("segment", ["clients", "cases", "events"])
def test_hold_dismissal_requires_post_and_csrf(client, lead_user, expired_event, facility, segment):
    from django.test import Client as HTTPClient

    case = Case.objects.create(facility=facility, client=expired_event.client, title="Held case")
    kind, target = {
        "clients": ("Client", expired_event.client_id),
        "cases": ("Case", case.pk),
        "events": ("Event", expired_event.pk),
    }[segment]
    hold = LegalHold.objects.create(
        facility=facility, target_type=kind, target_id=target, reason="Preserve", created_by=lead_user
    )
    url = f"/{segment}/{target}/legal-hold/{hold.pk}/dismiss/"
    client.force_login(lead_user)
    assert client.get(url).status_code == 405
    csrf_client = HTTPClient(enforce_csrf_checks=True)
    csrf_client.force_login(lead_user)
    assert csrf_client.post(url).status_code == 403
    assert csrf_client.post(f"/{segment}/{target}/legal-hold/", {"reason": "Preserve"}).status_code == 403
    hold.refresh_from_db()
    assert hold.dismissed_at is None


def test_system_dismissal_requires_post_and_csrf(client, super_admin_user, expired_event, facility, lead_user):
    from django.test import Client as HTTPClient

    hold = LegalHold.objects.create(
        facility=facility, target_type="Event", target_id=expired_event.pk, reason="Preserve", created_by=lead_user
    )
    url = f"/system/legal-holds/{hold.pk}/dismiss/"
    client.force_login(super_admin_user)
    assert client.get(url).status_code == 405
    csrf_client = HTTPClient(enforce_csrf_checks=True)
    csrf_client.force_login(super_admin_user)
    assert csrf_client.post(url).status_code == 403


def test_downgrade_requires_csrf(client, lead_user, expired_event):
    from django.test import Client as HTTPClient

    csrf_client = HTTPClient(enforce_csrf_checks=True)
    csrf_client.force_login(lead_user)
    assert (
        csrf_client.post(f"/clients/{expired_event.client_id}/stage-downgrade/", {"reason": "Minimize"}).status_code
        == 403
    )


def test_protection_pages_and_downgrade_review_render(client, expired_event, lead_user, admin_user, facility):
    from core.services.client.stage_downgrade import request_stage_downgrade

    client.force_login(lead_user)
    for url in [
        f"/clients/{expired_event.client_id}/legal-hold/",
        f"/events/{expired_event.pk}/legal-hold/",
        f"/clients/{expired_event.client_id}/stage-downgrade/",
    ]:
        assert client.get(url).status_code == 200
    dr = request_stage_downgrade(expired_event.client, lead_user, "Minimize")
    client.force_login(admin_user)
    response = client.get(reverse("core:deletion_review", args=[dr.pk]))
    assert response.status_code == 200
    assert "Herabstufungsantrag prüfen" in response.content.decode()
    assert client.get(reverse("core:client_detail", args=[expired_event.client_id])).status_code == 200
    assert client.get(reverse("core:deletion_request_list")).status_code == 200


@pytest.mark.parametrize("method", ["update", "bulk_update"])
def test_bulk_downgrade_with_events_is_refused(expired_event, method):
    person = expired_event.client
    with pytest.raises(ValidationError):
        if method == "update":
            Client.objects.filter(pk=person.pk).update(contact_stage="identified")
        else:
            person.contact_stage = "identified"
            Client.objects.bulk_update([person], ["contact_stage"])
    person.refresh_from_db()
    assert person.contact_stage == "qualified"


def test_domain_admin_cannot_downgrade(expired_event, admin_user, rf):
    from core.admin_site import anlaufstelle_admin_site

    request = rf.post("/")
    request.user = admin_user
    domain_admin = anlaufstelle_admin_site._registry[Client]
    assert not domain_admin.has_change_permission(request, expired_event.client)
    assert not domain_admin.get_actions(request)


def test_bulk_upsert_cannot_downgrade_with_events(expired_event):
    person = expired_event.client
    person.contact_stage = "identified"
    with pytest.raises(ValidationError):
        Client.objects.bulk_create(
            [person], update_conflicts=True, unique_fields=["pk"], update_fields=["contact_stage"]
        )
    person.refresh_from_db()
    assert person.contact_stage == "qualified"


def test_system_active_filter_excludes_expired_holds(client, super_admin_user, lead_user, facility, expired_event):
    hold = LegalHold.objects.create(
        facility=facility,
        target_type="Event",
        target_id=expired_event.pk,
        reason="Expired",
        expires_at=timezone.localdate() - timedelta(days=1),
        created_by=lead_user,
    )
    client.force_login(super_admin_user)
    response = client.get(reverse("core:system_legal_hold_list"), {"status": "active"})
    assert response.status_code == 200
    assert f"system-legal-hold-row-{hold.pk}" not in response.content.decode()


@pytest.mark.parametrize("dry_run", [False, True])
def test_restricted_trash_person_is_deferred_by_command(client_identified, facility, settings_obj, dry_run):
    from io import StringIO

    from django.core.management import call_command

    old = timezone.now() - timedelta(days=4000)
    Client.objects.filter(pk=client_identified.pk).update(
        is_deleted=True,
        deleted_at=old,
        processing_restricted=True,
    )
    output = StringIO()
    call_command("enforce_retention", facility=facility.name, dry_run=dry_run, stdout=output)
    client_identified.refresh_from_db()
    assert client_identified.anonymized_at is None
    assert "Deferred 1 event/person/case target(s)" in output.getvalue()
    if dry_run:
        assert "Would anonymize 0 trash-expired client(s)" in output.getvalue()


def test_type_retention_reports_restricted_target(expired_event, facility, doc_type_contact):
    from core.retention.enforcement import enforce_document_type_retention

    doc_type_contact.retention_days = 1
    doc_type_contact.save(update_fields=["retention_days"])
    Client.objects.filter(pk=expired_event.client_id).update(processing_restricted=True)
    result = enforce_document_type_retention(facility, timezone.now(), False)
    assert result["count"] == 0
    assert result["deferred"] == 1


def test_restriction_before_event_lock_is_rechecked(expired_event, facility, settings_obj):
    from core.retention import enforcement

    person = expired_event.client
    person.contact_stage = "identified"
    person.save(update_fields=["contact_stage"])
    real_lock = enforcement._lock_event_and_recheck_hold

    def restrict_then_lock(facility, pk):
        Client.objects.filter(pk=person.pk).update(processing_restricted=True)
        return real_lock(facility, pk)

    with patch.object(enforcement, "_lock_event_and_recheck_hold", side_effect=restrict_then_lock):
        result = process_facility_retention(facility, settings_obj, timezone.now(), False)
    expired_event.refresh_from_db()
    assert not expired_event.is_deleted
    assert expired_event.data_json == {"duration": 20}
    assert result["deferred"] == 1


def test_restriction_before_anonymization_lock_is_rechecked(expired_event, facility, settings_obj):
    expired_event.is_deleted = True
    expired_event.save(update_fields=["is_deleted"])
    manager = Client.objects
    real_lock = manager.select_for_update

    def restrict_then_lock(*args, **kwargs):
        Client.objects.filter(pk=expired_event.client_id).update(processing_restricted=True)
        return real_lock(*args, **kwargs)

    with patch.object(manager, "select_for_update", side_effect=restrict_then_lock):
        result = anonymize_clients(facility, False)
    expired_event.client.refresh_from_db()
    assert expired_event.client.anonymized_at is None
    assert result["deferred"] == 1


def test_downgrade_without_confirmation_right_is_refused(expired_event, lead_user, staff_user):
    from core.services.client.stage_downgrade import request_stage_downgrade, review_stage_downgrade

    dr = request_stage_downgrade(expired_event.client, lead_user, "Minimize information")
    with pytest.raises(ValidationError):
        review_stage_downgrade(dr, staff_user, approve=True)
    dr.refresh_from_db()
    assert dr.status == "pending"


def test_downgrade_request_is_idempotent_and_conflicts_with_other_requests(expired_event, lead_user, facility):
    from core.services.client.stage_downgrade import request_stage_downgrade

    dr = request_stage_downgrade(expired_event.client, lead_user, "Minimize information")
    assert request_stage_downgrade(expired_event.client, lead_user, "Repeated").pk == dr.pk
    assert (
        AuditLog.objects.filter(
            facility=facility, target_id=expired_event.client_id, action="stage_downgrade_requested"
        ).count()
        == 1
    )
    dr.status = "rejected"
    dr.save(update_fields=["status"])
    DeletionRequest.objects.create(
        facility=facility,
        target_type="Client",
        target_id=expired_event.client_id,
        requested_by=lead_user,
        reason="Delete",
    )
    with pytest.raises(ValidationError):
        request_stage_downgrade(expired_event.client, lead_user, "Minimize information")


def test_bulk_same_stage_and_upgrade_remain_allowed(expired_event, client_identified):
    assert Client.objects.filter(pk=expired_event.client_id).update(contact_stage="qualified") == 1
    assert Client.objects.bulk_update([expired_event.client], ["contact_stage"]) == 1
    assert Client.objects.filter(pk=client_identified.pk).update(contact_stage="qualified") == 1


def test_person_without_events_hold_is_preserved(client_identified, facility, settings_obj, lead_user):
    Client.objects.filter(pk=client_identified.pk).update(updated_at=timezone.now() - timedelta(days=4000))
    LegalHold.objects.create(
        facility=facility, target_type="Client", target_id=client_identified.pk, reason="Preserve", created_by=lead_user
    )
    assert anonymize_clients(facility, False)["count"] == 0
    client_identified.refresh_from_db()
    assert client_identified.anonymized_at is None
