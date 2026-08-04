"""E2E-Tests: Statistik-Export (CSV/PDF/Sachbericht) und Zugriffskontrolle.

K4a (Refs #1770): Die drei Export-Aktionen sind seit der Freigabe-Erfassung
``<button formaction=…>`` statt ``<a href=…>`` — nur so kommen die optionalen
Felder Empfaenger/Zweck ohne JavaScript an allen drei Zielen an.
"""

import pytest

pytestmark = pytest.mark.e2e


class TestCSVExportE2E:
    """CSV-Export-Button → Download."""

    @pytest.mark.smoke
    def test_csv_export_button_visible(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")
        assert page.locator("button:has-text('CSV-Export')").is_visible()

    @pytest.mark.smoke
    def test_csv_download(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")

        with page.expect_download() as download_info:
            page.locator("button:has-text('CSV-Export')").click()
        download = download_info.value
        assert download.suggested_filename.endswith(".csv")


class TestPDFExportE2E:
    """PDF-Export-Button → Download."""

    def test_pdf_export_button_visible(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")
        assert page.locator("button:has-text('PDF-Export')").is_visible()

    def test_pdf_download(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")

        with page.expect_download() as download_info:
            page.locator("button:has-text('PDF-Export')").click()
        download = download_info.value
        assert download.suggested_filename.endswith(".pdf")


class TestSampleReport1ExportE2E:
    """Sachbericht-Export → Download."""

    def test_sample_report_1_export_button_visible(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")
        assert page.locator("button:has-text('Beispiel-Sachbericht')").is_visible()

    def test_sample_report_1_download(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")

        with page.expect_download() as download_info:
            page.locator("button:has-text('Beispiel-Sachbericht')").click()
        download = download_info.value
        assert download.suggested_filename.startswith("sample-report-1_")


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


class TestFreigabeUebersichtE2E:
    """K4a (Refs #1770): Freigabe-Übersicht herausgegebener Berichtszeiträume.

    Sie kann die Zeitraum-Differenz nicht verhindern (akzeptierte
    Definitionsgrenze) — sie macht überlappende Herausgaben sichtbar.
    """

    def test_freigabe_uebersicht_ist_von_der_statistik_erreichbar(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")

        page.locator("a:has-text('Freigabe-Übersicht')").click()
        page.wait_for_url("**/statistics/reports/releases/")

        assert page.locator("h1:has-text('Freigabe-Übersicht')").is_visible()

    def test_export_mit_empfaenger_erscheint_in_der_uebersicht(self, authenticated_page, base_url):
        """Der Weg von der Erfassung bis in die Übersicht — über das
        EXPORT-Audit-Detail, ohne eigenen Schreibpfad."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")

        page.fill("#export-recipient", "Stadt Musterstadt")
        page.fill("#export-purpose", "Verwendungsnachweis 2026")
        with page.expect_download():
            page.locator("button:has-text('CSV-Export')").click()

        page.goto(f"{base_url}/statistics/reports/releases/")
        assert page.locator("text=Stadt Musterstadt").first.is_visible()
