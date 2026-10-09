"""E2E-Tests: Statistik-Export (Ereignisliste, Berichts-PDF) und Zugriffskontrolle.

K4a (Refs #1770): Die Export-Aktionen sind ``<button formaction=…>`` statt
``<a href=…>`` — nur so kommen die optionalen Felder Empfaenger/Zweck ohne
JavaScript am Ziel an. Seit #2505 sitzen alle Ziele in der einen Exportleiste
(``_export_bar.html``): die Uebersicht bietet nur die interne Ereignisliste, die
Datensatz-Berichte CSV, JSON, XML und PDF.
"""

import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

EVENT_LIST = "button:has-text('Ereignisliste')"


class TestCSVExportE2E:
    """Interne Ereignisliste (CSV) → Download."""

    @pytest.mark.smoke
    def test_csv_export_button_visible(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")
        assert page.locator(EVENT_LIST).is_visible()

    @pytest.mark.smoke
    def test_csv_download(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")

        with page.expect_download() as download_info:
            page.locator(EVENT_LIST).click()
        download = download_info.value
        assert download.suggested_filename.endswith(".csv")


class TestOverviewHasNoFileExportExceptEventList:
    """Die Uebersicht bleibt intern: keine Formatbuttons, kein PDF."""

    def test_overview_has_no_pdf_or_report_formats(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")
        expect(page.get_by_test_id("statistics-export-form")).to_be_visible(timeout=10000)

        form = page.get_by_test_id("statistics-export-form")
        assert form.locator("button:has-text('PDF-Export')").count() == 0
        assert form.locator("button:has-text('JSON-Export')").count() == 0
        assert form.locator("button:has-text('XML-Export')").count() == 0


class TestSampleReport1ExportE2E:
    """Sachbericht-Export → Download ueber die Exportleiste.

    R2 (Refs #1804): Der Export steht bei seinen Daten. Seit #2505 gibt es dafuer
    keine Sonderroute mehr — das PDF kommt wie CSV, JSON und XML aus der einen
    Exportleiste unter den Berichtskarten (``statistics_report_export``).
    """

    def _open_report(self, page, base_url):
        page.goto(f"{base_url}/statistics/?ansicht=datensaetze&bericht=sample-report-1")
        expect(page.locator("#report-panel h3")).to_have_text("Beispiel-Sachbericht", timeout=10000)
        return page.get_by_test_id("export-bar")

    def test_sachbericht_pdf_nicht_auf_der_uebersicht(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")
        expect(page.locator("#dashboard-content")).to_be_visible(timeout=10000)
        assert page.locator("button:has-text('Beispiel-Sachbericht')").count() == 0

    def test_export_bar_offers_all_four_formats(self, authenticated_page, base_url):
        bar = self._open_report(authenticated_page, base_url)
        for label in ("CSV-Export", "JSON-Export", "XML-Export", "PDF-Export"):
            expect(bar.locator(f"button:has-text('{label}')")).to_be_visible()

    def test_sample_report_1_pdf_download(self, authenticated_page, base_url):
        page = authenticated_page
        bar = self._open_report(page, base_url)

        with page.expect_download() as download_info:
            bar.locator("button:has-text('PDF-Export')").click()
        download = download_info.value
        assert download.suggested_filename.startswith("sample-report-1_")
        assert download.suggested_filename.endswith(".pdf")


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
        """Refs #1804: Einstieg ist der Subnav-Reiter, nicht mehr der Textlink im
        Seitenfuß der Übersicht (der ist mit dem Reiterschnitt entfallen)."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")

        page.locator('[data-testid="subnav-tab-statistik_releases"]').click()
        # Refs #1804 (§3.5): der Reiter-Link fuehrt den aufgeloesten Zeitraum
        # mit — die Ziel-URL traegt also eine Query (z. B. ?zeitraum=letzte30).
        page.wait_for_url(re.compile(r"/statistics/reports/releases/(\?.*)?$"))

        assert page.locator("h1:has-text('Freigabe-Übersicht')").is_visible()

    def test_export_mit_empfaenger_erscheint_in_der_uebersicht(self, authenticated_page, base_url):
        """Der Weg von der Erfassung bis in die Übersicht — über das
        EXPORT-Audit-Detail, ohne eigenen Schreibpfad."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")

        page.fill("#export-recipient", "Stadt Musterstadt")
        page.fill("#export-purpose", "Verwendungsnachweis 2026")
        with page.expect_download():
            page.locator(EVENT_LIST).click()

        page.goto(f"{base_url}/statistics/reports/releases/")
        assert page.locator("text=Stadt Musterstadt").first.is_visible()


class TestDoktypFilterErreichtDieExporteE2E:
    """R2 (Refs #1804): Was am Bildschirm steht, steht auch in der Datei.

    Der Doktyp-Filter wirkt auf KPI, Tabellen und Diagramme — die
    Ereignisliste darunter exportierte trotzdem den ganzen Zeitraum.
    Der Fehler ist nur im echten Klickpfad sichtbar: Filter setzen (HTMX-Swap),
    dann exportieren. Ein Server-Test der Vollseite sieht ihn nicht, weil er
    das versteckte Feld des Export-Formulars nie mit dem Swap-Stand vergleicht.
    """

    def _filter_auf_ersten_typ(self, page, base_url):
        """Ersten echten Dokumentationstyp waehlen und auf den Swap warten."""
        page.goto(f"{base_url}/statistics/")
        select = page.locator("#statistics-doktyp")
        select.wait_for(timeout=30_000)
        # Option 0 ist „Alle Dokumentationstypen" — der erste echte Typ steht danach.
        slug = select.locator("option").nth(1).get_attribute("value")
        label = select.locator("option").nth(1).inner_text()
        with page.expect_response(lambda r: "/statistics/" in r.url and "chart-data" not in r.url):
            select.select_option(slug)
        # Nach dem Swap traegt das Export-Formular den Filter als verstecktes Feld.
        expect(page.locator("[data-testid='statistics-export-form'] input[name='doktyp']")).to_have_attribute(
            "value", slug, timeout=10_000
        )
        return slug, label.strip()

    def test_csv_export_enthaelt_nur_den_gefilterten_typ(self, authenticated_page, base_url):
        page = authenticated_page
        _slug, label = self._filter_auf_ersten_typ(page, base_url)

        with page.expect_download() as download_info:
            page.locator(f"[data-testid='statistics-export-form'] {EVENT_LIST}").click()
        zeilen = download_info.value.path().read_text(encoding="utf-8").splitlines()

        # Zeile 0 = Metadaten, Zeile 1 = Kopf, ab Zeile 2 die Ereignisse.
        # Spalte 3 ist der Dokumentationstyp (``_build_event_row``).
        datenzeilen = [z for z in zeilen[2:] if z.strip()]
        assert datenzeilen, "Setup-Sanity: der Seed liefert Ereignisse dieses Typs"
        for zeile in datenzeilen:
            assert label in zeile, f"ungefilterte Zeile im gefilterten Export: {zeile}"
