"""The focus box group labels follow the language of the request, not the language at import time."""

from datetime import timedelta

import pytest
from django.utils import timezone, translation

from core.models import WorkItem
from core.services.dashboard import build_focus_box


@pytest.mark.django_db
def test_group_labels_are_translated_when_rendered_in_english(facility, staff_user):
    today = timezone.localdate()
    common = {"facility": facility, "created_by": staff_user, "item_type": WorkItem.ItemType.TASK}
    WorkItem.objects.create(title="a", status=WorkItem.Status.OPEN, due_date=today - timedelta(days=1), **common)
    WorkItem.objects.create(title="b", status=WorkItem.Status.OPEN, due_date=today, **common)
    WorkItem.objects.create(title="c", status=WorkItem.Status.OPEN, priority=WorkItem.Priority.URGENT, **common)
    WorkItem.objects.create(title="d", status=WorkItem.Status.IN_PROGRESS, **common)

    box = build_focus_box(facility)

    with translation.override("en"):
        labels = {group["key"]: str(group["label"]) for group in box["groups"]}
    assert labels == {
        "overdue": "Overdue",
        "today": "Due today",
        "priority": "Urgent · Important",
        "in_progress": "In progress",
    }
