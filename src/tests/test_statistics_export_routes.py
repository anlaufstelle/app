"""Statistics area: every file leaves through a logged release route.

Removed special routes stay gone, the old external-report address only
redirects to the report card, and every remaining file-producing route writes
a release audit row (artifact_released=True with a report period).
"""

from __future__ import annotations

from datetime import date
from urllib.parse import parse_qs, urlsplit

import pytest
from django.urls import NoReverseMatch, get_resolver, reverse

from core.models import AuditLog

START = date(2026, 1, 1)
END = date(2026, 1, 31)


def _query(location: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(location).query)


@pytest.mark.django_db
class TestRemovedSpecialRoutes:
    @pytest.mark.parametrize("name", ["statistics_pdf_export", "statistics_sample_report_1_export"])
    def test_route_name_is_gone(self, name):
        with pytest.raises(NoReverseMatch):
            reverse(f"core:{name}")

    @pytest.mark.parametrize("path", ["/statistics/export/pdf/", "/statistics/export/sample-report-1/"])
    def test_old_address_does_not_deliver_a_file(self, client, lead_user, path):
        client.force_login(lead_user)
        before = AuditLog.objects.filter(action=AuditLog.Action.EXPORT).count()

        response = client.get(path, {"date_from": START, "date_to": END})

        assert response.status_code == 404
        assert "attachment" not in response.get("Content-Disposition", "")
        assert AuditLog.objects.filter(action=AuditLog.Action.EXPORT).count() == before

    def test_internal_switch_is_not_honoured_anywhere(self, client, lead_user):
        client.force_login(lead_user)
        response = client.get("/statistics/export/pdf/", {"date_from": START, "date_to": END, "internal": "1"})
        assert response.status_code == 404


@pytest.mark.django_db
class TestExternalReportRedirect:
    """Former external report address: redirect only, never a file."""

    def test_html_redirects_to_the_report_card_with_period(self, client, lead_user):
        client.force_login(lead_user)

        response = client.get("/statistics/external/", {"zeitraum": "letzte90"})

        assert response.status_code == 302
        assert urlsplit(response["Location"]).path == reverse("core:statistics")
        query = _query(response["Location"])
        assert query["ansicht"] == ["datensaetze"]
        assert query["bericht"] == ["general-key-figures"]
        assert query["zeitraum"] == ["letzte90"]

    def test_year_and_custom_range_are_carried(self, client, lead_user):
        client.force_login(lead_user)

        year = client.get("/statistics/external/", {"zeitraum": "jahr", "year": "2025"})
        custom = client.get(
            "/statistics/external/", {"zeitraum": "frei", "date_from": "2026-01-05", "date_to": "2026-01-20"}
        )

        assert _query(year["Location"])["year"] == ["2025"]
        query = _query(custom["Location"])
        assert query["zeitraum"] == ["frei"]
        assert query["date_from"] == ["2026-01-05"]
        assert query["date_to"] == ["2026-01-20"]

    def test_json_format_redirects_and_delivers_no_file(self, client, lead_user):
        client.force_login(lead_user)

        response = client.get("/statistics/external/", {"format": "json", "zeitraum": "letzte30"})

        assert response.status_code == 302
        assert not response["Content-Type"].startswith("application/json")
        assert "format" not in _query(response["Location"])
        assert _query(response["Location"])["bericht"] == ["general-key-figures"]

    def test_redirect_writes_no_audit_row(self, client, lead_user):
        client.force_login(lead_user)
        before = AuditLog.objects.count()

        client.get("/statistics/external/", {"format": "json"})
        client.get("/statistics/external/")

        assert AuditLog.objects.filter(target_type="ExternalReport").count() == 0
        assert AuditLog.objects.count() == before

    def test_facility_admin_follows_the_redirect(self, client, admin_user):
        client.force_login(admin_user)

        response = client.get("/statistics/external/", {"zeitraum": "letzte30"})

        assert response.status_code == 302
        assert _query(response["Location"])["bericht"] == ["general-key-figures"]

    def test_staff_gets_no_redirect(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get("/statistics/external/", {"format": "json"})
        assert response.status_code == 403

    def test_anonymous_goes_to_login(self, client):
        response = client.get("/statistics/external/", {"format": "json"})
        assert response.status_code == 302
        assert "/login/" in response["Location"]

    def test_invalid_period_is_a_client_error(self, client, lead_user):
        client.force_login(lead_user)
        response = client.get(
            "/statistics/external/", {"zeitraum": "frei", "date_from": "2020-01-01", "date_to": "2030-01-01"}
        )
        assert response.status_code == 400


def _statistics_route_names() -> set[str]:
    names: set[str] = set()
    for pattern in get_resolver().url_patterns:
        for sub in getattr(pattern, "url_patterns", []):
            name = getattr(sub, "name", None)
            if name and name.startswith("statistics_"):
                names.add(name)
    return names


# Every statistics route is classified. A new route fails the guard until it is
# added to exactly one set - file routes additionally need an audit test below.
PAGE_ROUTES = {
    "statistics",
    "statistics_chart_data",
    "statistics_report_panel",
    "statistics_report_releases",
    "statistics_external_report",  # redirect only
}
FILE_ROUTES = {"statistics_csv_export", "statistics_report_export"}


def test_every_statistics_route_is_classified():
    names = _statistics_route_names() | {"statistics"}
    assert names == PAGE_ROUTES | FILE_ROUTES


@pytest.mark.django_db
class TestEveryFileRouteIsAReleaseRoute:
    def _released(self):
        return AuditLog.objects.filter(
            action=AuditLog.Action.EXPORT, detail__artifact_released=True, detail__has_key="date_from"
        )

    def test_events_csv_writes_a_release_row(self, client, lead_user):
        client.force_login(lead_user)
        before = self._released().count()

        response = client.get(
            reverse("core:statistics_csv_export"),
            {"date_from": START, "date_to": END, "recipient": "Stadt", "purpose": "Nachweis"},
        )

        assert response.status_code == 200
        assert "attachment" in response["Content-Disposition"]
        assert self._released().count() == before + 1
        row = self._released().order_by("-timestamp").first()
        assert row.detail["recipient"] == "Stadt"
        assert row.detail["date_from"] == str(START)

    @pytest.mark.parametrize("slug", ["general-key-figures", "sample-report-1"])
    @pytest.mark.parametrize("fmt", ["csv", "json", "xml", "pdf"])
    def test_report_export_writes_a_release_row_with_backend_identity(self, client, lead_user, slug, fmt):
        client.force_login(lead_user)
        before = self._released().count()

        response = client.get(
            reverse("core:statistics_report_export"),
            {"backend": slug, "format": fmt, "date_from": START, "date_to": END, "recipient": "Stadt"},
        )

        assert response.status_code == 200
        assert "attachment" in response["Content-Disposition"]
        assert self._released().count() == before + 1
        row = self._released().order_by("-timestamp").first()
        assert row.detail["backend"] == slug
        assert row.detail["format"] == fmt
        assert row.detail["recipient"] == "Stadt"

    def test_failed_render_leaves_neither_file_nor_release(self, client, lead_user, monkeypatch):
        from core.reporting.backends.general_key_figures import GeneralKeyFiguresBackend

        def boom(self, facility, date_from, date_to):
            raise RuntimeError("backend down")

        monkeypatch.setattr(GeneralKeyFiguresBackend, "build_dataset", boom)
        client.force_login(lead_user)
        before = self._released().count()

        response = client.get(
            reverse("core:statistics_report_export"),
            {"backend": "general-key-figures", "format": "pdf", "date_from": START, "date_to": END},
        )

        assert response.status_code == 502
        assert "attachment" not in response.get("Content-Disposition", "")
        assert self._released().count() == before
