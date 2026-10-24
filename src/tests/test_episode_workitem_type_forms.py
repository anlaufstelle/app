"""HTTP contracts for episode and task document type selection."""

from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from core.models import DocumentType, Episode, WorkItem
from core.services.case.episodes import episode_basis_token
from core.services.case.workitems import workitem_form_state_token


@pytest.fixture(params=["workitem", "episode"])
def endpoint(request, client, staff_user, case_open):
    kind = request.param
    client.force_login(staff_user)
    kwargs = {"case_pk": case_open.pk} if kind == "episode" else {}
    payload = {"title": "Type selection", "started_at": timezone.localdate().isoformat()}
    if kind == "workitem":
        payload |= {"item_type": WorkItem.ItemType.TASK, "priority": WorkItem.Priority.NORMAL}
    model = Episode if kind == "episode" else WorkItem
    return kind, reverse(f"core:{kind}_create", kwargs=kwargs), payload, model


def make_type(facility, kind, name="Selectable", **kwargs):
    return DocumentType.objects.create(facility=facility, entity_type=kind, name=name, **kwargs)


@pytest.mark.django_db
def test_multiple_types_require_selection_and_accept_selected_type(client, facility, endpoint):
    kind, url, payload, model = endpoint
    make_type(facility, kind)
    selected = make_type(facility, kind, name="Alternative")
    response = client.post(url, payload)
    assert response.status_code in (200, 422)
    assert "document_type" in response.context["form"].errors
    assert not model.objects.filter(title=payload["title"]).exists()
    response = client.post(url, payload | {"document_type": str(selected.pk)})
    assert response.status_code == 302
    assert model.objects.get(title=payload["title"]).document_type == selected


@pytest.mark.django_db
@pytest.mark.parametrize("count", [0, 1, 2])
def test_type_choices_default_and_requirement(client, facility, second_facility, endpoint, count):
    kind, url, _payload, _model = endpoint
    active = [make_type(facility, kind, name=f"Active {index}") for index in range(count)]
    make_type(facility, kind, name="Inactive", is_active=False)
    make_type(facility, "event", name="Other entity")
    make_type(second_facility, kind, name="Other facility")
    response = client.get(url)
    assert response.status_code == 200
    field = response.context["form"]["document_type"]
    assert set(field.field.queryset) == set(active)
    assert field.field.required is bool(count)
    assert field.value() == (active[0].pk if count == 1 else None)
    assert 'name="document_type"' in response.content.decode()
    assert not any(key.startswith("hx-") for key in field.field.widget.attrs)


@pytest.mark.django_db
@pytest.mark.parametrize("invalid", ["entity", "facility", "inactive"])
def test_create_rejects_unavailable_type(client, facility, second_facility, endpoint, invalid):
    kind, url, payload, model = endpoint
    wrong = make_type(
        second_facility if invalid == "facility" else facility,
        "event" if invalid == "entity" else kind,
        is_active=invalid != "inactive",
    )
    response = client.post(url, payload | {"document_type": str(wrong.pk)})
    assert response.status_code in (200, 422)
    assert "document_type" in response.context["form"].errors
    assert not model.objects.filter(title=payload["title"]).exists()


@pytest.mark.django_db
def test_edit_keeps_assigned_inactive_type(client, facility, staff_user, case_open, endpoint):
    kind, _url, payload, model = endpoint
    assigned = make_type(facility, kind, is_active=False)
    active = make_type(facility, kind, name="Active")
    kwargs = {"case": case_open, "started_at": timezone.localdate()} if kind == "episode" else {"facility": facility}
    record = model.objects.create(created_by=staff_user, document_type=assigned, title="Existing", **kwargs)
    url_kwargs = {"pk": record.pk} | ({"case_pk": case_open.pk} if kind == "episode" else {})
    url = reverse(f"core:{kind}_update", kwargs=url_kwargs)
    response = client.get(url)
    field = response.context["form"]["document_type"]
    assert set(field.field.queryset) == {assigned, active}
    assert field.value() == assigned.pk
    if kind == "episode":
        payload |= {"basis_token": episode_basis_token(record), "started_at": record.started_at.isoformat()}
    else:
        payload |= {
            "expected_updated_at": record.updated_at.isoformat(),
            "expected_form_state": workitem_form_state_token(record),
        }
    response = client.post(url, payload | {"document_type": str(assigned.pk)})
    assert response.status_code == 302
    record.refresh_from_db()
    assert record.document_type == assigned
    assert record.title == payload["title"]


@pytest.mark.django_db
@pytest.mark.parametrize("error_field", ["document_type", "unavailable_field"])
def test_service_field_errors_render_safely(client, endpoint, error_field):
    kind, url, payload, _model = endpoint
    module = "case_episodes" if kind == "episode" else "workitem_actions"
    with patch(
        f"core.views.{module}.create_{kind}",
        side_effect=ValidationError({error_field: "Selection changed"}),
    ):
        response = client.post(url, payload)
    assert response.status_code == 200
    form = response.context["form"]
    assert form.has_error("document_type" if error_field == "document_type" else "__all__")


@pytest.mark.django_db
def test_task_offline_create_returns_validation_response(client, facility, staff_user):
    client.force_login(staff_user)
    make_type(facility, "workitem")
    make_type(facility, "workitem", name="Alternative")
    payload = {"title": "Offline task", "item_type": WorkItem.ItemType.TASK, "priority": WorkItem.Priority.NORMAL}
    response = client.post(reverse("core:workitem_create"), payload, HTTP_ACCEPT="application/json")
    assert response.status_code == 422
    assert "document_type" in response.json()["errors"]
