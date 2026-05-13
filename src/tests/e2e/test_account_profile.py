"""E2E-Tests fuer die Benutzerprofilseite.

Prueft:
- Klick auf Username in Sidebar fuehrt zur Profilseite
- Profilseite zeigt korrekte Daten (Name, Rolle)
- Events und Aufgaben werden angezeigt
"""

import pytest

pytestmark = pytest.mark.e2e


class TestAccountProfile:
    """Benutzerprofilseite ist erreichbar und zeigt korrekte Daten."""

    def test_sidebar_username_links_to_profile(self, authenticated_page, base_url):
        """Klick auf Username in der Sidebar navigiert zur Profilseite."""
        page = authenticated_page
        nav = page.locator("nav[aria-label='Hauptnavigation']")
        nav.locator("a:has-text('Admin User')").click()
        page.wait_for_url(lambda url: "/account/" in url, timeout=10000)
        assert "/account/" in page.url

    def test_profile_shows_name(self, authenticated_page, base_url):
        """Profilseite zeigt den Namen des Users."""
        page = authenticated_page
        page.goto(f"{base_url}/account/", wait_until="domcontentloaded")
        assert page.locator("h1").inner_text() == "Mein Profil"
        profile_name = page.locator("[data-testid='profile-name']")
        assert profile_name.is_visible()

    def test_profile_shows_role(self, authenticated_page, base_url):
        """Profilseite zeigt die Rolle als Badge."""
        page = authenticated_page
        page.goto(f"{base_url}/account/", wait_until="domcontentloaded")
        role_badge = page.locator("[data-testid='profile-role']")
        assert role_badge.is_visible()
        # Refs #867: Rolle ``admin`` -> ``facility_admin`` (UI-Label „Anwendungsbetreuung").
        assert role_badge.inner_text().strip() == "Anwendungsbetreuung"

    def test_profile_shows_facility(self, authenticated_page, base_url):
        """Profilseite zeigt die Einrichtung."""
        page = authenticated_page
        page.goto(f"{base_url}/account/", wait_until="domcontentloaded")
        facility = page.locator("[data-testid='profile-facility']")
        assert facility.is_visible()

    def test_profile_shows_password_change_link(self, authenticated_page, base_url):
        """Profilseite enthaelt Link zum Passwort-Aendern."""
        page = authenticated_page
        page.goto(f"{base_url}/account/", wait_until="domcontentloaded")
        link = page.locator("a:has-text('Passwort')")
        assert link.is_visible()

    def test_profile_shows_events_section(self, authenticated_page, base_url):
        """Profilseite zeigt den Events-Bereich."""
        page = authenticated_page
        page.goto(f"{base_url}/account/", wait_until="domcontentloaded")
        assert page.locator("h2:has-text('Letzte Ereignisse')").is_visible()

    def test_profile_shows_workitems_section(self, authenticated_page, base_url):
        """Profilseite zeigt den Aufgaben-Bereich."""
        page = authenticated_page
        page.goto(f"{base_url}/account/", wait_until="domcontentloaded")
        assert page.locator("h2:has-text('Offene Aufgaben')").is_visible()
        assert page.locator("h2:has-text('erledigt')").is_visible()

    def test_staff_profile_shows_correct_role(self, staff_page, base_url):
        """Staff-User sieht korrekte Rolle auf Profilseite."""
        page = staff_page
        page.goto(f"{base_url}/account/", wait_until="domcontentloaded")
        role_badge = page.locator("[data-testid='profile-role']")
        assert role_badge.inner_text().strip() == "Fachkraft"
