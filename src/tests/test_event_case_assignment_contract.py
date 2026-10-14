"""Event creation and its picker enforce the same case assignment boundary."""

from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from core.models import Activity, AuditLog, Case, DocumentType, Event, EventHistory
from core.services.events import create_event
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db


def _payload(case, document_type):
    return {
        "client": str(case.client_id),
        "case": str(case.pk),
        "document_type": str(document_type.pk),
        "occurred_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
        "dauer": "15",
    }


def _set_case_type(case, sensitivity):
    if sensitivity is not None:
        case.document_type = DocumentType.objects.create(
            facility=case.facility,
            name="Case type",
            entity_type=DocumentType.EntityType.CASE,
            sensitivity=sensitivity,
        )
        case.save()


@pytest.mark.parametrize("variant", ["assistant", "hidden_case", "closed_after_validation"])
@pytest.mark.parametrize("raw_json", [False, True])
def test_create_rejects_invalid_case_without_losing_input(
    client, staff_user, assistant_user, case_open, doc_type_contact, variant, raw_json
):
    if variant == "hidden_case":
        _set_case_type(case_open, DocumentType.Sensitivity.HIGH)
    client.force_login(assistant_user if variant == "assistant" else staff_user)

    def write(**kwargs):
        if variant == "closed_after_validation":
            Case.objects.filter(pk=case_open.pk).update(status=Case.Status.CLOSED)
        return create_event(**kwargs)

    with patch("core.views.events.create_event", side_effect=write):
        response = client.post(
            reverse("core:event_create"),
            _payload(case_open, doc_type_contact),
            headers={"Accept": "application/json"} if raw_json else {},
        )
    assert response.status_code == (422 if raw_json else 200)
    assert not Event.objects.exists()
    if raw_json:
        assert "case" in response.json()["errors"]
    else:
        assert response.context["meta_form"].errors["case"]
        assert response.context["data_form"]["dauer"].value() == "15"
        assert response.context["meta_form"]["client"].value() == str(case_open.client_id)
        assert "Die Fallauswahl wurde zurückgesetzt" in response.content.decode()
        assert 'id="id_case-error"' in response.content.decode()


@pytest.mark.parametrize("variant", ["assistant", "hidden_case", "closed", "changed_type"])
def test_service_rechecks_assignment_under_the_existing_case_lock(
    facility, staff_user, assistant_user, case_open, doc_type_contact, variant
):
    if variant in {"hidden_case", "changed_type"}:
        _set_case_type(case_open, DocumentType.Sensitivity.HIGH)
    if variant == "changed_type":
        stale = Case.objects.get(pk=case_open.pk)
        stale.document_type = None
    else:
        stale = case_open
    if variant == "closed":
        Case.objects.filter(pk=case_open.pk).update(status=Case.Status.CLOSED)
    before = (Event.objects.count(), EventHistory.objects.count(), Activity.objects.count(), AuditLog.objects.count())
    with mitschnitt() as recording, pytest.raises(ValidationError) as error:
        create_event(
            facility=facility,
            user=assistant_user if variant == "assistant" else staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
            client=case_open.client,
            case=stale,
        )
    assert "case" in error.value.message_dict
    assert before == (
        Event.objects.count(),
        EventHistory.objects.count(),
        Activity.objects.count(),
        AuditLog.objects.count(),
    )
    assert recording.sperrfolge() == ["core_client", "core_case"]


@pytest.mark.parametrize(
    "role,sensitivity,allowed",
    [
        ("staff_user", None, True),
        ("staff_user", DocumentType.Sensitivity.NORMAL, True),
        ("staff_user", DocumentType.Sensitivity.HIGH, False),
        ("lead_user", DocumentType.Sensitivity.HIGH, True),
        ("admin_user", DocumentType.Sensitivity.HIGH, True),
    ],
)
def test_picker_and_create_share_case_visibility(
    client, request, case_open, doc_type_contact, role, sensitivity, allowed
):
    _set_case_type(case_open, sensitivity)
    client.force_login(request.getfixturevalue(role))
    picker = client.get(reverse("core:cases_for_client"), {"client": case_open.client_id})
    assert picker.status_code == 200
    assert (str(case_open.pk) in {case["id"] for case in picker.json()}) is allowed
    response = client.post(reverse("core:event_create"), _payload(case_open, doc_type_contact))
    assert response.status_code == (302 if allowed else 200)
    assert Event.objects.filter(case=case_open).exists() is allowed


def test_assistant_can_still_create_without_a_case(client, assistant_user, case_open, doc_type_contact):
    client.force_login(assistant_user)
    payload = _payload(case_open, doc_type_contact)
    payload["case"] = ""
    response = client.post(reverse("core:event_create"), payload)
    assert response.status_code == 302
    assert Event.objects.get().case_id is None


def test_dynamic_case_slug_error_does_not_reset_assignment(client, staff_user, case_open, doc_type_contact):
    """A dynamic field named case must not be mistaken for a metadata error."""
    client.force_login(staff_user)
    with patch("core.views.events.create_event", side_effect=ValidationError({"case": "Rejected"})):
        response = client.post(reverse("core:event_create"), _payload(case_open, doc_type_contact))
    assert response.status_code == 200
    assert not response.context["meta_form"].has_error("case")
    assert response.context["meta_form"]["case"].value() == str(case_open.pk)
    assert response.context["data_form"].non_field_errors() == ["Ungültige Eingabe."]
