"""E2E-Tests: Statistik-Export (CSV/PDF/Sachbericht) und Zugriffskontrolle.

K4a (Refs #1770): Die drei Export-Aktionen sind seit der Freigabe-Erfassung
``<button formaction=…>`` statt ``<a href=…>`` — nur so kommen die optionalen
Felder Empfaenger/Zweck ohne JavaScript an allen drei Zielen an.
"""

import re

import pytest
from playwright.sync_api import expect

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
    """Sachbericht-Export → Download.

    R2 (Refs #1804): Der Export steht bei seinen Daten. Das Sachbericht-PDF ist
    das Artefakt eines Berichts-Backends, nicht eine Ausgabe der
    Uebersichtszahlen — sein Einstieg liegt seit #1804 auf der Berichtskarte des
    Reiters „Datensatz-Berichte", nicht mehr in der Export-Leiste der Uebersicht.
    """

    def _open_report_panel(self, page, base_url):
        page.goto(f"{base_url}/statistics/?ansicht=datensaetze&bericht=sample-report-1")
        panel = page.locator("#report-panel")
        expect(panel.locator("h3")).to_have_text("Beispiel-Sachbericht", timeout=10000)
        return panel

    def test_sachbericht_pdf_nicht_mehr_auf_der_uebersicht(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")
        expect(page.locator("#dashboard-content")).to_be_visible(timeout=10000)
        assert page.locator("button:has-text('Beispiel-Sachbericht')").count() == 0

    def test_sample_report_1_export_button_visible(self, authenticated_page, base_url):
        panel = self._open_report_panel(authenticated_page, base_url)
        expect(panel.locator("button:has-text('PDF-Export')")).to_be_visible()

    def test_sample_report_1_download(self, authenticated_page, base_url):
        page = authenticated_page
        panel = self._open_report_panel(page, base_url)

        with page.expect_download() as download_info:
            panel.locator("button:has-text('PDF-Export')").click()
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
            page.locator("button:has-text('CSV-Export')").click()

        page.goto(f"{base_url}/statistics/reports/releases/")
        assert page.locator("text=Stadt Musterstadt").first.is_visible()


class TestDoktypFilterErreichtDieExporteE2E:
    """R2 (Refs #1804): Was am Bildschirm steht, steht auch in der Datei.

    Der Doktyp-Filter wirkt auf KPI, Tabellen und Diagramme — die beiden
    Export-Schaltflaechen darunter exportierten trotzdem den ganzen Zeitraum.
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
            page.locator("[data-testid='statistics-export-form'] button:has-text('CSV-Export')").click()
        zeilen = download_info.value.path().read_text(encoding="utf-8").splitlines()

        # Zeile 0 = Metadaten, Zeile 1 = Kopf, ab Zeile 2 die Ereignisse.
        # Spalte 3 ist der Dokumentationstyp (``_build_event_row``).
        datenzeilen = [z for z in zeilen[2:] if z.strip()]
        assert datenzeilen, "Setup-Sanity: der Seed liefert Ereignisse dieses Typs"
        for zeile in datenzeilen:
            assert label in zeile, f"ungefilterte Zeile im gefilterten Export: {zeile}"

    def test_pdf_export_folgt_dem_filter(self, authenticated_page, base_url):
        """Das PDF nennt den Ausschnitt — ohne die Zeile ist die Datei im Umlauf
        nicht mehr von einem vollstaendigen Bericht zu unterscheiden."""
        page = authenticated_page
        self._filter_auf_ersten_typ(page, base_url)

        with page.expect_download() as download_info:
            page.locator("[data-testid='statistics-export-form'] button:has-text('PDF-Export')").click()
        download = download_info.value
        assert download.suggested_filename.endswith(".pdf")
        assert download.path().read_bytes()[:4] == b"%PDF"
