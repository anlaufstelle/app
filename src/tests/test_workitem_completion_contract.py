"""Completion windows use the completion clock, including OOB responses."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import WorkItem
from core.services.case.workitems import (
    bulk_assign_workitems,
    bulk_update_workitem_priority,
    update_workitem,
    update_workitem_status,
)

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("status", ["done", "dismissed"])
@pytest.mark.parametrize("days,expected", [(8, False), (2, True), (None, False)])
def test_completion_clock_controls_full_widget_and_oob(client, staff_user, facility, status, days, expected):
    now = timezone.now()
    item = WorkItem.objects.create(facility=facility, created_by=staff_user, title="Completion clock", status=status)
    WorkItem.objects.filter(pk=item.pk).update(
        completed_at=now - timedelta(days=days) if days is not None else None,
        updated_at=now if days != 2 else now - timedelta(days=20),
    )
    client.force_login(staff_user)
    url = reverse("core:workitem_inbox")
    assert (item in client.get(url).context["done_items"]) is expected
    assert (item in client.get(url, {"status": "done", "done_period": "7"}).context["done_full_items"]) is expected
    assert (item in client.get(url, {"status": "done", "done_period": "30"}).context["done_full_items"]) is (
        days is not None
    )
    assert item in client.get(url, {"status": "done", "done_period": "all"}).context["done_full_items"]
    response = client.post(
        reverse("core:workitem_status_update", args=[item.pk]),
        {
            "status": status,
            "hide": "1",
            "recent_done_target": "1",
        },
        HTTP_HX_REQUEST="true",
    )
    assert ("afterbegin:#recent-done-items" in response.content.decode()) is expected


def test_edits_do_not_move_completion_window_and_reopen_resets_clock(client, staff_user, lead_user, facility):
    item = WorkItem.objects.create(facility=facility, created_by=staff_user, title="Old task", status="done")
    old = timezone.now() - timedelta(days=40)
    WorkItem.objects.filter(pk=item.pk).update(completed_at=old)
    item.refresh_from_db()
    update_workitem(item, staff_user, title="Corrected task")
    update_workitem(item, staff_user, title="Corrected task")
    bulk_update_workitem_priority([item], staff_user, "urgent")
    bulk_assign_workitems([item], staff_user, lead_user)
    item.refresh_from_db()
    assert item.completed_at == old
    client.force_login(staff_user)
    assert (
        item
        not in client.get(reverse("core:workitem_inbox"), {"status": "done", "done_period": "30"}).context[
            "done_full_items"
        ]
    )
    update_workitem_status(item, "open", staff_user)
    item.refresh_from_db()
    assert item.completed_at is None
    update_workitem_status(item, "dismissed", staff_user)
    item.refresh_from_db()
    assert item.completed_at > old
