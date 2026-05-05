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
        assert page.locator("text=Identifiziert").first.is_visible()
        assert page.locator("text=Qualifiziert").first.is_visible()

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

        # Auf Halbjahr klicken (HTMX aktualisiert den Content asynchron). Der
        # ``expect_response``-Block wartet nur auf den Server-Response, nicht
        # auf den anschliessenden DOM-Swap — unter Parallel-Last entstand sonst
        # eine Race-Condition zwischen Response und ``inner_text`` (Refs #761).
        with page.expect_response(lambda r: "/statistics/" in r.url and "chart-data" not in r.url):
            page.locator("button:has-text('Halbjahr')").click()

        # ``not_to_have_text`` pollt automatisch bis der Swap durch ist.
        expect(zeitraum_locator).not_to_have_text(zeitraum_text, timeout=10000)


class TestStatisticsYearNavigation:
    """Jahres-Navigation: Button, Pfeile, Zeitraum (#437)."""

    def test_jahr_button_visible(self, authenticated_page, base_url):
        """Jahr-Button ist auf der Statistik-Seite sichtbar."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")
        assert page.get_by_role("button", name="Jahr", exact=True).is_visible()

    def test_jahr_button_switches_period(self, authenticated_page, base_url):
        """Klick auf Jahr → Zeitraum zeigt 01.01. bis heute."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")

        with page.expect_response(lambda r: "/statistics/" in r.url and "chart-data" not in r.url):
            page.get_by_role("button", name="Jahr", exact=True).click()

        # ``expect`` pollt bis der HTMX-DOM-Swap durch ist (Refs #849).
        expect(page.locator("text=Zeitraum:")).to_contain_text("01.01.", timeout=10000)

    def test_year_nav_prev_arrow(self, authenticated_page, base_url):
        """Pfeil links → Jahr dekrementiert, Zeitraum = ganzes Vorjahr."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")

        # Auf Jahr klicken
        with page.expect_response(lambda r: "/statistics/" in r.url and "chart-data" not in r.url):
            page.get_by_role("button", name="Jahr", exact=True).click()

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

        # Zeitraum muss 01.01. – 31.12. des Vorjahres zeigen
        expect(page.locator("text=Zeitraum:")).to_contain_text("31.12.", timeout=10000)

    def test_year_nav_next_arrow_for_past_year(self, authenticated_page, base_url):
        """Im Vorjahr: Pfeil rechts sichtbar, Klick → Jahr inkrementiert."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/?period=year&year=2025")

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
            page.get_by_role("button", name="Jahr", exact=True).click()

        # ``expect`` pollt bis zum DOM-Swap — ``count() == 0`` direkt nach
        # ``expect_response`` kann unter Parallel-Last den Vor-Swap-Stand
        # sehen (Refs #849).
        expect(page.locator("[aria-label='Nächstes Jahr']")).to_have_count(0, timeout=10000)
