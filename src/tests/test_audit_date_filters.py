"""Tests for the audit date-filter range helper.

Refs #2029
"""

import csv
import io
from datetime import UTC, date, datetime, time, timedelta

import pytest
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
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


# --- Shared fixtures for the boundary probes ---------------------------------
#
# Refs #2029: Die Erwartungsmenge wird IMMER unabhaengig vom
# Produktionscode aus dem alten ``timestamp__date``-Lookup gebaut und
# zusaetzlich mit benannten Off-by-one-Proben belegt. Ein
# ``timestamp__lt`` -> ``timestamp__lte`` an einer der beiden Filterstellen
# muss an ``at_end`` scheitern, nicht erst an einer Gesamtzahl.

#: Der Tag, auf den alle Grenzproben filtern (kein DST-Uebergang, weit weg
#: von ``timezone.now()``, damit Login-/Export-Audit-Zeilen nicht hineinfallen).
PROBE_DAY = date(2026, 6, 15)


def _make_user(facility, username=None):
    return User.objects.create_user(
        username=username or f"auditor-{facility.pk}",
        password="x",
        facility=facility,
        role=User.Role.FACILITY_ADMIN,
    )


def _make_facility(name="Stelle"):
    return Facility.objects.create(organization=Organization.objects.create(name="Org"), name=name)


def _boundary_entries(facility, user, day=PROBE_DAY):
    """Four AuditLog rows around the local day boundaries of ``day``.

    ``at_end`` sits on exactly 00:00:00 Europe/Berlin of the FOLLOWING day —
    the row an inclusive upper bound (``timestamp__lte``) would wrongly keep.
    ``before_end`` sits on 23:59:59 of ``day`` and must always stay.
    """
    start, end = day_bounds(day)
    stamps = {
        "before_start": start - timedelta(seconds=1),
        "after_start": start + timedelta(seconds=1),
        "before_end": end - timedelta(seconds=1),
        "at_end": end,
    }
    return {
        name: AuditLog.objects.create(
            facility=facility,
            user=user,
            action=AuditLog.Action.LOGIN,
            timestamp=ts,
        )
        for name, ts in stamps.items()
    }


def _date_lookup_pks(day=PROBE_DAY, **extra):
    """Expected pks, built independently via the OLD ``timestamp__date`` lookup."""
    return set(
        AuditLog.objects.filter(
            timestamp__date__gte=day,
            timestamp__date__lte=day,
            **extra,
        ).values_list("pk", flat=True)
    )


def _auditlog_select_sql(ctx):
    """SQL of the captured SELECTs that read ``core_auditlog`` with a timestamp filter."""
    statements = [
        q.get("sql", "")
        for q in ctx.captured_queries
        if 'FROM "core_auditlog"' in q.get("sql", "") and '"core_auditlog"."timestamp"' in q.get("sql", "")
    ]
    assert statements, [q.get("sql", "") for q in ctx.captured_queries]
    return "\n".join(statements)


def _assert_sargable_range(sql):
    """The filter must compare the raw column, not ``date(timestamp AT TIME ZONE ...)``.

    This is the only assertion that survives a *semantics-preserving* full
    revert to ``timestamp__date__gte/lte`` — that revert returns the same rows,
    so no behavioural assertion can see it, but it destroys the whole point of
    #2029 (index usability).
    """
    assert "::date" not in sql, sql
    assert "AT TIME ZONE" not in sql, sql
    assert '"core_auditlog"."timestamp" >= ' in sql, sql
    assert '"core_auditlog"."timestamp" < ' in sql, sql


# --- The helper itself --------------------------------------------------------


@pytest.mark.django_db
def test_range_filter_matches_date_lookup_at_day_boundaries():
    """The new range filter returns exactly the same rows as timestamp__date."""
    facility = _make_facility()
    user = _make_user(facility)
    entries = _boundary_entries(facility, user)
    start, end = day_bounds(PROBE_DAY)

    old_qs = AuditLog.objects.filter(facility=facility, timestamp__date__gte=PROBE_DAY, timestamp__date__lte=PROBE_DAY)
    new_qs = AuditLog.objects.filter(facility=facility, timestamp__gte=start, timestamp__lt=end)

    assert set(old_qs) == set(new_qs)
    assert set(new_qs) == {entries["after_start"], entries["before_end"]}


# --- views/system/audit.py: shared filter, list view and CSV export ----------


@pytest.mark.django_db
def test_system_audit_filters_match_date_lookup_at_boundaries():
    """``_apply_auditlog_filters`` (list AND export) matches the __date semantics.

    The expectation is built independently from the production code via the old
    ``timestamp__date`` lookups; the named off-by-one probes pin which single
    row an inclusive upper bound would add.
    """
    from core.views.system.audit import _apply_auditlog_filters

    facility = _make_facility()
    user = _make_user(facility)
    entries = _boundary_entries(facility, user)
    expected = _date_lookup_pks()
    assert expected == {entries["after_start"].pk, entries["before_end"].pk}

    request = RequestFactory().get(f"/system/audit/?date_from={PROBE_DAY.isoformat()}&date_to={PROBE_DAY.isoformat()}")
    with CaptureQueriesContext(connection) as ctx:
        qs, _raw, _applied = _apply_auditlog_filters(request)
        actual = set(qs.values_list("pk", flat=True))

    assert actual == expected
    # Off-by-one probes: 23:59:59 stays, 00:00:00 of the next day goes.
    assert entries["before_end"].pk in actual
    assert entries["at_end"].pk not in actual
    assert entries["before_start"].pk not in actual
    _assert_sargable_range(_auditlog_select_sql(ctx))


@pytest.mark.django_db
def test_system_audit_list_view_date_filter_matches_date_lookup(client, super_admin_user):
    """The /system/audit/ list page shows exactly the __date-equivalent rows."""
    facility = _make_facility()
    user = _make_user(facility)
    entries = _boundary_entries(facility, user)
    expected = _date_lookup_pks()

    client.force_login(super_admin_user)
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(
            reverse("core:system_audit_list"),
            {"date_from": PROBE_DAY.isoformat(), "date_to": PROBE_DAY.isoformat()},
        )

    assert response.status_code == 200
    actual = {entry.pk for entry in response.context["page_obj"].object_list}
    assert actual == expected
    assert entries["before_end"].pk in actual
    assert entries["at_end"].pk not in actual
    _assert_sargable_range(_auditlog_select_sql(ctx))


@pytest.mark.django_db
def test_system_audit_csv_export_date_filter_matches_date_lookup(client, super_admin_user):
    """The CSV export streams exactly the __date-equivalent rows.

    List and export share ``_apply_auditlog_filters``; this pins the export end
    to end, because that is the artefact a super_admin takes out of the system.
    """
    facility = _make_facility()
    user = _make_user(facility)
    entries = _boundary_entries(facility, user)
    expected = {
        ts.isoformat()
        for ts in AuditLog.objects.filter(timestamp__date__gte=PROBE_DAY, timestamp__date__lte=PROBE_DAY).values_list(
            "timestamp", flat=True
        )
    }

    client.force_login(super_admin_user)
    response = client.get(
        reverse("core:system_audit_export"),
        {"format": "csv", "date_from": PROBE_DAY.isoformat(), "date_to": PROBE_DAY.isoformat()},
    )
    assert response.status_code == 200
    content = b"".join(response.streaming_content).decode("utf-8")
    exported = {row["timestamp"] for row in csv.DictReader(io.StringIO(content))}

    assert exported == expected
    # Off-by-one probes (the export renders the DB value, i.e. UTC).
    assert _utc(entries["before_end"].timestamp).isoformat() in exported
    assert _utc(entries["at_end"].timestamp).isoformat() not in exported
    assert _utc(entries["before_start"].timestamp).isoformat() not in exported


# --- views/audit.py: the facility-scoped list --------------------------------


@pytest.mark.django_db
def test_audit_view_date_filter_matches_date_lookup_at_boundaries(client):
    """The /audit/ view returns exactly the __date-equivalent rows of the facility."""
    facility = _make_facility()
    user = _make_user(facility)
    entries = _boundary_entries(facility, user)
    expected = _date_lookup_pks(facility=facility)
    assert expected == {entries["after_start"].pk, entries["before_end"].pk}

    client.force_login(user)
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(
            reverse("core:audit_log"),
            {"date_from": PROBE_DAY.isoformat(), "date_to": PROBE_DAY.isoformat()},
        )

    assert response.status_code == 200
    actual = {entry.pk for entry in response.context["page_obj"].object_list}
    assert actual == expected
    # Off-by-one probes: 23:59:59 stays, 00:00:00 of the next day goes.
    assert entries["before_end"].pk in actual
    assert entries["at_end"].pk not in actual
    assert entries["before_start"].pk not in actual
    _assert_sargable_range(_auditlog_select_sql(ctx))
