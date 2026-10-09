"""E2E-Tests: Statistik-Dashboard — Navigation, KPIs, HTMX, Jahresnavigation."""

import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


class TestStatisticsNavigation:
    """Nav: "Statistik" Link für Admin sichtbar."""

    @pytest.mark.smoke
    def test_statistik_link_visible(self, authenticated_page):
        page = authenticated_page
        nav = page.locator("nav[aria-label='Hauptnavigation']")
        assert nav.locator("a:has-text('Statistik')").is_visible()

    @pytest.mark.smoke
    def test_statistik_link_navigates(self, authenticated_page, base_url):
        page = authenticated_page
        nav = page.locator("nav[aria-label='Hauptnavigation']")
        nav.locator("a:has-text('Statistik')").click()
        page.wait_for_url("**/statistics/")
        assert page.locator("h1").inner_text() == "Statistik"


class TestStatisticsDashboard:
    """Dashboard: KPI-Karten + Tabellen sichtbar."""

    def test_kpi_cards_visible(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")
        assert page.locator("text=Gesamtkontakte").is_visible()
        assert page.locator("text=Anonym").first.is_visible()
        assert page.locator("text=Nur Pseudonym").first.is_visible()
        assert page.locator("text=Mit Zusatzangaben").first.is_visible()

    def test_tables_visible(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")
        assert page.locator("text=Kontakte nach Dokumentationstyp").is_visible()
        assert page.locator("text=Kontakte nach Altersgruppe").is_visible()


class TestStatisticsHTMX:
    """Zeitraum-Wechsel per HTMX."""

    def test_period_switch_updates_content(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")

        zeitraum_locator = page.locator("text=Zeitraum:")
        zeitraum_text = zeitraum_locator.inner_text()

        # Auf „Letzte 180 Tage" klicken (HTMX aktualisiert den Content asynchron). Der
        # ``expect_response``-Block wartet nur auf den Server-Response, nicht
        # auf den anschliessenden DOM-Swap — unter Parallel-Last entstand sonst
        # eine Race-Condition zwischen Response und ``inner_text`` (Refs #761).
        with page.expect_response(lambda r: "/statistics/" in r.url and "chart-data" not in r.url):
            page.locator("button:has-text('Letzte 180 Tage')").click()

        # ``not_to_have_text`` pollt automatisch bis der Swap durch ist.
        expect(zeitraum_locator).not_to_have_text(zeitraum_text, timeout=10000)


class TestStatisticsYearNavigation:
    """Jahres-Navigation: Button, Pfeile, Zeitraum (#437)."""

    def test_jahr_button_visible(self, authenticated_page, base_url):
        """Kalenderjahr-Button ist auf der Statistik-Seite sichtbar."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")
        assert page.get_by_role("button", name="Kalenderjahr", exact=True).is_visible()

    def test_jahr_button_switches_period(self, authenticated_page, base_url):
        """Klick auf Kalenderjahr → Zeitraum zeigt 1. Januar bis heute."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")

        with page.expect_response(lambda r: "/statistics/" in r.url and "chart-data" not in r.url):
            page.get_by_role("button", name="Kalenderjahr", exact=True).click()

        # ``expect`` pollt bis der HTMX-DOM-Swap durch ist (Refs #849).
        # Langformat-Datum seit i18n-L10N-Umstellung (Refs #815).
        expect(page.locator("text=Zeitraum:")).to_contain_text("1. Januar", timeout=10000)

    def test_year_nav_prev_arrow(self, authenticated_page, base_url):
        """Pfeil links → Jahr dekrementiert, Zeitraum = ganzes Vorjahr."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")

        # Auf Jahr klicken
        with page.expect_response(lambda r: "/statistics/" in r.url and "chart-data" not in r.url):
            page.get_by_role("button", name="Kalenderjahr", exact=True).click()

        # Auf DOM-Swap warten, bevor wir das Jahr lesen — ohne ``expect``
        # liest ``inner_text`` evtl. den alten Wert (Refs #849).
        year_label = page.locator("[aria-label='Vorheriges Jahr']").locator("..").locator("span")
        expect(year_label).to_have_text(re.compile(r"^\d{4}$"), timeout=10000)
        current_year_text = year_label.inner_text()

        # Pfeil links klicken
        with page.expect_response(lambda r: "/statistics/" in r.url and "chart-data" not in r.url):
            page.locator("[aria-label='Vorheriges Jahr']").click()

        # ``expect``-Locator pollt bis zum DOM-Swap nach dem HTMX-Response (Refs #761).
        expect(year_label).to_have_text(str(int(current_year_text) - 1), timeout=10000)

        # Zeitraum muss bis 31. Dezember des Vorjahres reichen
        # (Langformat-Datum seit i18n-L10N-Umstellung, Refs #815).
        expect(page.locator("text=Zeitraum:")).to_contain_text("31. Dezember", timeout=10000)

    def test_year_nav_next_arrow_for_past_year(self, authenticated_page, base_url):
        """Im Vorjahr: Pfeil rechts sichtbar, Klick → Jahr inkrementiert."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/?zeitraum=jahr&year=2025")

        # Pfeil rechts muss sichtbar sein
        assert page.locator("[aria-label='Nächstes Jahr']").is_visible()

        # Klick auf Pfeil rechts
        with page.expect_response(lambda r: "/statistics/" in r.url and "chart-data" not in r.url):
            page.locator("[aria-label='Nächstes Jahr']").click()

        year_label = page.locator("[aria-label='Vorheriges Jahr']").locator("..").locator("span")
        # ``expect`` pollt automatisch bis zum DOM-Swap (Refs #761).
        expect(year_label).to_have_text("2026", timeout=10000)

    def test_no_next_arrow_for_current_year(self, authenticated_page, base_url):
        """Im aktuellen Jahr: kein Pfeil rechts."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")

        with page.expect_response(lambda r: "/statistics/" in r.url and "chart-data" not in r.url):
            page.get_by_role("button", name="Kalenderjahr", exact=True).click()

        # ``expect`` pollt bis zum DOM-Swap — ``count() == 0`` direkt nach
        # ``expect_response`` kann unter Parallel-Last den Vor-Swap-Stand
        # sehen (Refs #849).
        expect(page.locator("[aria-label='Nächstes Jahr']")).to_have_count(0, timeout=10000)


class TestZeitraumUeberlebtDenReiterwechsel:
    """Refs #1804 (§3.5): EIN Zeitraum fuer den ganzen Statistik-Bereich.

    Bisher fing jeder Reiter wieder bei den letzten 30 Tagen an — wer auf der
    Uebersicht ein Kalenderjahr eingestellt hatte und den Reiter wechselte, sah
    dort ein anderes Fenster, ohne dass es irgendwo stand. Der Fehler ist nur im
    Klickpfad sichtbar: die Reiter-``href``s entstehen serverseitig aus dem
    Zustand der gerade gerenderten Seite.
    """

    def test_period_travels_to_the_other_tabs(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")

        with page.expect_response(lambda r: "/statistics/" in r.url and "chart-data" not in r.url):
            page.get_by_role("button", name="Letzte 180 Tage", exact=True).click()

        # Die Reiterzeile liegt seit #1804 INNERHALB von #statistics-content und
        # wird vom Swap mit erneuert — genau deshalb tragen ihre Links danach den
        # neuen Zeitraum. Geprueft wird der echte Klick nach dem Swap: ein
        # Attribut-Test auf der frisch geladenen Seite bemerkt den Bruch nicht.
        page.locator("[data-testid='subnav-tab-statistik_berichte']").click()
        page.wait_for_url("**/statistics/?*ansicht=datensaetze*", timeout=30_000)
        assert "zeitraum=letzte180" in page.url, page.url

        page.locator("[data-testid='subnav-tab-statistik_releases']").click()
        page.wait_for_url("**/statistics/reports/releases/**", timeout=30_000)
        assert "zeitraum=letzte180" in page.url, page.url
        # Ausnahme Freigaben: ignoriert den Zeitraum, sagt das — und reicht ihn weiter.
        expect(page.locator("[data-testid='release-period-note']")).to_contain_text("unabhängig vom gewählten Zeitraum")

        page.locator("[data-testid='subnav-tab-statistik_uebersicht']").click()
        page.wait_for_url("**/statistics/**", timeout=30_000)
        assert "zeitraum=letzte180" in page.url, page.url
        expect(page.locator("button:has-text('Letzte 180 Tage')")).to_have_class(re.compile(r"bg-accent"))
