"""E2E-Tests: Berichts-Panel, Backend-Auswahl und Format-Downloads (M5 P8, Refs #1061).

Deckt die Benutzersicht der Berichte-Sektion auf ``/statistics/`` ab: das per
Lazy-Discovery befuellte Backend-Dropdown, den HTMX-Swap des Vorschau-Panels
(``core:statistics_report_panel``) und die drei Format-Downloads
(``core:statistics_report_export``). Die inhaltlichen Garantien der Backends
(K-Anon-Suppression, Vollstaendigkeits-Semantik, Audit-Eintrag) haengen an den
Unit-Tests (``src/tests/test_reporting_*.py``) — hier geht es um Verdrahtung:
Dropdown → Panel → Downloads → Zeitraum-Durchreichung, inklusive Rollen-Sicht
(LEAD_PLUS) und Konsolen-Sauberkeit.

Wait-Strategie nach ``docs/e2e-runbook.md`` § 8: ``expect_response`` wartet nur
auf die HTMX-Antwort, der anschliessende DOM-Swap wird ausschliesslich ueber
pollende ``expect()``-Assertions abgewartet — nie ``networkidle``, nie
``wait_for_function`` mit asynchroner Bedingung.
"""

import re

import pytest
from playwright.sync_api import expect

from ._helpers import track_console_errors

pytestmark = pytest.mark.e2e

# HTMX-Endpunkt des Vorschau-Panels (``core:statistics_report_panel``).
PANEL_URL_FRAGMENT = "/statistics/reports/panel/"


def _open_statistics(page, base_url):
    """Statistik-Seite oeffnen und das Backend-Dropdown der Berichte-Sektion liefern."""
    page.goto(f"{base_url}/statistics/", wait_until="domcontentloaded")
    expect(page.locator("#report-section")).to_be_visible(timeout=10000)
    return page.locator("#report-section select[name='backend']")


def _select_backend(page, slug):
    """Backend waehlen, HTMX-Antwort abwarten, gerendertes Panel zurueckgeben.

    Der ``expect_response``-Block endet mit der Server-Antwort; auf den DOM-Swap
    wartet erst die pollende ``expect()``-Assertion auf die Panel-Ueberschrift
    (Refs #761/#849).
    """
    select = page.locator("#report-section select[name='backend']")
    with page.expect_response(lambda r: PANEL_URL_FRAGMENT in r.url) as response_info:
        select.select_option(slug)
    assert response_info.value.status == 200, f"Panel-Request fehlgeschlagen: {response_info.value.status}"

    panel = page.locator("#report-panel")
    expect(panel.locator("h3")).to_be_visible(timeout=10000)
    return panel


class TestReportSectionVisibility:
    """Berichte-Sektion + Backend-Dropdown auf der Statistik-Seite."""

    @pytest.mark.smoke
    def test_report_section_visible(self, authenticated_page, base_url):
        """Admin sieht die Berichte-Sektion mit Ueberschrift und Dropdown."""
        page = authenticated_page
        select = _open_statistics(page, base_url)

        expect(page.locator("#report-section h2")).to_have_text("Berichte")
        expect(select).to_be_visible()

    def test_dropdown_offers_registered_backends(self, authenticated_page, base_url):
        """Beide registrierten Backends stehen zur Wahl (Auto-Discovery der Registry).

        Das Beispiel-Backend (``example``) taucht ohne View-/Template-Aenderung
        auf — genau das ist der Copy-Vorlage-Vertrag aus M5 P6.
        """
        page = authenticated_page
        select = _open_statistics(page, base_url)

        options = select.locator("option")
        values = [options.nth(i).get_attribute("value") for i in range(options.count())]
        # Leerwert = Platzhalter "Bericht wählen …"; Backends nach Slug sortiert.
        # Die Menge ist bewusst eingefroren: das Dropdown ist eine
        # nutzersichtbare Flaeche — ein neu registriertes Backend soll hier
        # sichtbar nachgezogen werden statt still aufzutauchen. Wer ein
        # Backend ergaenzt, aktualisiert diese Zeile mit.
        assert values == ["", "example", "sample-report-1"], f"Unerwartete Backend-Auswahl: {values}"

        expect(select.locator("option[value='sample-report-1']")).to_have_text("Beispiel-Sachbericht")
        expect(select.locator("option[value='example']")).to_have_text("Beispiel: Ereignisse je Dokumenttyp")

    def test_lead_sees_report_section(self, lead_page, base_url):
        """Lead (emma) sieht die Sektion ebenfalls — die Seite ist LEAD_PLUS-gated."""
        page = lead_page
        select = _open_statistics(page, base_url)
        expect(select).to_be_visible()

        panel = _select_backend(page, "sample-report-1")
        expect(panel.locator("h3")).to_have_text("Beispiel-Sachbericht")

    def test_lead_can_download_report(self, lead_page, base_url):
        """Lead darf den Export ziehen (Server erzwingt LEAD_PLUS auf der Export-View)."""
        page = lead_page
        _open_statistics(page, base_url)
        panel = _select_backend(page, "sample-report-1")

        with page.expect_download() as download_info:
            panel.locator("button:has-text('CSV-Export')").click()
        assert download_info.value.suggested_filename.startswith("sample-report-1_")


class TestReportPanelPreview:
    """HTMX-Vorschau: Panel-Inhalt je Backend."""

    @pytest.mark.smoke
    def test_sample_report_1_panel_renders_preview(self, authenticated_page, base_url):
        """Auswahl "Beispiel-Sachbericht" → Panel mit Titel, Zeitraum und Vorschau-Tabelle."""
        page = authenticated_page
        _open_statistics(page, base_url)
        panel = _select_backend(page, "sample-report-1")

        expect(panel.locator("h3")).to_have_text("Beispiel-Sachbericht")
        expect(panel.locator("p").first).to_contain_text("Zeitraum")
        # Spaltenkoepfe des Aggregat-Backends (CSS ``uppercase`` — Vergleich
        # daher case-insensitiv gegen die Template-Labels).
        headers = panel.locator("thead th")
        expect(headers.first).to_have_text(re.compile(r"^kategorie$", re.IGNORECASE))
        expect(headers.nth(1)).to_have_text(re.compile(r"^anzahl$", re.IGNORECASE))
        assert panel.locator("tbody tr").count() > 0

    def test_panel_shows_k_anonymity_footnote(self, authenticated_page, base_url):
        """Die K-Anon-Fussnote steht am Panel — die Vorschau ist export-paritaetisch."""
        page = authenticated_page
        _open_statistics(page, base_url)
        panel = _select_backend(page, "sample-report-1")

        expect(panel).to_contain_text("Werte unter der Kleinstfallzahl-Schwelle werden unterdrückt")

    def test_panel_weist_die_live_datenquelle_aus(self, authenticated_page, base_url):
        """Refs #1743: Die Vorschau liest live und sagt das — sonst stehen ihre
        Zahlen und die des hybrid gelesenen Sachbericht-PDFs unkommentiert
        nebeneinander.
        """
        page = authenticated_page
        _open_statistics(page, base_url)
        panel = _select_backend(page, "sample-report-1")

        expect(panel).to_contain_text("Datenquelle")
        expect(panel).to_contain_text("Live-Abfrage zum Erstellungszeitpunkt")

    def test_panel_shows_completeness_area(self, authenticated_page, base_url):
        """Der Vollstaendigkeits-Bereich rendert in genau einer seiner beiden Formen.

        Ob Hinweise auftreten, haengt an den Seed-Daten im gewaehlten Zeitraum
        (``validate_completeness`` meldet nur Luecken > 0) — geprueft wird darum
        die Existenz des Bereichs: entweder Hinweis-/Info-Badges oder der
        "keine Probleme"-Kasten. Ein entfallener Block faellt so auf, ohne den
        Test an tagesabhaengige Zahlen zu binden.
        """
        page = authenticated_page
        _open_statistics(page, base_url)
        panel = _select_backend(page, "sample-report-1")

        badges = panel.locator("span", has_text=re.compile(r"^(Hinweis|Info)$")).count()
        clean_box = panel.get_by_text("Keine Vollständigkeitsprobleme erkannt").count()
        assert badges > 0 or clean_box == 1, "Weder Vollstaendigkeits-Hinweise noch der 'keine Probleme'-Kasten"

    def test_example_backend_panel_renders(self, authenticated_page, base_url):
        """Auswahl "Beispiel: Ereignisse je Dokumenttyp" → eigenes Panel (Zeilen-Modus)."""
        page = authenticated_page
        _open_statistics(page, base_url)
        panel = _select_backend(page, "example")

        expect(panel.locator("h3")).to_have_text("Beispiel: Ereignisse je Dokumenttyp")
        expect(panel.locator("thead th").first).to_have_text(re.compile(r"^dokumenttyp$", re.IGNORECASE))
        # ``mode == "rows"`` blendet die Zeilen-Zaehlung ein (max. 10 Vorschauzeilen).
        expect(panel).to_contain_text("Zeilen")

    def test_backend_switch_replaces_panel(self, authenticated_page, base_url):
        """Zweite Auswahl ersetzt das Panel vollstaendig (kein Rest des ersten Backends)."""
        page = authenticated_page
        _open_statistics(page, base_url)
        _select_backend(page, "sample-report-1")
        panel = _select_backend(page, "example")

        expect(panel.locator("h3")).to_have_text("Beispiel: Ereignisse je Dokumenttyp")
        assert panel.locator("h3:has-text('Beispiel-Sachbericht')").count() == 0


class TestReportDownloads:
    """Format-Downloads aus dem Panel (CSV/JSON/XML)."""

    @pytest.mark.parametrize(
        ("button_text", "suffix"),
        [("CSV-Export", ".csv"), ("JSON-Export", ".json"), ("XML-Export", ".xml")],
    )
    def test_sample_report_1_download_per_format(self, authenticated_page, base_url, button_text, suffix):
        """Jede Format-Schaltflaeche liefert ``sample-report-1_<von>_<bis>.<ext>``."""
        page = authenticated_page
        _open_statistics(page, base_url)
        panel = _select_backend(page, "sample-report-1")

        with page.expect_download() as download_info:
            panel.locator(f"button:has-text('{button_text}')").click()
        filename = download_info.value.suggested_filename

        assert filename.startswith("sample-report-1_"), filename
        assert filename.endswith(suffix), filename

    def test_example_csv_download(self, authenticated_page, base_url):
        """Auch das Beispiel-Backend liefert einen Download (Dateiname nach Slug)."""
        page = authenticated_page
        _open_statistics(page, base_url)
        panel = _select_backend(page, "example")

        with page.expect_download() as download_info:
            panel.locator("button:has-text('CSV-Export')").click()
        filename = download_info.value.suggested_filename

        assert filename.startswith("example_"), filename
        assert filename.endswith(".csv"), filename


class TestReportPeriodPassthrough:
    """Zeitraum-Durchreichung: der oben gewaehlte Zeitraum gilt auch fuer Bericht + Export."""

    def test_period_switch_reaches_panel_and_export(self, authenticated_page, base_url):
        """Wechsel auf "Jahr" → Panel UND heruntergeladene Datei tragen den Jahresbeginn.

        Seit K4a (Refs #1770) steht der Zeitraum nicht mehr im ``href`` der
        Export-Ziele, sondern in den Hidden-Feldern des umschliessenden
        GET-Formulars; gepruefft wird deshalb das Ergebnis (Dateiname) statt
        eines Attributs.
        """
        page = authenticated_page
        _open_statistics(page, base_url)

        date_from_input = page.locator("#report-section input[name='date_from']")
        # Der Klick swappt ``#statistics-content`` inklusive Berichte-Sektion;
        # der ``chart-data``-Ausschluss haelt den Chart-Request aus dem Prädikat.
        with page.expect_response(lambda r: "/statistics/" in r.url and "chart-data" not in r.url):
            page.get_by_role("button", name="Jahr", exact=True).click()

        # Pollt bis der Swap durch ist: das versteckte Feld der Berichte-Form
        # traegt danach den 1. Januar des gewaehlten Jahres.
        expect(date_from_input).to_have_attribute("value", re.compile(r"^\d{4}-01-01$"), timeout=10000)
        year_start = date_from_input.get_attribute("value")

        panel = _select_backend(page, "sample-report-1")

        expect(panel.locator("p").first).to_contain_text("1. Januar")
        with page.expect_download() as download_info:
            panel.locator("button:has-text('CSV-Export')").click()
        assert download_info.value.suggested_filename.startswith(f"sample-report-1_{year_start}_"), (
            download_info.value.suggested_filename
        )


class TestReportPanelConsoleHygiene:
    """Keine CSP-Violations / neuen JS-Fehler durch die Berichts-Interaktion."""

    def test_no_console_errors_during_report_flow(self, authenticated_page, base_url):
        """Panel-Swap + Backend-Wechsel erzeugen ueberhaupt keinen Konsolen-Fehler.

        Bis #1747 duldete
        dieser Test namentlich den vorbestehenden Alpine-CSP-Parserfehler der
        Statistik-Seite. Der ist mit dem Entfernen des toten
        ``@htmx:after-swap.window``-Listeners weg — die Assertion ist jetzt
        strikt und schlaegt bei jeder CSP-Violation und jedem JS-Fehler an.
        """
        page = authenticated_page
        errors = track_console_errors(page)

        _open_statistics(page, base_url)
        _select_backend(page, "sample-report-1")
        _select_backend(page, "example")

        assert errors == [], f"Konsolen-Fehler in der Berichte-Sektion: {errors}"
