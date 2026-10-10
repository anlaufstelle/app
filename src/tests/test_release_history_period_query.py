"""Overlap warnings include older releases; display pagination has no analysis cap."""

import pytest
from django.urls import reverse

from core.models import AuditLog
from core.services.dashboard.release_log import build_release_overview
from tests.test_report_release_log import _export


@pytest.fixture
def long_release_history(facility, lead_user):
    old = _export(facility, lead_user, "2026-01-01", "2026-12-31", backend="sample-report-1")
    for _ in range(501):
        _export(facility, lead_user, "2025-01-01", "2025-01-31", backend="sample-report-1")
    newest = _export(facility, lead_user, "2026-02-01", "2026-02-28", backend="sample-report-1")
    return old, newest


@pytest.mark.django_db
def test_overlap_query_reaches_beyond_500_releases(facility, long_release_history):
    releases = build_release_overview(facility, limit=1)
    assert len(releases) == 1
    assert releases[0].overlaps == ("sample-report-1 2026-01-01 – 2026-12-31",)


@pytest.mark.django_db
def test_release_view_paginates_without_hiding_old_warnings(client, lead_user, facility, long_release_history):
    client.force_login(lead_user)
    url = reverse("core:statistics_report_releases")
    first = client.get(url)
    assert first.status_code == 200
    assert first.context["page_obj"].paginator.count == 503
    assert first.context["page_obj"].has_next()
    assert len(first.context["releases"]) <= 50
    assert first.context["releases"][0].overlaps
    last = client.get(url, {"page": first.context["page_obj"].paginator.num_pages})
    assert last.status_code == 200
    assert last.context["releases"][-1].overlaps == ("sample-report-1 2026-02-01 – 2026-02-28",)
    assert b"release-limit-note" not in first.content


@pytest.mark.django_db
def test_period_query_keeps_facility_and_artifact_boundaries(facility, other_facility, lead_user):
    _export(other_facility, lead_user, "2026-01-01", "2026-12-31", backend="sample-report-1")
    _export(facility, lead_user, "2026-01-01", "2026-12-31", backend="different-report")
    _export(facility, lead_user, "2026-02-01", "2026-02-28", backend="sample-report-1")
    assert not build_release_overview(facility, limit=1)[0].overlaps


@pytest.mark.django_db
def test_non_release_views_cannot_displace_release_candidates(facility, lead_user):
    _export(facility, lead_user, "2026-01-01", "2026-12-31", backend="sample-report-1")
    AuditLog.objects.create(
        facility=facility,
        user=lead_user,
        action=AuditLog.Action.EXPORT,
        target_type="DatasetExport",
        detail={"date_from": "2026-02-01", "date_to": "2026-02-28"},
    )
    releases = build_release_overview(facility, limit=1)
    assert len(releases) == 1
    assert not releases[0].overlaps
