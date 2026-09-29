"""Work item permission is decided again at the locked row.

The edit view and the bulk views check ``can_user_mutate_workitem`` on rows
read without a lock. When the assignment or the creator changes between that
check and the write, the request must end exactly like a request that lacked
the permission from the start: same response, nothing written, no audit row.

The race is staged deterministically: the change runs right after the first
row lock on ``core_client``, i.e. before the work item row is locked — the
order a concurrent writer that commits just before the lock would produce.
"""

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from core.models import AuditLog, WorkItem
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

EDIT_PAYLOAD = {"item_type": "task", "title": "Changed title", "priority": "urgent"}

BULK_ACTIONS = [
    pytest.param("core:workitem_bulk_status", {"status": WorkItem.Status.IN_PROGRESS}, id="status"),
    pytest.param("core:workitem_bulk_priority", {"priority": WorkItem.Priority.URGENT}, id="priority"),
    pytest.param("core:workitem_bulk_assign", None, id="assign"),
]

LOSS_KINDS = ["reassigned", "creator_changed"]


def _mutable_workitem(kind, facility, person, staff_user, lead_user, title):
    """A work item ``staff_user`` may change — through the rule ``kind`` takes away."""
    if kind == "reassigned":
        # Team task: nobody assigned, so any staff member may change it.
        return WorkItem.objects.create(facility=facility, client=person, created_by=lead_user, title=title)
    # Assigned to someone else, but created by ``staff_user``.
    return WorkItem.objects.create(
        facility=facility, client=person, created_by=staff_user, assigned_to=lead_user, title=title
    )


def _take_permission_away(kind, workitem, lead_user):
    if kind == "reassigned":
        return lambda: WorkItem.objects.filter(pk=workitem.pk).update(assigned_to=lead_user)
    return lambda: WorkItem.objects.filter(pk=workitem.pk).update(created_by=lead_user)


def _state(items):
    return [
        WorkItem.objects.filter(pk=item.pk).values_list("title", "status", "priority", "assigned_to_id").get()
        for item in items
    ]


def _messages(response):
    return [(msg.level, str(msg)) for msg in get_messages(response.wsgi_request)]


# --------------------------------------------------------------------------
# Edit view
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kind", LOSS_KINDS)
def test_edit_is_refused_when_permission_is_lost_before_the_row_lock(
    client, facility, client_identified, staff_user, lead_user, kind
):
    foreign = WorkItem.objects.create(
        facility=facility, client=client_identified, created_by=lead_user, assigned_to=lead_user, title="Foreign"
    )
    workitem = _mutable_workitem(kind, facility, client_identified, staff_user, lead_user, "Original")
    client.force_login(staff_user)
    without_permission = client.post(reverse("core:workitem_update", args=[foreign.pk]), EDIT_PAYLOAD)
    before = _state([workitem])
    audit_before = AuditLog.objects.count()

    with mitschnitt() as m:
        m.nach_sperre("core_client", _take_permission_away(kind, workitem, lead_user))
        response = client.post(reverse("core:workitem_update", args=[workitem.pk]), EDIT_PAYLOAD)

    assert m.offene_schranken() == [], "the race was not staged"
    assert without_permission.status_code == 403
    assert (response.status_code, response.content) == (without_permission.status_code, without_permission.content)
    assert _state([workitem]) == before
    assert AuditLog.objects.count() == audit_before


def test_edit_with_unchanged_permission_still_writes(client, facility, client_identified, staff_user, lead_user):
    workitem = _mutable_workitem("reassigned", facility, client_identified, staff_user, lead_user, "Original")
    client.force_login(staff_user)
    audit_before = AuditLog.objects.filter(action=AuditLog.Action.WORKITEM_UPDATE).count()

    response = client.post(reverse("core:workitem_update", args=[workitem.pk]), EDIT_PAYLOAD)

    assert response.status_code == 302
    assert WorkItem.objects.get(pk=workitem.pk).title == "Changed title"
    assert AuditLog.objects.filter(action=AuditLog.Action.WORKITEM_UPDATE).count() == audit_before + 1


# --------------------------------------------------------------------------
# Bulk actions
# --------------------------------------------------------------------------


def _bulk_payload(data, items, staff_user):
    payload = {"assigned_to": str(staff_user.pk)} if data is None else dict(data)
    payload["workitem_ids"] = [str(item.pk) for item in items]
    return payload


@pytest.mark.parametrize("kind", LOSS_KINDS)
@pytest.mark.parametrize(("url", "data"), BULK_ACTIONS)
def test_bulk_is_refused_when_permission_is_lost_before_the_row_lock(
    client, facility, client_identified, staff_user, lead_user, kind, url, data
):
    own = WorkItem.objects.create(facility=facility, client=client_identified, created_by=staff_user, title="Own")
    workitem = _mutable_workitem(kind, facility, client_identified, staff_user, lead_user, "Team")
    items = [own, workitem]
    payload = _bulk_payload(data, items, staff_user)
    client.force_login(staff_user)

    # Reference: the same selection with the permission already missing.
    before = _state(items)
    ownership = WorkItem.objects.filter(pk=workitem.pk).values("created_by_id", "assigned_to_id").get()
    _take_permission_away(kind, workitem, lead_user)()
    without_permission = client.post(reverse(url), payload)
    reference = (without_permission.status_code, without_permission["Location"], _messages(without_permission))
    client.get(without_permission["Location"])  # shows, and thereby consumes, the reference message
    WorkItem.objects.filter(pk=workitem.pk).update(**ownership)
    assert _state(items) == before
    audit_before = AuditLog.objects.count()

    with mitschnitt() as m:
        m.nach_sperre("core_client", _take_permission_away(kind, workitem, lead_user))
        response = client.post(reverse(url), payload)

    assert m.offene_schranken() == [], "the race was not staged"
    assert f"forbidden={workitem.pk}" in reference[1]
    assert (response.status_code, response["Location"], _messages(response)) == reference
    assert _state(items) == before
    assert AuditLog.objects.count() == audit_before


@pytest.mark.parametrize(("url", "data"), BULK_ACTIONS)
def test_bulk_with_unchanged_permission_still_writes(
    client, facility, client_identified, staff_user, lead_user, url, data
):
    own = WorkItem.objects.create(facility=facility, client=client_identified, created_by=staff_user, title="Own")
    team = _mutable_workitem("reassigned", facility, client_identified, staff_user, lead_user, "Team")
    items = [own, team]
    before = _state(items)
    client.force_login(staff_user)

    response = client.post(reverse(url), _bulk_payload(data, items, staff_user))

    assert response.status_code == 302
    assert "forbidden=" not in response["Location"]
    after = _state(items)
    assert all(a != b for a, b in zip(after, before, strict=True)), (before, after)
