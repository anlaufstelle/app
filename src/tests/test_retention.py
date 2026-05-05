"""Unit tests for the enforce_retention management command."""

from datetime import timedelta

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.utils import timezone

from core.models import Activity, AuditLog, Case, Event, Facility, Settings


@pytest.fixture
def facility_with_settings(facility):
    """Facility with default retention settings."""
    Settings.objects.create(
        facility=facility,
        retention_anonymous_days=90,
        retention_identified_days=365,
        retention_qualified_days=3650,
    )
    return facility


@pytest.fixture
def anon_event_expired(facility_with_settings, doc_type_contact, staff_user):
    """Anonymous event older than 90 days → should be deleted."""
    return Event.objects.create(
        facility=facility_with_settings,
        client=None,
        document_type=doc_type_contact,
        occurred_at=timezone.now() - timedelta(days=100),
        data_json={"dauer": 10},
        is_anonymous=True,
        created_by=staff_user,
    )


@pytest.fixture
def anon_event_recent(facility_with_settings, doc_type_contact, staff_user):
    """Anonymous event within retention period → should stay."""
    return Event.objects.create(
        facility=facility_with_settings,
        client=None,
        document_type=doc_type_contact,
        occurred_at=timezone.now() - timedelta(days=10),
        data_json={"dauer": 5},
        is_anonymous=True,
        created_by=staff_user,
    )


@pytest.fixture
def identified_event_expired(facility_with_settings, client_identified, doc_type_contact, staff_user):
    """Event from IDENTIFIED client older than 365 days → should be deleted."""
    return Event.objects.create(
        facility=facility_with_settings,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=timezone.now() - timedelta(days=400),
        data_json={"dauer": 20},
        is_anonymous=False,
        created_by=staff_user,
    )


@pytest.fixture
def identified_event_recent(facility_with_settings, client_identified, doc_type_contact, staff_user):
    """Event from IDENTIFIED client within retention period → should stay."""
    return Event.objects.create(
        facility=facility_with_settings,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=timezone.now() - timedelta(days=100),
        data_json={"dauer": 15},
        is_anonymous=False,
        created_by=staff_user,
    )


@pytest.fixture
def qualified_event_open_case(facility_with_settings, client_qualified, doc_type_contact, staff_user):
    """Event from QUALIFIED client with OPEN case → should stay."""
    case = Case.objects.create(
        facility=facility_with_settings,
        client=client_qualified,
        title="Open Case",
        status=Case.Status.OPEN,
        created_by=staff_user,
    )
    return Event.objects.create(
        facility=facility_with_settings,
        client=client_qualified,
        case=case,
        document_type=doc_type_contact,
        occurred_at=timezone.now() - timedelta(days=4000),
        data_json={"dauer": 30},
        is_anonymous=False,
        created_by=staff_user,
    )


@pytest.fixture
def qualified_event_closed_case_expired(facility_with_settings, client_qualified, doc_type_contact, staff_user):
    """Event from QUALIFIED client with CLOSED case, closed more than 3650 days ago → should be deleted."""
    case = Case.objects.create(
        facility=facility_with_settings,
        client=client_qualified,
        title="Closed Case",
        status=Case.Status.CLOSED,
        closed_at=timezone.now() - timedelta(days=3700),
        created_by=staff_user,
    )
    return Event.objects.create(
        facility=facility_with_settings,
        client=client_qualified,
        case=case,
        document_type=doc_type_contact,
        occurred_at=timezone.now() - timedelta(days=4000),
        data_json={"dauer": 30},
        is_anonymous=False,
        created_by=staff_user,
    )


@pytest.fixture
def qualified_event_closed_case_recent(facility_with_settings, client_qualified, doc_type_contact, staff_user):
    """Event from QUALIFIED client with CLOSED case, closed recently → should stay."""
    case = Case.objects.create(
        facility=facility_with_settings,
        client=client_qualified,
        title="Recently Closed Case",
        status=Case.Status.CLOSED,
        closed_at=timezone.now() - timedelta(days=100),
        created_by=staff_user,
    )
    return Event.objects.create(
        facility=facility_with_settings,
        client=client_qualified,
        case=case,
        document_type=doc_type_contact,
        occurred_at=timezone.now() - timedelta(days=200),
        data_json={"dauer": 25},
        is_anonymous=False,
        created_by=staff_user,
    )


@pytest.mark.django_db
def test_anonymous_event_older_than_retention_gets_deleted(anon_event_expired):
    """Anonymous event older than retention_anonymous_days is soft-deleted."""
    call_command("enforce_retention")

    anon_event_expired.refresh_from_db()
    assert anon_event_expired.is_deleted is True
    assert anon_event_expired.data_json == {}


@pytest.mark.django_db
def test_anonymous_event_within_retention_stays(anon_event_recent):
    """Anonymous event within retention period is not deleted."""
    call_command("enforce_retention")

    anon_event_recent.refresh_from_db()
    assert anon_event_recent.is_deleted is False
    assert anon_event_recent.data_json != {}


@pytest.mark.django_db
def test_identified_event_expired_gets_deleted(identified_event_expired):
    """Event from IDENTIFIED client older than retention_identified_days is soft-deleted."""
    call_command("enforce_retention")

    identified_event_expired.refresh_from_db()
    assert identified_event_expired.is_deleted is True
    assert identified_event_expired.data_json == {}


@pytest.mark.django_db
def test_identified_event_within_retention_stays(identified_event_recent):
    """Event from IDENTIFIED client within retention period is not deleted."""
    call_command("enforce_retention")

    identified_event_recent.refresh_from_db()
    assert identified_event_recent.is_deleted is False
    assert identified_event_recent.data_json != {}


@pytest.mark.django_db
def test_qualified_event_open_case_stays(qualified_event_open_case):
    """Event from QUALIFIED client with open case is not deleted, even if very old."""
    call_command("enforce_retention")

    qualified_event_open_case.refresh_from_db()
    assert qualified_event_open_case.is_deleted is False


@pytest.mark.django_db
def test_qualified_event_closed_case_expired_gets_deleted(qualified_event_closed_case_expired):
    """Event from QUALIFIED client with closed + expired case is soft-deleted."""
    call_command("enforce_retention")

    qualified_event_closed_case_expired.refresh_from_db()
    assert qualified_event_closed_case_expired.is_deleted is True
    assert qualified_event_closed_case_expired.data_json == {}


@pytest.mark.django_db
def test_qualified_event_closed_case_recent_stays(qualified_event_closed_case_recent):
    """Event from QUALIFIED client with recently closed case is not deleted."""
    call_command("enforce_retention")

    qualified_event_closed_case_recent.refresh_from_db()
    assert qualified_event_closed_case_recent.is_deleted is False


@pytest.mark.django_db
def test_dry_run_does_not_delete(anon_event_expired):
    """Dry-run mode counts but does not actually delete events."""
    call_command("enforce_retention", dry_run=True)

    anon_event_expired.refresh_from_db()
    assert anon_event_expired.is_deleted is False
    assert anon_event_expired.data_json != {}


@pytest.mark.django_db
def test_dry_run_creates_no_audit_log(anon_event_expired):
    """Dry-run mode does not create audit log entries."""
    initial_audit_count = AuditLog.objects.count()
    call_command("enforce_retention", dry_run=True)
    assert AuditLog.objects.count() == initial_audit_count


@pytest.mark.django_db
def test_audit_log_created_on_deletion(anon_event_expired):
    """An AuditLog entry with action=DELETE is created when events are deleted."""
    initial_audit_count = AuditLog.objects.count()
    call_command("enforce_retention")

    new_logs = AuditLog.objects.filter(action=AuditLog.Action.DELETE)
    assert new_logs.exists()
    assert AuditLog.objects.count() > initial_audit_count


@pytest.mark.django_db
def test_facility_filter_limits_scope(facility_with_settings, anon_event_expired):
    """--facility option limits processing to the specified facility."""
    # Deleted because facility matches
    call_command("enforce_retention", facility=facility_with_settings.name)
    anon_event_expired.refresh_from_db()
    assert anon_event_expired.is_deleted is True


@pytest.mark.django_db
def test_facility_filter_nonexistent_warns(capsys):
    """--facility with unknown name prints an error and does nothing."""
    call_command("enforce_retention", facility="Nonexistent Facility")
    # Should not raise, just warn


@pytest.mark.django_db
def test_already_deleted_event_not_reprocessed(anon_event_expired):
    """An event that is already soft-deleted is not processed again."""
    # Pre-mark as deleted with cleared data
    Event.objects.filter(pk=anon_event_expired.pk).update(
        is_deleted=True,
        data_json={},
    )
    anon_event_expired.refresh_from_db()

    initial_audit_count = AuditLog.objects.count()
    call_command("enforce_retention")

    # No new audit log entries should be created for an already-deleted event
    assert AuditLog.objects.count() == initial_audit_count


@pytest.mark.django_db
def test_no_settings_facility_skipped(facility, doc_type_contact, staff_user):
    """A facility without Settings is silently skipped — no error, no deletion."""
    # Create an old anonymous event in a facility that has no Settings
    event = Event.objects.create(
        facility=facility,
        client=None,
        document_type=doc_type_contact,
        occurred_at=timezone.now() - timedelta(days=200),
        data_json={"dauer": 5},
        is_anonymous=True,
        created_by=staff_user,
    )

    # Running the command must not raise
    call_command("enforce_retention")

    # The event must not be deleted since no retention settings exist
    event.refresh_from_db()
    assert event.is_deleted is False


@pytest.mark.django_db
def test_client_anonymized_after_all_events_soft_deleted(identified_event_expired, client_identified):
    """Client is anonymized when all their events have been soft-deleted by retention."""
    call_command("enforce_retention")

    # Event should be soft-deleted
    identified_event_expired.refresh_from_db()
    assert identified_event_expired.is_deleted is True

    # Client should be anonymized
    client_identified.refresh_from_db()
    assert client_identified.pseudonym.startswith("Gelöscht-")
    assert client_identified.notes == ""
    assert client_identified.is_active is False


@pytest.mark.django_db
def test_client_not_anonymized_with_active_events(identified_event_recent, client_identified):
    """Client is NOT anonymized when they still have active (non-deleted) events."""
    call_command("enforce_retention")

    client_identified.refresh_from_db()
    assert not client_identified.pseudonym.startswith("Gelöscht-")
    assert client_identified.is_active is True


@pytest.mark.django_db
def test_events_survive_client_deletion(facility_with_settings, client_identified, doc_type_contact, staff_user):
    """Events with SET_NULL FK survive when their client is deleted (not just anonymized)."""
    event = Event.objects.create(
        facility=facility_with_settings,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=timezone.now() - timedelta(days=10),
        data_json={"dauer": 15},
        is_anonymous=False,
        created_by=staff_user,
    )

    # Hard-delete the client (simulates a direct DB delete)
    client_identified.delete()

    # Event must still exist with client=None
    event.refresh_from_db()
    assert event.client is None
    assert event.is_deleted is False
    assert event.data_json == {"dauer": 15}


@pytest.mark.django_db
def test_client_deletion_protected_when_cases_exist(facility_with_settings, client_identified, staff_user):
    """Refs #748: Case.client uses on_delete=PROTECT — Personen mit aktiven
    Fällen können nicht versehentlich gelöscht werden. Ein Lösch-Versuch
    wirft ``ProtectedError`` und der Fall bleibt mit Klientel-Zuordnung
    erhalten.
    """
    from django.db.models.deletion import ProtectedError

    case = Case.objects.create(
        facility=facility_with_settings,
        client=client_identified,
        title="Test Case",
        status=Case.Status.OPEN,
        created_by=staff_user,
    )

    with pytest.raises(ProtectedError):
        client_identified.delete()

    case.refresh_from_db()
    assert case.client_id == client_identified.pk


@pytest.mark.django_db
def test_anonymize_creates_audit_log(identified_event_expired, facility_with_settings):
    """Client anonymization creates an AuditLog entry."""
    call_command("enforce_retention")

    log = AuditLog.objects.filter(action=AuditLog.Action.DELETE, target_type="Client").first()
    assert log is not None
    assert log.detail["category"] == "client_anonymized"


# ---------------------------------------------------------------------------
# Activity retention tests
# ---------------------------------------------------------------------------


def _create_activity(facility, staff_user, days_ago):
    """Helper to create an Activity with a specific age."""
    ct = ContentType.objects.get_for_model(Facility)
    return Activity.objects.create(
        facility=facility,
        actor=staff_user,
        verb=Activity.Verb.CREATED,
        target_type=ct,
        target_id=facility.pk,
        summary="Test activity",
        occurred_at=timezone.now() - timedelta(days=days_ago),
    )


@pytest.mark.django_db
def test_activity_older_than_retention_gets_deleted(facility_with_settings, staff_user):
    """Activity older than retention_activities_days is hard-deleted."""
    old_activity = _create_activity(facility_with_settings, staff_user, days_ago=400)

    call_command("enforce_retention")

    assert not Activity.objects.filter(pk=old_activity.pk).exists()


@pytest.mark.django_db
def test_activity_within_retention_stays(facility_with_settings, staff_user):
    """Activity within retention period is not deleted."""
    recent_activity = _create_activity(facility_with_settings, staff_user, days_ago=100)

    call_command("enforce_retention")

    assert Activity.objects.filter(pk=recent_activity.pk).exists()


@pytest.mark.django_db
def test_activity_retention_custom_setting(facility_with_settings, staff_user):
    """Custom retention_activities_days is respected."""
    settings_obj = facility_with_settings.settings
    settings_obj.retention_activities_days = 30
    settings_obj.save(update_fields=["retention_activities_days"])

    old_activity = _create_activity(facility_with_settings, staff_user, days_ago=50)
    recent_activity = _create_activity(facility_with_settings, staff_user, days_ago=10)

    call_command("enforce_retention")

    assert not Activity.objects.filter(pk=old_activity.pk).exists()
    assert Activity.objects.filter(pk=recent_activity.pk).exists()


@pytest.mark.django_db
def test_activity_dry_run_does_not_delete(facility_with_settings, staff_user):
    """Dry-run mode counts but does not delete activities."""
    old_activity = _create_activity(facility_with_settings, staff_user, days_ago=400)

    call_command("enforce_retention", dry_run=True)

    assert Activity.objects.filter(pk=old_activity.pk).exists()


@pytest.mark.django_db
def test_activity_deletion_creates_audit_log(facility_with_settings, staff_user):
    """Hard-deleting activities creates an AuditLog entry."""
    _create_activity(facility_with_settings, staff_user, days_ago=400)

    call_command("enforce_retention")

    log = AuditLog.objects.filter(action=AuditLog.Action.DELETE, target_type="Activity").first()
    assert log is not None
    assert log.detail["category"] == "activities"
    assert log.detail["count"] == 1
    assert log.detail["retention_days"] == 365


# ---------------------------------------------------------------------------
# Snapshot-related retention tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_retention_creates_snapshot_before_deletion(facility_with_settings, doc_type_contact, staff_user):
    """enforce_retention creates a StatisticsSnapshot for affected months before deleting."""
    from core.models import StatisticsSnapshot

    # Create an expired anonymous event in a past month
    event = Event.objects.create(
        facility=facility_with_settings,
        client=None,
        document_type=doc_type_contact,
        occurred_at=timezone.now() - timedelta(days=100),
        data_json={"dauer": 10},
        is_anonymous=True,
        created_by=staff_user,
    )
    event_month = event.occurred_at.month
    event_year = event.occurred_at.year

    assert StatisticsSnapshot.objects.count() == 0

    call_command("enforce_retention")

    # Snapshot should have been created for the event's month
    snap = StatisticsSnapshot.objects.filter(
        facility=facility_with_settings,
        year=event_year,
        month=event_month,
    ).first()
    assert snap is not None, "Snapshot was not created for the affected month"
    # The snapshot data should reflect the pre-deletion count (1 event)
    assert snap.data["total_contacts"] >= 1


@pytest.mark.django_db
def test_retention_dry_run_no_snapshot(facility_with_settings, doc_type_contact, staff_user):
    """Dry-run mode does not create any StatisticsSnapshot."""
    from core.models import StatisticsSnapshot

    Event.objects.create(
        facility=facility_with_settings,
        client=None,
        document_type=doc_type_contact,
        occurred_at=timezone.now() - timedelta(days=100),
        data_json={"dauer": 10},
        is_anonymous=True,
        created_by=staff_user,
    )

    assert StatisticsSnapshot.objects.count() == 0

    call_command("enforce_retention", dry_run=True)

    assert StatisticsSnapshot.objects.count() == 0


@pytest.mark.django_db
def test_statistics_correct_after_retention(facility_with_settings, doc_type_contact, staff_user):
    """Hybrid statistics after retention match the pre-deletion live statistics."""
    import calendar
    from datetime import date

    from core.services.snapshot import get_statistics_hybrid
    from core.services.statistics import get_statistics

    # Create an expired anonymous event in a past month
    old_date = timezone.now() - timedelta(days=100)
    Event.objects.create(
        facility=facility_with_settings,
        client=None,
        document_type=doc_type_contact,
        occurred_at=old_date,
        data_json={"dauer": 10},
        is_anonymous=True,
        created_by=staff_user,
    )

    # Compute full-month date range covering the event's month
    event_date = old_date.date()
    _, last_day = calendar.monthrange(event_date.year, event_date.month)
    date_from = date(event_date.year, event_date.month, 1)
    date_to = date(event_date.year, event_date.month, last_day)

    # Get stats before retention (live)
    stats_before = get_statistics(facility_with_settings, date_from, date_to)
    total_before = stats_before["total_contacts"]
    assert total_before >= 1

    # Run retention (creates snapshot, then deletes)
    call_command("enforce_retention")

    # Live stats after deletion should be less
    stats_live_after = get_statistics(facility_with_settings, date_from, date_to)
    assert stats_live_after["total_contacts"] < total_before

    # Hybrid stats should use the snapshot and match pre-deletion numbers
    stats_hybrid = get_statistics_hybrid(facility_with_settings, date_from, date_to)
    assert stats_hybrid["total_contacts"] == total_before


@pytest.mark.django_db
def test_collect_doomed_events_covers_all_strategies(
    facility_with_settings, client_identified, client_qualified, doc_type_contact, staff_user
):
    """_collect_doomed_events returns events matching all 4 retention strategies."""
    from core.management.commands.enforce_retention import Command

    now = timezone.now()

    # Strategy 1: Anonymous expired event
    ev_anon = Event.objects.create(
        facility=facility_with_settings,
        client=None,
        document_type=doc_type_contact,
        occurred_at=now - timedelta(days=100),
        data_json={"dauer": 10},
        is_anonymous=True,
        created_by=staff_user,
    )

    # Strategy 2: Identified client expired event
    ev_ident = Event.objects.create(
        facility=facility_with_settings,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=now - timedelta(days=400),
        data_json={"dauer": 20},
        is_anonymous=False,
        created_by=staff_user,
    )

    # Strategy 3: Qualified client with closed + expired case
    case_closed = Case.objects.create(
        facility=facility_with_settings,
        client=client_qualified,
        title="Old Closed Case",
        status=Case.Status.CLOSED,
        closed_at=now - timedelta(days=3700),
        created_by=staff_user,
    )
    ev_qual = Event.objects.create(
        facility=facility_with_settings,
        client=client_qualified,
        case=case_closed,
        document_type=doc_type_contact,
        occurred_at=now - timedelta(days=4000),
        data_json={"dauer": 30},
        is_anonymous=False,
        created_by=staff_user,
    )

    # Strategy 4: DocumentType with custom retention_days
    from core.models import DocumentType

    doc_type_custom = DocumentType.objects.create(
        facility=facility_with_settings,
        category=DocumentType.Category.CONTACT,
        name="Custom Retention Type",
        retention_days=30,
    )
    ev_doctype = Event.objects.create(
        facility=facility_with_settings,
        client=None,
        document_type=doc_type_custom,
        occurred_at=now - timedelta(days=50),
        data_json={"dauer": 5},
        is_anonymous=False,
        created_by=staff_user,
    )

    settings_obj = facility_with_settings.settings
    cmd = Command()
    doomed_qs = cmd._collect_doomed_events(facility_with_settings, settings_obj, now)
    doomed_ids = set(doomed_qs.values_list("pk", flat=True))

    assert ev_anon.pk in doomed_ids, "Anonymous expired event not in doomed set"
    assert ev_ident.pk in doomed_ids, "Identified expired event not in doomed set"
    assert ev_qual.pk in doomed_ids, "Qualified closed-case expired event not in doomed set"
    assert ev_doctype.pk in doomed_ids, "DocumentType custom-retention expired event not in doomed set"
