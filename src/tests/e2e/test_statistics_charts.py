"""E2E-Tests: Analytics Charts — Chart.js-Visualisierungen auf der Statistik-Seite."""

import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


class TestStatisticsCharts:
    """Charts: Canvas-Elemente, API-Aufrufe, Filter."""

    def test_chart_canvases_visible(self, authenticated_page, base_url):
        """Drei Chart-Canvas-Elemente werden auf der Statistik-Seite gerendert."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/?zeitraum=jahr")
        # Auf das erste Chart-Canvas warten — signalisiert, dass HTMX-Response
        # + Chart.js-Init abgeschlossen sind.
        page.locator("#chart-contacts").wait_for(state="visible", timeout=10000)

        assert page.locator("#chart-contacts").is_visible()
        assert page.locator("#chart-doc-types").is_visible()
        assert page.locator("#chart-age-clusters").is_visible()

    def test_chart_headings_visible(self, authenticated_page, base_url):
        """Chart-Überschriften sind sichtbar."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/?zeitraum=jahr")
        page.locator("h2:has-text('Kontakte im Zeitverlauf')").wait_for(state="visible", timeout=5000)

        assert page.locator("h2:has-text('Kontakte im Zeitverlauf')").is_visible()
        assert page.locator("h2:has-text('Dokumentationstypen')").is_visible()
        assert page.locator("h2:has-text('Altersgruppen')").is_visible()

    def test_chart_data_api_called(self, authenticated_page, base_url):
        """Chart-Daten-API wird beim Laden der Seite aufgerufen."""
        page = authenticated_page

        with page.expect_response(lambda r: "/statistics/chart-data/" in r.url and r.status == 200):
            page.goto(f"{base_url}/statistics/?zeitraum=jahr")

    def test_charts_update_on_period_change(self, authenticated_page, base_url):
        """Charts aktualisieren sich bei Periodenwechsel (HTMX-Swap)."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")
        page.wait_for_selector("#chart-contacts", timeout=5000)

        # Auf „Letzte 180 Tage" klicken → HTMX-Swap → Chart-API wird erneut aufgerufen
        with page.expect_response(lambda r: re.search(r"/statistics/\?", r.url)):
            page.locator("button:has-text('Letzte 180 Tage')").click()

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
        # Refs #1804: Etiketten nach der Zeitwort-Konvention (Z1/Z2).
        for label in ("Letzte 90 Tage", "Letzte 180 Tage", "Kalenderjahr"):
            with page.expect_response(lambda r: re.search(r"/statistics/chart-data/", r.url)):
                page.get_by_role("button", name=label, exact=True).click()
            page.wait_for_selector("#chart-contacts", timeout=10000)

        canvas_reuse_errors = [e for e in console_errors if "Canvas is already in use" in e]
        assert canvas_reuse_errors == [], f"Chart.js canvas-reuse error on period change: {canvas_reuse_errors}"

    def test_document_type_dropdown_visible(self, authenticated_page, base_url):
        """DocumentType-Filter-Dropdown ist vorhanden."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")
        dropdown = page.locator("#statistics-doktyp")
        dropdown.wait_for(state="visible", timeout=5000)

        assert dropdown.is_visible()
        # Dropdown sollte Optionen enthalten (Seed-Daten)
        options = dropdown.locator("option")
        assert options.count() > 1

    def test_document_type_filter_wirkt_auf_kpi_tabelle_und_url(self, authenticated_page, base_url):
        """Refs #1804 (R6/F3): Wirkungstest statt Sichtbarkeitstest.

        Der Filter war reiner Alpine-State und setzte einen Parameter, den die
        Chart-API nirgends las — KPI-Karten und Tabellen sah er ohnehin nie.
        Geprüft wird deshalb genau das, was vorher fehlte: der gewählte Typ
        landet in der URL (teilbar, reload-fest), die Kontaktzahl sinkt, und die
        Typ-Tabelle zeigt nur noch diesen einen Typ.
        """
        page = authenticated_page
        page.goto(f"{base_url}/statistics/?zeitraum=jahr")
        page.locator("#chart-contacts").wait_for(state="visible", timeout=10000)

        gesamt = int(page.locator("[data-testid='kpi-total-contacts']").inner_text())
        assert gesamt > 0, "Seed liefert Ereignisse — sonst prüft der Test nichts."

        typ_zeilen = page.locator("[data-testid='table-by-document-type'] tbody tr")
        assert typ_zeilen.count() > 1, "Für einen Wirkungstest braucht es mindestens zwei Typen."
        erster_typ = typ_zeilen.first.locator("td").first.inner_text().strip()

        with page.expect_response(lambda r: "/statistics/" in r.url and "chart-data" not in r.url):
            page.locator("#statistics-doktyp").select_option(label=erster_typ)

        # Der Zustand steht in der URL, nicht nur im DOM.
        page.wait_for_url(lambda url: "doktyp=" in url, timeout=10000)

        gefiltert_locator = page.locator("[data-testid='kpi-total-contacts']")
        expect(gefiltert_locator).not_to_have_text(str(gesamt), timeout=10000)
        assert int(gefiltert_locator.inner_text()) < gesamt

        # Die Tabelle unter den Diagrammen folgt demselben Filter — genau die
        # Divergenz, die #1804 schliesst.
        expect(page.locator("[data-testid='table-by-document-type'] tbody tr")).to_have_count(1, timeout=10000)

    def test_data_source_legend_visible(self, authenticated_page, base_url):
        """Datenquellen-Legende (Live-Daten / Snapshot) ist sichtbar."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/?zeitraum=jahr")
        # Nach Charts-Render erscheint die Datenquellen-Legende.
        page.locator("text=Live-Daten").wait_for(state="visible", timeout=10000)

        assert page.locator("text=Live-Daten").is_visible()
        assert page.locator("text=Snapshot").is_visible()

    def test_charts_no_print(self, authenticated_page, base_url):
        """Chart-Sektion hat no-print-Klasse."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")
        chart_section = page.locator("[x-data*='statisticsCharts']")
        chart_section.wait_for(state="attached", timeout=5000)

        classes = chart_section.get_attribute("class") or ""
        assert "no-print" in classes
