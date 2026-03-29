"""E2E-Tests: Dashboard, globale Suche, Neu-Dropdown.

Refs #386 — Frontend-Redesign.
"""

import pytest

pytestmark = pytest.mark.e2e


class TestDashboardStartseite:
    """Zeitstrom ist die neue Startseite."""

    def test_zeitstrom_is_start_page(self, authenticated_page):
        page = authenticated_page
        assert page.url.endswith("/")
        assert page.locator("h1").inner_text() == "Zeitstrom"

    def test_aktivitaetslog_redirects(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/aktivitaetslog/")
        page.wait_for_load_state("domcontentloaded")
        # Should redirect to root
        assert page.url.rstrip("/") == base_url.rstrip("/") or page.url.endswith("/")
        assert page.locator("h1").inner_text() == "Zeitstrom"

    def test_timeline_redirects(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/timeline/")
        page.wait_for_load_state("domcontentloaded")
        assert page.locator("h1").inner_text() == "Zeitstrom"


class TestGlobalSearch:
    """Globale Suche in der Sidebar."""

    def test_search_input_visible(self, authenticated_page):
        page = authenticated_page
        search_input = page.locator("[data-testid='global-search-input']")
        assert search_input.is_visible()

    def test_search_returns_results(self, authenticated_page):
        page = authenticated_page
        search_input = page.locator("[data-testid='global-search-input']")
        search_input.click()
        search_input.press_sequentially("Stern", delay=50)
        page.wait_for_timeout(1000)

        results = page.locator("[data-testid='global-search-results']")
        results.wait_for(state="visible", timeout=5000)
        assert "Stern-42" in results.inner_text()

    def test_search_all_results_link(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")
        search_input = page.locator("[data-testid='global-search-input']")
        search_input.click()
        search_input.press_sequentially("Stern", delay=50)
        page.wait_for_timeout(1000)

        results = page.locator("[data-testid='global-search-results']")
        results.wait_for(state="visible", timeout=5000)
        link = results.locator("a:has-text('Alle Ergebnisse anzeigen')")
        assert link.is_visible()

    def test_full_search_page_still_works(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/search/?q=Stern")
        page.wait_for_load_state("domcontentloaded")
        assert "Stern-42" in page.content()


class TestNeuDropdown:
    """Erweiterter Neu-Dropdown mit rollenbasiertem Menü."""

    def test_staff_sees_all_create_options(self, staff_page, base_url):
        page = staff_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        page.locator("[data-testid='sidebar-create-btn']").click()
        dropdown = page.locator("[data-testid='sidebar-create-dropdown']")
        dropdown.wait_for(state="visible", timeout=3000)

        text = dropdown.inner_text()
        assert "Kontakt" in text
        assert "Klientel" in text
        assert "Aufgabe" in text
        assert "Fall" in text

    def test_assistant_sees_only_kontakt(self, assistant_page, base_url):
        page = assistant_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        page.locator("[data-testid='sidebar-create-btn']").click()
        dropdown = page.locator("[data-testid='sidebar-create-dropdown']")
        dropdown.wait_for(state="visible", timeout=3000)

        text = dropdown.inner_text()
        assert "Kontakt" in text
        assert "Klientel" not in text
        assert "Aufgabe" not in text
        assert "Fall" not in text
