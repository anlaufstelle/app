"""Delta STAT pins for reading the persisted monthly statistics payload."""

import pytest

from core.models import Facility, StatisticsSnapshot
from core.services.dashboard.snapshot import get_snapshot

pytestmark = pytest.mark.django_db


def test_existing_snapshot_returns_the_requested_facility_and_month_payload(facility):
    """Delta STAT: x_get_snapshot__mutmut_7 always discards the stored data."""
    other_facility = Facility.objects.create(organization=facility.organization, name="Other facility")
    expected = {"total_contacts": 7, "by_document_type": [{"name": "Contact", "count": 7}]}
    StatisticsSnapshot.objects.create(facility=facility, year=2024, month=6, data=expected)
    StatisticsSnapshot.objects.create(facility=facility, year=2024, month=5, data={"total_contacts": 3})
    StatisticsSnapshot.objects.create(facility=facility, year=2023, month=6, data={"total_contacts": 4})
    StatisticsSnapshot.objects.create(facility=other_facility, year=2024, month=6, data={"total_contacts": 99})

    assert get_snapshot(facility, 2024, 6) == expected
