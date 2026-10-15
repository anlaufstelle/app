"""Fresh recipient validation for whole work item batches."""

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.forms.workitems import WorkItemForm
from core.models import AuditLog, User, WorkItem
from core.services.case.workitems import bulk_assign_workitems
from tests.test_workitem_filter_contract import Controls

pytestmark = pytest.mark.django_db


@pytest.fixture
def items(facility, staff_user):
    return [WorkItem.objects.create(facility=facility, created_by=staff_user, title=f"Task {n}") for n in range(2)]


@pytest.mark.parametrize("kind", ["inactive", "foreign", "super_admin", "missing"])
def test_service_rejects_invalid_recipient_for_whole_batch(items, staff_user, lead_user, other_facility, kind):
    if kind == "inactive":
        User.objects.filter(pk=lead_user.pk).update(is_active=False)
    elif kind == "foreign":
        User.objects.filter(pk=lead_user.pk).update(facility=other_facility)
    elif kind == "super_admin":
        User.objects.filter(pk=lead_user.pk).update(role=User.Role.SUPER_ADMIN)
    else:
        lead_user.pk = 99999999
    before = AuditLog.objects.count()
    with pytest.raises(ValidationError):
        bulk_assign_workitems(items, staff_user, lead_user)
    assert not WorkItem.objects.filter(pk__in=[item.pk for item in items], assigned_to__isnull=False).exists()
    assert AuditLog.objects.count() == before


def test_inbox_assignment_and_form_share_active_recipient_set(client, staff_user, lead_user, facility):
    User.objects.filter(pk=lead_user.pk).update(is_active=False)
    User.objects.create(username="system-recipient", facility=facility, role=User.Role.SUPER_ADMIN)
    client.force_login(staff_user)
    response = client.get(reverse("core:workitem_inbox"))
    form_ids = set(WorkItemForm(facility=facility).fields["assigned_to"].queryset.values_list("pk", flat=True))
    assert set(response.context["assignable_users"].values_list("pk", flat=True)) == form_ids
    assert lead_user.pk in set(response.context["facility_users"].values_list("pk", flat=True))
    controls = Controls(response.content.decode())
    inside = False
    option_ids = []
    # Options of the final assignment select follow all other selects.
    for tag, attrs in controls.elements:
        if tag == "select":
            inside = attrs.get("id") == "bulk-assign"
        if inside and tag == "option" and attrs.get("value"):
            option_ids.append(int(attrs["value"]))
    assert set(option_ids) == form_ids


def test_deactivation_between_view_lookup_and_service_is_refused(client, staff_user, lead_user, items, monkeypatch):
    from core.views import workitem_bulk

    original = workitem_bulk.bulk_assign_workitems

    def deactivate_before_service(workitems, actor, recipient):
        User.objects.filter(pk=recipient.pk).update(is_active=False)
        return original(workitems, actor, recipient)

    monkeypatch.setattr(workitem_bulk, "bulk_assign_workitems", deactivate_before_service)
    client.force_login(staff_user)
    response = client.post(
        reverse("core:workitem_bulk_assign"),
        {
            "workitem_ids": [str(item.pk) for item in items],
            "assigned_to": str(lead_user.pk),
        },
    )
    assert response.status_code == 302
    assert any("aktiv" in str(message) for message in get_messages(response.wsgi_request))
    assert not WorkItem.objects.filter(pk__in=[item.pk for item in items], assigned_to__isnull=False).exists()


@pytest.mark.parametrize("kind", ["inactive", "foreign", "super_admin", "missing"])
def test_post_rejects_invalid_recipient_without_partial_assignment(
    client, staff_user, lead_user, items, other_facility, kind
):
    if kind == "inactive":
        User.objects.filter(pk=lead_user.pk).update(is_active=False)
    elif kind == "foreign":
        User.objects.filter(pk=lead_user.pk).update(facility=other_facility)
    elif kind == "super_admin":
        User.objects.filter(pk=lead_user.pk).update(role=User.Role.SUPER_ADMIN)
    else:
        lead_user.pk = 99999999
    client.force_login(staff_user)
    response = client.post(
        reverse("core:workitem_bulk_assign"),
        {
            "workitem_ids": [str(item.pk) for item in items],
            "assigned_to": str(lead_user.pk),
        },
    )
    assert response.status_code == 302
    assert list(get_messages(response.wsgi_request))
    assert not WorkItem.objects.filter(pk__in=[item.pk for item in items], assigned_to__isnull=False).exists()


def test_historical_inactive_assignment_does_not_block_other_actions(client, staff_user, lead_user, items):
    WorkItem.objects.filter(pk__in=[item.pk for item in items]).update(assigned_to=lead_user)
    User.objects.filter(pk=lead_user.pk).update(is_active=False)
    client.force_login(staff_user)
    for route, values in [
        ("core:workitem_bulk_priority", {"priority": "urgent"}),
        ("core:workitem_bulk_status", {"status": "in_progress"}),
        ("core:workitem_bulk_assign", {"assigned_to": ""}),
    ]:
        response = client.post(reverse(route), {"workitem_ids": [str(item.pk) for item in items], **values})
        assert response.status_code == 302
    for item in items:
        item.refresh_from_db()
        assert item.assigned_to is None
        assert item.priority == "urgent"
        assert item.status == "in_progress"


@pytest.mark.parametrize("kind", ["deleted_task", "deleted_client", "inactive_client", "restricted_client"])
def test_freshly_hidden_or_locked_item_refuses_whole_post(
    client, staff_user, items, client_identified, kind, monkeypatch
):
    from core.views.workitem_bulk import _BulkActionMixin

    WorkItem.objects.filter(pk=items[1].pk).update(client=client_identified)
    original = _BulkActionMixin._load_workitems

    def revoke_after_lookup(self, request, ids):
        loaded = original(self, request, ids)
        # Refs #2585: the service must catch revocation after the read boundary.
        if kind == "deleted_task":
            WorkItem.objects.filter(pk=items[1].pk).update(is_deleted=True)
        elif kind == "deleted_client":
            type(client_identified).objects.filter(pk=client_identified.pk).update(is_deleted=True)
        elif kind == "inactive_client":
            type(client_identified).objects.filter(pk=client_identified.pk).update(is_active=False)
        else:
            type(client_identified).objects.filter(pk=client_identified.pk).update(processing_restricted=True)
        return loaded

    monkeypatch.setattr(_BulkActionMixin, "_load_workitems", revoke_after_lookup)
    client.force_login(staff_user)
    response = client.post(
        reverse("core:workitem_bulk_priority"),
        {
            "workitem_ids": [str(item.pk) for item in items],
            "priority": "urgent",
        },
    )
    assert response.status_code == 302
    assert list(get_messages(response.wsgi_request))
    assert not WorkItem.objects.filter(pk__in=[item.pk for item in items], priority="urgent").exists()


@pytest.mark.postgres
def test_recipient_lock_follows_client_and_workitem_locks(items, staff_user, lead_user, client_identified):
    from tests._row_lock_helpers import mitschnitt

    WorkItem.objects.filter(pk__in=[item.pk for item in items]).update(client=client_identified)
    with mitschnitt() as capture:
        assert bulk_assign_workitems(items, staff_user, lead_user) == 2
    relevant = [table for table in capture.sperrfolge() if table in {"core_user", "core_client", "core_workitem"}]
    assert relevant[0] == "core_client"
    assert relevant.index("core_client") < relevant.index("core_workitem") < relevant.index("core_user")
