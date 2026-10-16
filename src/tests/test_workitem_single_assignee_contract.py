"""Single assignments validate fresh recipients without blocking historical ones."""

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.forms.workitems import WorkItemForm
from core.models import Activity, AuditLog, User, WorkItem
from core.services.case.workitems import update_workitem, update_workitem_status

pytestmark = pytest.mark.django_db


@pytest.fixture
def item(facility, staff_user, client_identified):
    return WorkItem.objects.create(
        facility=facility, created_by=staff_user, client=client_identified, title="Single assignment"
    )


@pytest.mark.parametrize("kind", ["inactive", "foreign", "wrong_role", "super_admin", "missing"])
def test_update_rejects_invalid_new_assignment(item, staff_user, lead_user, other_facility, kind):
    if kind == "inactive":
        User.objects.filter(pk=lead_user.pk).update(is_active=False)
    elif kind == "foreign":
        User.objects.filter(pk=lead_user.pk).update(facility=other_facility)
    elif kind == "wrong_role":
        User.objects.filter(pk=lead_user.pk).update(role="observer")
    elif kind == "super_admin":
        User.objects.filter(pk=lead_user.pk).update(role=User.Role.SUPER_ADMIN)
    else:
        lead_user.pk = 99999999
    # The recipient instance deliberately keeps its previously valid state.
    before = (AuditLog.objects.count(), Activity.objects.count(), item.updated_at)
    with pytest.raises(ValidationError, match="aktiv"):
        update_workitem(item, staff_user, assigned_to=lead_user, title="Refused title")
    item.refresh_from_db()
    assert item.assigned_to_id is None
    assert item.title == "Single assignment"
    assert (AuditLog.objects.count(), Activity.objects.count(), item.updated_at) == before


@pytest.mark.parametrize("wants_json", [False, True], ids=["html", "json"])
def test_http_update_rechecks_recipient_after_form_validation(
    item, staff_user, lead_user, client, monkeypatch, wants_json
):
    from core.views import workitem_actions

    original = workitem_actions.update_workitem
    calls = []

    def deactivate_after_form(workitem, user, **fields):
        calls.append(fields["assigned_to"].pk)
        # ModelForm._post_clean has already mutated this instance.
        assert workitem.assigned_to_id == lead_user.pk
        User.objects.filter(pk=lead_user.pk).update(is_active=False)
        return original(workitem, user, **fields)

    monkeypatch.setattr(workitem_actions, "update_workitem", deactivate_after_form)
    client.force_login(staff_user)
    before = (AuditLog.objects.count(), Activity.objects.count(), item.updated_at)
    url = reverse("core:workitem_update", args=[item.pk])
    response = client.post(
        url,
        {
            "title": "Refused title",
            "item_type": "task",
            "priority": "normal",
            "recurrence": "none",
            "assigned_to": lead_user.pk,
            "expected_updated_at": item.updated_at.isoformat(),
        },
        **({"HTTP_ACCEPT": "application/json"} if wants_json else {}),
    )
    assert calls == [lead_user.pk]
    assert response.status_code == (409 if wants_json else 302)
    if not wants_json:
        assert response["Location"] == url
        assert any("aktiv" in str(message) for message in get_messages(response.wsgi_request))
    item.refresh_from_db()
    assert item.assigned_to_id is None
    assert item.title == "Single assignment"
    assert (AuditLog.objects.count(), Activity.objects.count(), item.updated_at) == before


@pytest.mark.parametrize("kind", ["inactive", "wrong_role"])
@pytest.mark.parametrize("include_assignee", [False, True], ids=["field-omitted", "same-assignee"])
def test_historical_assignment_does_not_block_edits_or_clear(item, staff_user, lead_user, kind, include_assignee):
    WorkItem.objects.filter(pk=item.pk).update(assigned_to=lead_user)
    User.objects.filter(pk=lead_user.pk).update(
        **({"is_active": False} if kind == "inactive" else {"role": "observer"})
    )
    # The stale item still has no assignee: compare against the locked DB row.
    fields = {"assigned_to": lead_user} if include_assignee else {}
    update_workitem(item, staff_user, title="Changed title", priority="urgent", **fields)
    item.refresh_from_db()
    assert item.title == "Changed title"
    assert item.priority == "urgent"
    assert item.assigned_to_id == lead_user.pk
    update_workitem_status(item, WorkItem.Status.IN_PROGRESS, staff_user)
    item.refresh_from_db()
    assert item.status == WorkItem.Status.IN_PROGRESS
    assert item.assigned_to_id == lead_user.pk
    update_workitem(item, staff_user, assigned_to=None)
    item.refresh_from_db()
    assert item.assigned_to_id is None


def test_unchanged_assignment_uses_locked_identity(item, staff_user, lead_user):
    WorkItem.objects.filter(pk=item.pk).update(assigned_to=lead_user)
    # Another assignee on the passed instance must not become the comparison baseline.
    item.assigned_to = staff_user
    User.objects.filter(pk=lead_user.pk).update(is_active=False)
    update_workitem(item, staff_user, assigned_to=lead_user, description="Historical assignment")
    item.refresh_from_db()
    assert item.assigned_to_id == lead_user.pk
    assert item.description == "Historical assignment"
    assert AuditLog.objects.filter(action=AuditLog.Action.WORKITEM_UPDATE).latest("timestamp").detail[
        "changed_fields"
    ] == ["description"]


@pytest.mark.parametrize("role", [User.Role.FACILITY_ADMIN, User.Role.LEAD, User.Role.STAFF, User.Role.ASSISTANT])
def test_active_new_recipient_is_assigned_and_audited(item, staff_user, lead_user, role):
    User.objects.filter(pk=lead_user.pk).update(role=role)
    update_workitem(item, staff_user, assigned_to=lead_user)
    item.refresh_from_db()
    assert item.assigned_to_id == lead_user.pk
    assert AuditLog.objects.filter(action=AuditLog.Action.WORKITEM_UPDATE).latest("timestamp").detail[
        "changed_fields"
    ] == ["assigned_to"]


def test_validated_form_instance_still_records_assignment_diff(item, staff_user, lead_user, facility):
    form = WorkItemForm(
        data={"title": item.title, "item_type": "task", "priority": "normal", "assigned_to": lead_user.pk},
        instance=item,
        facility=facility,
        pinned_client=item.client,
    )
    assert form.is_valid(), form.errors
    assert item.assigned_to_id == lead_user.pk
    update_workitem(item, staff_user, assigned_to=form.cleaned_data["assigned_to"])
    item.refresh_from_db()
    assert item.assigned_to_id == lead_user.pk
    assert AuditLog.objects.filter(action=AuditLog.Action.WORKITEM_UPDATE).latest("timestamp").detail[
        "changed_fields"
    ] == ["assigned_to"]


@pytest.mark.postgres
def test_single_recipient_lock_follows_client_and_workitem_locks(item, staff_user, lead_user):
    from tests._row_lock_helpers import mitschnitt

    with mitschnitt() as capture:
        update_workitem(item, staff_user, assigned_to=lead_user)
    relevant = [table for table in capture.sperrfolge() if table in {"core_client", "core_workitem", "core_user"}]
    assert relevant[0] == "core_client"
    assert relevant.index("core_client") < relevant.index("core_workitem") < relevant.index("core_user")


@pytest.mark.parametrize("kind", ["inactive", "wrong_role", "super_admin"])
@pytest.mark.parametrize("title", ["Single assignment", "Edited historical task"])
def test_edit_form_preserves_historical_assignment(client, item, staff_user, lead_user, kind, title):
    WorkItem.objects.filter(pk=item.pk).update(assigned_to=lead_user)
    User.objects.filter(pk=lead_user.pk).update(
        **(
            {"is_active": False}
            if kind == "inactive"
            else {"role": "observer" if kind == "wrong_role" else User.Role.SUPER_ADMIN}
        )
    )
    client.force_login(staff_user)
    url = reverse("core:workitem_update", args=[item.pk])
    response = client.get(url)
    assert response.status_code == 200
    choices = dict(response.context["form"].fields["assigned_to"].choices)
    assert str(lead_user.pk) in {str(value) for value in choices}
    assert f"{lead_user} (aktuell)" in str(response.context["form"]["assigned_to"])
    response = client.post(
        url,
        {
            "title": title,
            "item_type": "task",
            "priority": "normal",
            "assigned_to": lead_user.pk,
            "expected_updated_at": item.updated_at.isoformat(),
        },
    )
    assert response.status_code == 302
    item.refresh_from_db()
    assert item.title == title
    assert item.priority == "normal"
    assert item.assigned_to_id == lead_user.pk


@pytest.mark.parametrize("kind", ["inactive", "foreign"])
def test_edit_form_rejects_other_unassignable_recipient(
    client, item, staff_user, lead_user, facility, other_facility, kind
):
    WorkItem.objects.filter(pk=item.pk).update(assigned_to=lead_user)
    User.objects.filter(pk=lead_user.pk).update(is_active=False)
    recipient = User.objects.create(
        username="other-recipient",
        facility=other_facility if kind == "foreign" else facility,
        is_active=kind == "foreign",
        role=User.Role.STAFF,
    )
    client.force_login(staff_user)
    url = reverse("core:workitem_update", args=[item.pk])
    response = client.get(url)
    assert recipient.pk not in response.context["form"].fields["assigned_to"].queryset.values_list("pk", flat=True)
    response = client.post(
        url,
        {"title": "Refused edit", "item_type": "task", "priority": "normal", "assigned_to": recipient.pk},
    )
    assert response.status_code == 200
    assert "assigned_to" in response.context["form"].errors
    item.refresh_from_db()
    assert item.title == "Single assignment"
    assert item.assigned_to_id == lead_user.pk


def test_edit_form_excludes_foreign_historical_recipient(client, item, staff_user, lead_user, other_facility):
    WorkItem.objects.filter(pk=item.pk).update(assigned_to=lead_user)
    User.objects.filter(pk=lead_user.pk).update(facility=other_facility)
    client.force_login(staff_user)
    url = reverse("core:workitem_update", args=[item.pk])
    response = client.get(url)
    assert lead_user.pk not in response.context["form"].fields["assigned_to"].queryset.values_list("pk", flat=True)
    response = client.post(
        url,
        {"title": "Refused edit", "item_type": "task", "priority": "normal", "assigned_to": lead_user.pk},
    )
    assert response.status_code == 200
    assert "assigned_to" in response.context["form"].errors
    item.refresh_from_db()
    assert item.title == "Single assignment"


def test_create_form_excludes_and_rejects_inactive_recipient(client, staff_user, lead_user):
    User.objects.filter(pk=lead_user.pk).update(is_active=False)
    client.force_login(staff_user)
    url = reverse("core:workitem_create")
    response = client.get(url)
    assert lead_user.pk not in response.context["form"].fields["assigned_to"].queryset.values_list("pk", flat=True)
    response = client.post(
        url,
        {"title": "Refused creation", "item_type": "task", "priority": "normal", "assigned_to": lead_user.pk},
    )
    assert response.status_code == 200
    assert "assigned_to" in response.context["form"].errors
    assert not WorkItem.objects.filter(title="Refused creation").exists()
