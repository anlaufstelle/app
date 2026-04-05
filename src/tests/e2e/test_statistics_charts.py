"""E2E-Tests: Analytics Charts — Chart.js-Visualisierungen auf der Statistik-Seite."""

import re

import pytest

pytestmark = pytest.mark.e2e


class TestStatisticsCharts:
    """Charts: Canvas-Elemente, API-Aufrufe, Filter."""

    def test_chart_canvases_visible(self, authenticated_page, base_url):
        """Drei Chart-Canvas-Elemente werden auf der Statistik-Seite gerendert."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/?period=year")
        page.wait_for_timeout(2000)

        assert page.locator("#chart-contacts").is_visible()
        assert page.locator("#chart-doc-types").is_visible()
        assert page.locator("#chart-age-clusters").is_visible()

    def test_chart_headings_visible(self, authenticated_page, base_url):
        """Chart-Überschriften sind sichtbar."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/?period=year")
        page.wait_for_timeout(1000)

        assert page.locator("h3:has-text('Kontakte im Zeitverlauf')").is_visible()
        assert page.locator("h3:has-text('Dokumentationstypen')").is_visible()
        assert page.locator("h3:has-text('Altersgruppen')").is_visible()

    def test_chart_data_api_called(self, authenticated_page, base_url):
        """Chart-Daten-API wird beim Laden der Seite aufgerufen."""
        page = authenticated_page

        with page.expect_response(lambda r: "/statistics/chart-data/" in r.url and r.status == 200):
            page.goto(f"{base_url}/statistics/?period=year")

    def test_charts_update_on_period_change(self, authenticated_page, base_url):
        """Charts aktualisieren sich bei Periodenwechsel (HTMX-Swap)."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")
        page.wait_for_selector("#chart-contacts", timeout=5000)

        # Auf Halbjahr klicken → HTMX-Swap → Chart-API wird erneut aufgerufen
        with page.expect_response(lambda r: re.search(r"/statistics/\?", r.url)):
            page.locator("button:has-text('Halbjahr')").click()

        # Nach HTMX-Swap warten bis Alpine die Charts neu initialisiert
        page.wait_for_selector("#chart-contacts", timeout=10000)

    def test_document_type_dropdown_visible(self, authenticated_page, base_url):
        """DocumentType-Filter-Dropdown ist vorhanden."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")
        page.wait_for_timeout(1000)

        dropdown = page.locator("select").filter(has_text="Alle Dokumentationstypen")
        assert dropdown.is_visible()
        # Dropdown sollte Optionen enthalten (Seed-Daten)
        options = dropdown.locator("option")
        assert options.count() > 1

    def test_data_source_legend_visible(self, authenticated_page, base_url):
        """Datenquellen-Legende (Live-Daten / Snapshot) ist sichtbar."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/?period=year")
        page.wait_for_timeout(2000)

        assert page.locator("text=Live-Daten").is_visible()
        assert page.locator("text=Snapshot").is_visible()

    def test_charts_no_print(self, authenticated_page, base_url):
        """Chart-Sektion hat no-print-Klasse."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")
        page.wait_for_timeout(1000)

        chart_section = page.locator("[x-data*='statisticsCharts']")
        classes = chart_section.get_attribute("class") or ""
        assert "no-print" in classes
