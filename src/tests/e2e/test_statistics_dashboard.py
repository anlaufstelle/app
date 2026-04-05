"""E2E-Tests: Statistik-Dashboard — Navigation, KPIs, HTMX, Jahresnavigation."""

import pytest

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

        # Zeitraum-Text vor Wechsel merken
        zeitraum_text = page.locator("text=Zeitraum:").inner_text()

        # Auf Halbjahr klicken (HTMX aktualisiert den Content asynchron)
        with page.expect_response(lambda r: "/statistics/" in r.url and "chart-data" not in r.url):
            page.locator("button:has-text('Halbjahr')").click()

        # Nach HTMX-Swap den neuen Text prüfen
        new_text = page.locator("text=Zeitraum:").inner_text()
        assert new_text != zeitraum_text


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

        zeitraum = page.locator("text=Zeitraum:").inner_text()
        assert "01.01." in zeitraum

    def test_year_nav_prev_arrow(self, authenticated_page, base_url):
        """Pfeil links → Jahr dekrementiert, Zeitraum = ganzes Vorjahr."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")

        # Auf Jahr klicken
        with page.expect_response(lambda r: "/statistics/" in r.url and "chart-data" not in r.url):
            page.get_by_role("button", name="Jahr", exact=True).click()

        # Aktuelles Jahr merken
        year_label = page.locator("[aria-label='Vorheriges Jahr']").locator("..").locator("span")
        current_year_text = year_label.inner_text()

        # Pfeil links klicken
        with page.expect_response(lambda r: "/statistics/" in r.url and "chart-data" not in r.url):
            page.locator("[aria-label='Vorheriges Jahr']").click()

        new_year_text = page.locator("[aria-label='Vorheriges Jahr']").locator("..").locator("span")
        assert int(new_year_text.inner_text()) == int(current_year_text) - 1

        # Zeitraum muss 01.01. – 31.12. des Vorjahres zeigen
        zeitraum = page.locator("text=Zeitraum:").inner_text()
        assert "31.12." in zeitraum

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
        assert int(year_label.inner_text()) == 2026

    def test_no_next_arrow_for_current_year(self, authenticated_page, base_url):
        """Im aktuellen Jahr: kein Pfeil rechts."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/")

        with page.expect_response(lambda r: "/statistics/" in r.url and "chart-data" not in r.url):
            page.get_by_role("button", name="Jahr", exact=True).click()

        assert page.locator("[aria-label='Nächstes Jahr']").count() == 0
