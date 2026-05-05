"""E2E-Tests: Sidebar-Neu-Dropdown zeigt konsistente Anlage-Buttons."""

import re

import pytest

pytestmark = pytest.mark.e2e


@pytest.mark.smoke
class TestSidebarCreateButtons:
    """Alle drei Anlage-Buttons sind im Sidebar-Neu-Dropdown sichtbar und funktional."""

    def test_all_create_buttons_visible(self, authenticated_page, base_url):
        """Sidebar-Dropdown zeigt Links fuer Kontakt, Aufgabe und Klientel."""
        page = authenticated_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")

        page.locator("[data-testid='sidebar-create-btn']").click()
        dropdown = page.locator("[data-testid='sidebar-create-dropdown']")
        dropdown.wait_for(state="visible", timeout=3000)
        assert dropdown.locator("a:has-text('Kontakt')").is_visible()
        assert dropdown.locator("a:has-text('Aufgabe')").is_visible()
        assert dropdown.locator("a:has-text('Person')").is_visible()

    def test_aufgabe_navigates_to_workitem_create(self, authenticated_page, base_url):
        """Klick auf 'Aufgabe' im Sidebar-Dropdown navigiert zur WorkItem-Erstellungsseite."""
        page = authenticated_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")

        page.locator("[data-testid='sidebar-create-btn']").click()
        page.locator("[data-testid='sidebar-create-dropdown'] a:has-text('Aufgabe')").click()
        page.wait_for_url(re.compile(r"/workitems/new/"), timeout=10000)

        assert "/workitems/new/" in page.url

    def test_klient_navigates_to_client_create(self, authenticated_page, base_url):
        """Klick auf 'Person' im Sidebar-Dropdown navigiert zur Personen-Erstellungsseite."""
        page = authenticated_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")

        page.locator("[data-testid='sidebar-create-btn']").click()
        page.locator("[data-testid='sidebar-create-dropdown'] a:has-text('Person')").click()
        page.wait_for_url(re.compile(r"/clients/new/"), timeout=10000)

        assert "/clients/new/" in page.url
