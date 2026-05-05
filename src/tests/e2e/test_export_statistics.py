"""E2E-Tests: Statistik-Export (CSV/PDF/Jugendamt) und Zugriffskontrolle."""

import pytest

pytestmark = pytest.mark.e2e


class TestCSVExportE2E:
    """CSV-Export-Button → Download."""

    @pytest.mark.smoke
    def test_csv_export_button_visible(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")
        assert page.locator("a:has-text('CSV-Export')").is_visible()

    @pytest.mark.smoke
    def test_csv_download(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")

        with page.expect_download() as download_info:
            page.locator("a:has-text('CSV-Export')").click()
        download = download_info.value
        assert download.suggested_filename.endswith(".csv")


class TestPDFExportE2E:
    """PDF-Export-Button → Download."""

    def test_pdf_export_button_visible(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")
        assert page.locator("a:has-text('Halbjahresbericht (PDF)')").is_visible()

    def test_pdf_download(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")

        with page.expect_download() as download_info:
            page.locator("a:has-text('Halbjahresbericht (PDF)')").click()
        download = download_info.value
        assert download.suggested_filename.endswith(".pdf")


class TestJugendamtExportE2E:
    """Jugendamt-Export → Download."""

    def test_jugendamt_export_button_visible(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")
        assert page.locator("a:has-text('Jugendamt-Bericht')").is_visible()

    def test_jugendamt_download(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")

        with page.expect_download() as download_info:
            page.locator("a:has-text('Jugendamt-Bericht')").click()
        download = download_info.value
        assert download.suggested_filename.startswith("jugendamt_")


class TestStatisticsAccessControl:
    """Staff-User: kein Zugriff auf Statistik."""

    def test_staff_no_access(self, staff_page, base_url):
        page = staff_page
        page.goto(f"{base_url}/")

        # Statistik-Link sollte nicht sichtbar sein
        nav = page.locator("nav[aria-label='Hauptnavigation']")
        assert nav.locator("a:has-text('Statistik')").count() == 0

        # Direkter Zugriff → 403
        response = page.goto(f"{base_url}/statistics/")
        assert response.status == 403


class TestAssistantCannotAccessStatistics:
    """Assistenz-Rolle hat keinen Zugriff auf Statistiken."""

    def test_assistant_no_statistics_access(self, assistant_page, base_url):
        resp = assistant_page.goto(f"{base_url}/statistics/")
        assert resp.status == 403

    def test_assistant_no_statistics_nav_link(self, assistant_page):
        nav = assistant_page.locator("nav[aria-label='Hauptnavigation']")
        assert nav.locator("a:has-text('Statistik')").count() == 0
