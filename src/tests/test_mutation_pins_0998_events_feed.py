"""Pins for readable task activity in the public feed.

Source: mutation delta of the release-0.99.8 checkpoint, 1 mutant.
Groups: task facility scope (1).
"""

import pytest
from django.contrib.contenttypes.models import ContentType

from core.models import Activity
from core.services.events.feed import build_feed_items

pytestmark = pytest.mark.django_db


def test_readable_task_activity_remains_in_feed(facility, staff_user, sample_workitem):
    """The activity feed retains an update to a readable task in its facility.

    x_activities_for_feed_read__mutmut_37.
    """
    from django.utils import timezone

    activity = Activity.objects.create(
        facility=facility,
        actor=staff_user,
        verb=Activity.Verb.UPDATED,
        target_type=ContentType.objects.get_for_model(sample_workitem),
        target_id=sample_workitem.pk,
    )
    items = build_feed_items(facility, timezone.localdate(activity.occurred_at), "activities", user=staff_user)
    assert activity.pk in {item["object"].pk for item in items}
