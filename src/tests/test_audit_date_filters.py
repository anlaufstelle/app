"""Tests for the audit date-filter range helper.

Refs https://github.com/anlaufstelle/app/issues/2029
"""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from django.test import RequestFactory
from django.utils import timezone

from core.models import AuditLog, Facility, Organization, User
from core.services.audit.filters import day_bounds


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC)


def test_day_bounds_returns_aware_start_and_next_midnight():
    """day_bounds yields timezone-aware [00:00, 00:00 next day) in TIME_ZONE."""
    start, end = day_bounds(date(2026, 6, 1))
    assert start == timezone.make_aware(datetime.combine(date(2026, 6, 1), time.min))
    assert end == start + timedelta(days=1)
    assert end.day == 2


@pytest.mark.parametrize(
    ("day", "expected_start_utc", "expected_end_utc", "expected_wall_hours"),
    [
        # Spring forward: 2026-03-29 00:00 CET -> 2026-03-28 23:00 UTC,
        # next midnight CEST -> 2026-03-29 22:00 UTC = 23 wall-clock hours.
        (
            date(2026, 3, 29),
            datetime(2026, 3, 28, 23, 0, tzinfo=UTC),
            datetime(2026, 3, 29, 22, 0, tzinfo=UTC),
            23,
        ),
        # Fall back: 2026-10-25 00:00 CEST -> 2026-10-24 22:00 UTC,
        # next midnight CET -> 2026-10-25 23:00 UTC = 25 wall-clock hours.
        (
            date(2026, 10, 25),
            datetime(2026, 10, 24, 22, 0, tzinfo=UTC),
            datetime(2026, 10, 25, 23, 0, tzinfo=UTC),
            25,
        ),
        # Year boundary: 2026-12-31 00:00 CET -> 2026-12-30 23:00 UTC,
        # next midnight CET -> 2026-12-31 23:00 UTC = 24 hours.
        (
            date(2026, 12, 31),
            datetime(2026, 12, 30, 23, 0, tzinfo=UTC),
            datetime(2026, 12, 31, 23, 0, tzinfo=UTC),
            24,
        ),
    ],
)
def test_day_bounds_handles_dst_and_year_boundaries(day, expected_start_utc, expected_end_utc, expected_wall_hours):
    """day + 1 means the next local midnight, even across DST transitions."""
    start, end = day_bounds(day)
    assert _utc(start) == expected_start_utc
    assert _utc(end) == expected_end_utc
    assert (_utc(end) - _utc(start)).total_seconds() == expected_wall_hours * 3600


def _make_user(facility):
    return User.objects.create_user(
        username=f"auditor-{facility.pk}",
        password="x",
        facility=facility,
        role=User.Role.FACILITY_ADMIN,
    )


@pytest.mark.django_db
def test_range_filter_matches_date_lookup_at_day_boundaries():
    """The new range filter returns exactly the same rows as timestamp__date."""
    facility = Facility.objects.create(organization=Organization.objects.create(name="Org"), name="Stelle")
    user = _make_user(facility)
    day = date(2026, 6, 15)
    start, end = day_bounds(day)
    timestamps = [
        start - timedelta(seconds=1),  # excluded
        start + timedelta(seconds=1),  # included
        end - timedelta(seconds=1),  # included
        end,  # excluded
    ]
    entries = [
        AuditLog.objects.create(
            facility=facility,
            user=user,
            action=AuditLog.Action.LOGIN,
            timestamp=ts,
        )
        for ts in timestamps
    ]
    old_qs = AuditLog.objects.filter(facility=facility, timestamp__date__gte=day, timestamp__date__lte=day)
    new_qs = AuditLog.objects.filter(facility=facility, timestamp__gte=start, timestamp__lt=end)
    assert set(old_qs) == set(new_qs)
    assert set(new_qs) == {entries[1], entries[2]}


@pytest.mark.django_db
def test_system_audit_filters_match_date_lookup_at_boundaries():
    """System audit list/export shared filter matches __date semantics."""
    from core.views.system.audit import _apply_auditlog_filters

    facility = Facility.objects.create(organization=Organization.objects.create(name="Org"), name="Stelle")
    user = _make_user(facility)
    day = date(2026, 6, 15)
    start, end = day_bounds(day)
    timestamps = [
        start - timedelta(seconds=1),
        start + timedelta(seconds=1),
        end - timedelta(seconds=1),
        end,
    ]
    for ts in timestamps:
        AuditLog.objects.create(
            facility=facility,
            user=user,
            action=AuditLog.Action.LOGIN,
            timestamp=ts,
        )

    request = RequestFactory().get(f"/system/audit/?date_from={day.isoformat()}&date_to={day.isoformat()}")
    qs, _raw, _applied = _apply_auditlog_filters(request)
    old_qs = qs.filter(timestamp__date__gte=day, timestamp__date__lte=day)
    new_qs = qs.filter(timestamp__gte=start, timestamp__lt=end)
    assert set(old_qs) == set(new_qs)
    assert set(new_qs) == set(AuditLog.objects.filter(timestamp__gte=start, timestamp__lt=end))


@pytest.mark.django_db
def test_audit_view_date_filter_matches_day_boundary_entries(client):
    """The /audit/ view returns exactly one entry at the day boundary."""
    facility = Facility.objects.create(organization=Organization.objects.create(name="Org"), name="Stelle")
    user = _make_user(facility)
    day = date(2026, 6, 15)
    start, end = day_bounds(day)
    AuditLog.objects.create(
        facility=facility,
        user=user,
        action=AuditLog.Action.LOGIN,
        timestamp=end - timedelta(seconds=1),
    )
    AuditLog.objects.create(
        facility=facility,
        user=user,
        action=AuditLog.Action.LOGIN,
        timestamp=end,
    )
    AuditLog.objects.create(
        facility=facility,
        user=user,
        action=AuditLog.Action.LOGIN,
        timestamp=start - timedelta(seconds=1),
    )

    client.force_login(user)
    response = client.get(f"/audit/?date_from={day.isoformat()}&date_to={day.isoformat()}", follow=True)
    assert response.status_code == 200
    page_obj = response.context["page_obj"]
    assert page_obj.paginator.count == 1
