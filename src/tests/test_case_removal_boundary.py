"""Case removal uses the same role and status boundary as assignment."""

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, Case, DocumentType, Event
from core.services.case import assign_event_to_case, remove_event_from_case
from tests._factories import make_document_type

pytestmark = pytest.mark.django_db


@pytest.fixture
def linked_case(case_open, sample_event, facility, request):
    boundary = request.param
    Event.objects.filter(pk=sample_event.pk).update(case=case_open)
    if boundary == "closed":
        Case.objects.filter(pk=case_open.pk).update(status=Case.Status.CLOSED, closed_at=timezone.now())
    elif boundary == "high":
        case_type = make_document_type(
            facility=facility,
            name="Restricted case",
            entity_type=DocumentType.EntityType.CASE,
            sensitivity=DocumentType.Sensitivity.HIGH,
        )
        Case.objects.filter(pk=case_open.pk).update(document_type=case_type)
    # Keep the caller's instances stale to exercise the state read under lock.
    return case_open


@pytest.mark.parametrize("linked_case", ["closed", "high"], indirect=True)
@pytest.mark.parametrize("path", ["service", "view", "reassign"])
def test_removal_rejects_unassignable_case(
    linked_case, sample_event, staff_user, client, facility, client_identified, path
):
    if path == "view":
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_remove_event", kwargs={"pk": linked_case.pk, "event_pk": sample_event.pk}),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 409
        assert "Der Fall ist nicht mehr verfügbar oder nicht zugänglich." in response.content.decode()
    else:
        with pytest.raises(ValidationError, match="Der Fall ist nicht mehr verfügbar oder nicht zugänglich"):
            if path == "reassign":
                target = Case.objects.create(
                    facility=facility, client=client_identified, created_by=staff_user, title="Target case"
                )
                assign_event_to_case(target, sample_event, staff_user)
            else:
                remove_event_from_case(sample_event, staff_user, expected_case=linked_case)
    sample_event.refresh_from_db()
    assert sample_event.case_id == linked_case.pk
    assert not AuditLog.objects.filter(
        action__in=[AuditLog.Action.CASE_EVENT_REMOVED, AuditLog.Action.CASE_EVENT_ASSIGNED]
    ).exists()


@pytest.mark.parametrize("linked_case", ["closed"], indirect=True)
@pytest.mark.parametrize("actor", ["lead_user", "admin_user", "assistant_user"])
def test_removal_rejects_closed_case_for_every_role(linked_case, sample_event, request, actor):
    user = request.getfixturevalue(actor)
    with pytest.raises(ValidationError, match="Der Fall ist nicht mehr verfügbar oder nicht zugänglich"):
        remove_event_from_case(sample_event, user)
    sample_event.refresh_from_db()
    assert sample_event.case_id == linked_case.pk
    assert not AuditLog.objects.filter(action=AuditLog.Action.CASE_EVENT_REMOVED).exists()


@pytest.mark.parametrize(
    ("linked_case", "actor"), [("open", "staff_user"), ("high", "lead_user")], indirect=["linked_case"]
)
def test_removal_allows_assignable_case_with_audit(linked_case, sample_event, request, actor):
    user = request.getfixturevalue(actor)
    remove_event_from_case(sample_event, user)
    sample_event.refresh_from_db()
    assert sample_event.case_id is None
    log = AuditLog.objects.get(action=AuditLog.Action.CASE_EVENT_REMOVED)
    assert log.user == user
    assert log.target_id == str(sample_event.pk)
    assert log.detail == {"case_id": str(linked_case.pk)}


@pytest.mark.parametrize(
    ("linked_case", "actor", "allowed"),
    [
        ("open", "staff_user", True),
        ("high", "staff_user", False),
        ("closed", "staff_user", False),
        ("high", "lead_user", True),
        ("closed", "lead_user", False),
    ],
    indirect=["linked_case"],
)
def test_case_detail_removal_button_respects_boundary(linked_case, sample_event, request, actor, allowed, client):
    client.force_login(request.getfixturevalue(actor))
    response = client.get(reverse("core:case_detail", kwargs={"pk": linked_case.pk}))
    assert response.status_code == 200
    removal_url = reverse("core:case_remove_event", kwargs={"pk": linked_case.pk, "event_pk": sample_event.pk})
    assert (removal_url in response.content.decode()) == allowed
