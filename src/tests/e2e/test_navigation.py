"""E2E-Tests: Sidebar-Neu-Dropdown zeigt konsistente Anlage-Buttons."""

import re

import pytest

pytestmark = pytest.mark.e2e


@pytest.mark.smoke
class TestSidebarCreateButtons:
    """Alle drei Anlage-Buttons sind im Sidebar-Neu-Dropdown sichtbar und funktional."""

    def test_all_create_buttons_visible(self, authenticated_page, base_url):
        """Sidebar-Dropdown zeigt Links fuer Kontakt, Aufgabe und Person."""
        page = authenticated_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")

        page.locator("[data-testid='sidebar-create-btn']").click()
        dropdown = page.locator("[data-testid='sidebar-create-dropdown']")
        dropdown.wait_for(state="visible", timeout=10000)
        assert dropdown.locator("[data-testid='sidebar-create-event']").is_visible()
        assert dropdown.locator("[data-testid='sidebar-create-workitem']").is_visible()
        assert dropdown.locator("[data-testid='sidebar-create-client']").is_visible()

    def test_aufgabe_navigates_to_workitem_create(self, authenticated_page, base_url):
        """Klick auf 'Aufgabe' im Sidebar-Dropdown navigiert zur WorkItem-Erstellungsseite."""
        page = authenticated_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")

        page.locator("[data-testid='sidebar-create-btn']").click()
        page.locator("[data-testid='sidebar-create-workitem']").click()
        page.wait_for_url(re.compile(r"/workitems/new/"), timeout=10000)

        assert "/workitems/new/" in page.url

    def test_klient_navigates_to_client_create(self, authenticated_page, base_url):
        """Klick auf 'Person' im Sidebar-Dropdown navigiert zur Personen-Erstellungsseite."""
        page = authenticated_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")

        page.locator("[data-testid='sidebar-create-btn']").click()
        page.locator("[data-testid='sidebar-create-client']").click()
        page.wait_for_url(re.compile(r"/clients/new/"), timeout=10000)

        assert "/clients/new/" in page.url


@pytest.mark.smoke
class TestOperativeNavOrder:
    """Operative Hauptnavigation: Zeitstrom -> Aufgaben -> Personen -> Faelle (Refs #1126)."""

    def test_desktop_sidebar_order(self, authenticated_page, base_url):
        """Aufgaben steht in der Desktop-Sidebar direkt unter Zeitstrom, vor Personen/Faellen."""
        page = authenticated_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")

        sidebar = page.locator("nav[aria-label='Hauptnavigation'] .flex-1.overflow-y-auto")
        sidebar.wait_for(state="visible", timeout=10000)
        hrefs = sidebar.locator(":scope > a").evaluate_all("els => els.map(a => a.getAttribute('href'))")

        idx_zeitstrom = hrefs.index("/")
        idx_aufgaben = hrefs.index("/workitems/")
        idx_personen = hrefs.index("/clients/")
        idx_faelle = hrefs.index("/cases/")

        assert idx_zeitstrom < idx_aufgaben < idx_personen < idx_faelle
