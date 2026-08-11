"""E2E-Tests: Berichts-Panel, Backend-Auswahl und Format-Downloads (M5 P8, Refs #1061).

Deckt die Benutzersicht der Berichte-Sektion ab — seit der Statistik-Reiterzeile
(Refs #1813) liegt sie auf dem Reiter ``/statistics/?ansicht=datensaetze`` statt unten
am Dashboard. Geprueft wird das per
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


def _open_statistics(page, base_url):
    """Berichte-Reiter oeffnen und seine Berichtsliste liefern.

    Refs #1804: ``?ansicht=datensaetze`` ist der Reiter „Datensatz-Berichte" der
    Statistik-Reiterzeile; die Auswahl liegt seither als Karten in der Liste
    (frueher ein ``<select name="backend">``).
    """
    page.goto(f"{base_url}/statistics/?ansicht=datensaetze", wait_until="domcontentloaded")
    expect(page.locator("#report-section")).to_be_visible(timeout=10000)
    return page.locator("[data-testid='report-card-list']")


def _select_backend(page, slug):
    """Berichtskarte anklicken, HTMX-Antwort abwarten, Panel zurueckgeben.

    Refs #1804: Die Auswahl ist URL-Zustand (``?bericht=<slug>``) und wird
    serverseitig gerendert — der Klick swappt ``#statistics-content``, nicht nur
    ``#report-panel``. Der ``expect_response``-Block endet mit der Server-
    Antwort; auf den DOM-Swap wartet erst die pollende ``expect()``-Assertion
    (Refs #761/#849).
    """
    karte = page.locator(f"[data-testid='report-card-{slug}']")
    # Swap-bewusstes Warten (Refs #1798-Lektion): nach einem HX-Swap steht die
    # Karte sofort im DOM, htmx bindet seine Listener aber erst in der
    # Settle-Phase. Ein Klick davor waere eine normale Navigation statt eines
    # Swaps. Python-seitig pollen (kein ``wait_for_function``, Refs
    # docs/e2e-runbook.md § 8).
    for _ in range(100):
        if karte.evaluate("el => !!el['htmx-internal-data']"):
            break
        page.wait_for_timeout(100)
    else:
        raise AssertionError("Berichtskarte wurde nicht von htmx verarbeitet (Settle blieb aus)")
    with page.expect_response(lambda r: "/statistics/" in r.url and "chart-data" not in r.url) as response_info:
        karte.click()
    assert response_info.value.status == 200, f"Berichts-Request fehlgeschlagen: {response_info.value.status}"

    panel = page.locator("#report-panel")
    expect(panel.locator("h3")).to_be_visible(timeout=10000)
    return panel


class TestReportSectionVisibility:
    """Berichte-Sektion + Backend-Dropdown auf der Statistik-Seite."""

    @pytest.mark.smoke
    def test_report_section_visible(self, authenticated_page, base_url):
        """Admin sieht die Berichte-Sektion mit Ueberschrift und Berichtsliste."""
        page = authenticated_page
        liste = _open_statistics(page, base_url)

        expect(page.locator("#report-section h2")).to_have_text("Datensatz-Berichte")
        expect(liste).to_be_visible()

    def test_liste_bietet_alle_registrierten_backends(self, authenticated_page, base_url):
        """Alle registrierten Backends stehen zur Wahl (Auto-Discovery der Registry).

        Ein neues Backend taucht ohne View-/Template-Aenderung auf — genau das
        ist der Copy-Vorlage-Vertrag aus M5 P6.
        """
        page = authenticated_page
        liste = _open_statistics(page, base_url)

        karten = liste.locator("[data-testid^='report-card-']")
        slugs = [
            (karten.nth(i).get_attribute("data-testid") or "").removeprefix("report-card-")
            for i in range(karten.count())
        ]
        # Die Menge ist bewusst eingefroren: die Berichtsliste ist eine
        # nutzersichtbare Flaeche — ein neu registriertes Backend soll hier
        # sichtbar nachgezogen werden statt still aufzutauchen. Wer ein
        # Backend ergaenzt, aktualisiert diese Zeile mit.
        #
        # Die Liste ist zugleich ein Pin auf ``is_available`` (Refs #1754):
        # jedes der sieben Beispiel-Backends haengt an Seed-/Paket-Konfiguration
        # (``ban-overview`` am Hausverbots-Typ, ``client-metrics`` an einem
        # Ereignis-Typ, ``needle-exchange-balance`` an beiden Feldvorlagen an
        # EINEM Typ, …). Faellt eines aus dieser Liste, ist entweder die
        # Verfuegbarkeitsregel oder der Seed gewandert — beides gehoert gesehen.
        assert slugs == [
            "ban-overview",
            "case-metrics",
            "client-metrics",
            "counseling-hours",
            "example",
            "monthly-trend",
            "needle-exchange-balance",
            "sample-report-1",
            "service-distribution",
        ], f"Unerwartete Backend-Auswahl: {slugs}"

        for slug, name in [
            ("sample-report-1", "Beispiel-Sachbericht"),
            ("example", "Beispiel: Ereignisse je Dokumentationstyp"),
            ("ban-overview", "Beispiel: Hausverbots-Übersicht"),
            ("case-metrics", "Beispiel: Fall-Kennzahlen"),
            ("client-metrics", "Beispiel: Personen-Kennzahlen"),
            ("counseling-hours", "Beispiel: Beratungsstunden"),
            ("monthly-trend", "Beispiel: Monatsverlauf nach Kategorie"),
            ("needle-exchange-balance", "Beispiel: Spritzentausch-Bilanz"),
            ("service-distribution", "Beispiel: Leistungs- und Krisenarten-Verteilung"),
        ]:
            expect(liste.locator(f"[data-testid='report-card-{slug}']")).to_contain_text(name)

    def test_karte_nennt_beschreibung_und_grundgesamtheit(self, authenticated_page, base_url):
        """Refs #1804 (Konzept §3.3): Der Name allein war keine Auswahlgrundlage —
        neun gleich praefigierte Eintraege ohne Erklaersatz waren ein Ratespiel."""
        page = authenticated_page
        liste = _open_statistics(page, base_url)

        karte = liste.locator("[data-testid='report-card-example']")
        expect(karte).to_contain_text("Zählt die Ereignisse des Zeitraums je Dokumentationstyp.")
        expect(karte).to_contain_text("Grundgesamtheit")
        expect(karte).to_contain_text("Wird bei jedem Aufruf live berechnet")
        expect(liste.locator("[data-testid='report-card-sample-report-1']")).to_contain_text(
            "Fließt in die Monats-Aggregate ein"
        )

    def test_auswahl_steht_in_der_url_und_ueberlebt_den_reload(self, authenticated_page, base_url):
        """Refs #1804: ``?bericht=<slug>`` ist teilbar und reload-fest — vorher war
        die Auswahl reiner Select-Zustand und nach jedem Reload weg."""
        page = authenticated_page
        _open_statistics(page, base_url)
        _select_backend(page, "example")

        page.wait_for_url(lambda url: "bericht=example" in url, timeout=10000)
        page.reload(wait_until="domcontentloaded")
        expect(page.locator("#report-panel h3")).to_have_text("Beispiel: Ereignisse je Dokumentationstyp")

    def test_lead_sees_report_section(self, lead_page, base_url):
        """Lead (emma) sieht die Sektion ebenfalls — die Seite ist LEAD_PLUS-gated."""
        page = lead_page
        liste = _open_statistics(page, base_url)
        expect(liste).to_be_visible()

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
        """Auswahl "Beispiel: Ereignisse je Dokumentationstyp" → eigenes Panel (Zeilen-Modus)."""
        page = authenticated_page
        _open_statistics(page, base_url)
        panel = _select_backend(page, "example")

        expect(panel.locator("h3")).to_have_text("Beispiel: Ereignisse je Dokumentationstyp")
        expect(panel.locator("thead th").first).to_have_text(re.compile(r"^dokumentationstyp$", re.IGNORECASE))
        # ``mode == "rows"`` blendet die Zeilen-Zaehlung ein (max. 10 Vorschauzeilen).
        expect(panel).to_contain_text("Zeilen")

    def test_client_metrics_panel_renders(self, authenticated_page, base_url):
        """Refs #1754: "Beispiel: Personen-Kennzahlen" rendert die Personen-Partition.

        Beobachtet am manuellen E2E-Server (2026-08-05): Spalten
        "Altersgruppe"/"Personen", vier Zeilen (``u18``/``18_26``/``27_plus``/
        ``unknown``) — bei Seed-Daten allesamt k-anonym unterdrueckt ("—").
        Der Test prueft deshalb **Gestalt statt Zahlen**: die Zeilen werden
        datenunabhaengig emittiert (K6-Lehre), nur ihre Werte fallen.
        """
        page = authenticated_page
        _open_statistics(page, base_url)
        panel = _select_backend(page, "client-metrics")

        expect(panel.locator("h3")).to_have_text("Beispiel: Personen-Kennzahlen")
        headers = panel.locator("thead th")
        expect(headers.first).to_have_text(re.compile(r"^altersgruppe$", re.IGNORECASE))
        expect(headers.nth(1)).to_have_text(re.compile(r"^personen$", re.IGNORECASE))
        assert panel.locator("tbody tr").count() > 0
        # Grundgesamtheit steht am Panel — ohne sie ist "Personen" mehrdeutig.
        expect(panel).to_contain_text("Personen mit mindestens einem Ereignis im Zeitraum")

    def test_needle_exchange_balance_panel_renders(self, authenticated_page, base_url):
        """Refs #1754: "Beispiel: Spritzentausch-Bilanz" rendert Summen + Saldo.

        Beobachtet am manuellen E2E-Server (2026-08-05): eine Zeile mit den
        Spalten "Ereignisse"/"Ausgabe gesamt"/"Rückgabe gesamt"/"Saldo". Bei
        Seed-Daten faellt sie komplett unter die k-Schwelle — geprueft wird
        darum die Spaltenform und die deklarierte Grundgesamtheit.
        """
        page = authenticated_page
        _open_statistics(page, base_url)
        panel = _select_backend(page, "needle-exchange-balance")

        expect(panel.locator("h3")).to_have_text("Beispiel: Spritzentausch-Bilanz")
        headers = panel.locator("thead th")
        expect(headers.first).to_have_text(re.compile(r"^ereignisse$", re.IGNORECASE))
        expect(headers.nth(1)).to_have_text(re.compile(r"^ausgabe gesamt$", re.IGNORECASE))
        expect(headers.nth(2)).to_have_text(re.compile(r"^rückgabe gesamt$", re.IGNORECASE))
        expect(headers.nth(3)).to_have_text(re.compile(r"^saldo$", re.IGNORECASE))
        assert panel.locator("tbody tr").count() > 0
        expect(panel).to_contain_text("Ereignisse der im Bericht ausgewiesenen Dokumentationstypen im Zeitraum")

    def test_backend_switch_replaces_panel(self, authenticated_page, base_url):
        """Zweite Auswahl ersetzt das Panel vollstaendig (kein Rest des ersten Backends)."""
        page = authenticated_page
        _open_statistics(page, base_url)
        _select_backend(page, "sample-report-1")
        panel = _select_backend(page, "example")

        expect(panel.locator("h3")).to_have_text("Beispiel: Ereignisse je Dokumentationstyp")
        assert panel.locator("h3:has-text('Beispiel-Sachbericht')").count() == 0

    def test_sachbericht_pdf_steht_beim_bericht_nicht_auf_der_uebersicht(self, authenticated_page, base_url):
        """R2 (Refs #1804): Der Export steht bei seinen Daten. Das Sachbericht-PDF
        ist das Artefakt eines Backends, nicht eine Ausgabe der Uebersichtszahlen —
        auf der Uebersicht hat es nichts mehr verloren."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/", wait_until="domcontentloaded")
        expect(page.locator("#dashboard-content")).to_be_visible(timeout=10000)
        assert page.locator("button:has-text('Beispiel-Sachbericht')").count() == 0

        _open_statistics(page, base_url)
        panel = _select_backend(page, "sample-report-1")
        with page.expect_download() as download_info:
            panel.locator("button:has-text('PDF-Export')").click()
        assert download_info.value.suggested_filename.endswith(".pdf")
        assert download_info.value.suggested_filename.startswith("sample-report-1_")


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

    @pytest.mark.parametrize("slug", ["client-metrics", "needle-exchange-balance"])
    def test_new_backend_csv_download(self, authenticated_page, base_url, slug):
        """Refs #1754: Panel-Knopf → CSV mit Slug-Dateiname und Kopfzeile.

        Die Kopfzeile ist der Ort, an dem Grundgesamtheit, Datenquelle und
        k-Schwelle **mit den Zahlen mitreisen** — ohne sie waere eine exportierte
        Datei ausserhalb der App nicht mehr interpretierbar. Deshalb wird nicht
        nur der Dateiname geprueft, sondern auch der Inhalt der ersten Zeile.
        """
        page = authenticated_page
        _open_statistics(page, base_url)
        panel = _select_backend(page, slug)

        with page.expect_download() as download_info:
            panel.locator("button:has-text('CSV-Export')").click()
        download = download_info.value

        assert download.suggested_filename.startswith(f"{slug}_"), download.suggested_filename
        assert download.suggested_filename.endswith(".csv"), download.suggested_filename

        header = download.path().read_text(encoding="utf-8").splitlines()[0]
        assert header.startswith("# Bericht: "), header
        assert slug in header, header
        assert "Grundgesamtheit:" in header, header
        assert "k-Anonymität:" in header, header


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
            page.get_by_role("button", name="Kalenderjahr", exact=True).click()

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
