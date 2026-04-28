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

    def test_charts_no_canvas_reuse_error_on_period_change(self, authenticated_page, base_url):
        """Regression #509: Periodenwechsel darf keinen Chart.js
        'Canvas is already in use'-Fehler in der Konsole erzeugen."""
        page = authenticated_page
        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        page.goto(f"{base_url}/statistics/")
        page.wait_for_selector("#chart-contacts", timeout=5000)

        # Drei Periodenwechsel hintereinander stressen den Lifecycle.
        # get_by_role mit exact=True, damit "Jahr" nicht "Halbjahr" matched.
        for label in ("Quartal", "Halbjahr", "Jahr"):
            with page.expect_response(lambda r: re.search(r"/statistics/chart-data/", r.url)):
                page.get_by_role("button", name=label, exact=True).click()
            page.wait_for_selector("#chart-contacts", timeout=10000)

        canvas_reuse_errors = [e for e in console_errors if "Canvas is already in use" in e]
        assert canvas_reuse_errors == [], f"Chart.js canvas-reuse error on period change: {canvas_reuse_errors}"

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
