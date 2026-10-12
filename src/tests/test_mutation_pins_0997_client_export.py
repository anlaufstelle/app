"""Pins for the public personal data export.

Source: mutation delta of the release-0.99.7 checkpoint, 1 mutant.
Groups: newest work items precede older items regardless of UUID order (1).
"""

from datetime import timedelta
from uuid import UUID

import pytest
from django.utils import timezone

from core.models import WorkItem
from core.services.client.export import export_client_data

pytestmark = pytest.mark.django_db


def test_work_items_are_newest_first_even_with_opposite_uuid_order(facility, client_identified, lead_user):
    """Export orders by creation time before UUID: x__gather_workitems__mutmut_4."""
    now = timezone.now()
    for number, title, created_at in [(1, "Older", now - timedelta(days=2)), (2, "Newest", now)]:
        item = WorkItem.objects.create(
            pk=UUID(int=number), facility=facility, client=client_identified, title=title, created_by=lead_user
        )
        WorkItem.objects.filter(pk=item.pk).update(created_at=created_at)

    exported = export_client_data(client_identified, facility, lead_user)

    assert [item["title"] for item in exported["work_items"]] == ["Newest", "Older"]
