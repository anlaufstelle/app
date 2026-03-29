"""Tests for the create_statistics_snapshots management command."""

from datetime import date, datetime
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.utils import timezone

from core.models import Event, StatisticsSnapshot


@pytest.fixture
def _events_jan_feb(facility, client_identified, doc_type_contact, staff_user):
    """Create events in Jan 2025 and Feb 2025."""
    jan_dt = timezone.make_aware(datetime(2025, 1, 15, 10, 0))
    feb_dt = timezone.make_aware(datetime(2025, 2, 20, 14, 0))
    Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=jan_dt,
        data_json={},
        created_by=staff_user,
    )
    Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=feb_dt,
        data_json={},
        created_by=staff_user,
    )


@pytest.mark.django_db
def test_default_snapshots_previous_month(facility, client_identified, doc_type_contact, staff_user, settings_obj):
    """No flags -> snapshots previous month for all facilities."""
    # Create an event in Feb 2025
    feb_dt = timezone.make_aware(datetime(2025, 2, 10, 12, 0))
    Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=feb_dt,
        data_json={},
        created_by=staff_user,
    )

    # Mock timezone.localdate() so "previous month" is Feb 2025 (today = March 2025)
    mock_date = date(2025, 3, 15)
    with patch("core.management.commands.create_statistics_snapshots.timezone") as mock_cmd_tz:
        mock_cmd_tz.localdate.return_value = mock_date
        call_command("create_statistics_snapshots")

    assert StatisticsSnapshot.objects.count() == 1
    snap = StatisticsSnapshot.objects.first()
    assert snap.facility == facility
    assert snap.year == 2025
    assert snap.month == 2


@pytest.mark.django_db
@pytest.mark.usefixtures("_events_jan_feb")
def test_backfill_creates_all(facility, settings_obj):
    """--backfill -> creates snapshots for all months with events (excluding current)."""
    # Mock today to April 2025 so both Jan and Feb are in the past
    mock_date = date(2025, 4, 1)
    with patch("core.management.commands.create_statistics_snapshots.timezone") as mock_cmd_tz:
        mock_cmd_tz.localdate.return_value = mock_date
        call_command("create_statistics_snapshots", backfill=True)

    assert StatisticsSnapshot.objects.count() == 2
    assert StatisticsSnapshot.objects.filter(facility=facility, year=2025, month=1).exists()
    assert StatisticsSnapshot.objects.filter(facility=facility, year=2025, month=2).exists()


@pytest.mark.django_db
@pytest.mark.usefixtures("_events_jan_feb")
def test_specific_month(facility, settings_obj):
    """--year 2025 --month 2 -> only that month."""
    call_command("create_statistics_snapshots", year=2025, month=2)

    assert StatisticsSnapshot.objects.count() == 1
    snap = StatisticsSnapshot.objects.first()
    assert snap.facility == facility
    assert snap.year == 2025
    assert snap.month == 2


@pytest.mark.django_db
@pytest.mark.usefixtures("_events_jan_feb")
def test_dry_run_no_snapshots(facility, settings_obj):
    """--dry-run -> no StatisticsSnapshot objects created."""
    call_command("create_statistics_snapshots", year=2025, month=1, dry_run=True)

    assert StatisticsSnapshot.objects.count() == 0


@pytest.mark.django_db
def test_facility_filter(
    facility,
    settings_obj,
    client_identified,
    doc_type_contact,
    staff_user,
    other_facility,
):
    """--facility X -> only that facility gets a snapshot."""
    # Create settings for other_facility too
    from core.models import Settings

    Settings.objects.create(
        facility=other_facility,
        retention_anonymous_days=90,
        retention_identified_days=365,
        retention_qualified_days=3650,
    )

    # Create events in both facilities for Jan 2025
    jan_dt = timezone.make_aware(datetime(2025, 1, 15, 10, 0))
    Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=jan_dt,
        data_json={},
        created_by=staff_user,
    )

    # other_facility needs its own client and doc_type
    from core.models import Client, DocumentType

    other_client = Client.objects.create(
        facility=other_facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Other-01",
        created_by=staff_user,
    )
    other_doc = DocumentType.objects.create(
        facility=other_facility,
        category=DocumentType.Category.CONTACT,
        name="Kontakt",
    )
    Event.objects.create(
        facility=other_facility,
        client=other_client,
        document_type=other_doc,
        occurred_at=jan_dt,
        data_json={},
        created_by=staff_user,
    )

    call_command(
        "create_statistics_snapshots",
        year=2025,
        month=1,
        facility="Teststelle",
    )

    assert StatisticsSnapshot.objects.count() == 1
    assert StatisticsSnapshot.objects.filter(facility=facility).exists()
    assert not StatisticsSnapshot.objects.filter(facility=other_facility).exists()
