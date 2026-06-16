"""E2E-Tests: Rollenbezogene Arbeitszentrale unter /start/ (Refs #920)."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


class TestRoleDashboardLanding:
    """Smoke pro Rolle: /start/ rendert das rollenspezifische Template."""

    @pytest.mark.smoke
    def test_staff_start_redirects_to_cockpit(self, staff_page, base_url):
        """Fachkraft: /start/ leitet auf / um — das Cockpit liegt dort (Refs #1124)."""
        page = staff_page
        page.goto(f"{base_url}/start/")
        page.wait_for_url(f"{base_url}/")
        expect(page.locator("[data-testid='zeitstrom-cockpit']")).to_be_visible()

    @pytest.mark.smoke
    def test_assistant_start_redirects_to_cockpit(self, assistant_page, base_url):
        """Assistenz: /start/ leitet auf / um (Refs #1124)."""
        page = assistant_page
        page.goto(f"{base_url}/start/")
        page.wait_for_url(f"{base_url}/")
        expect(page.locator("[data-testid='zeitstrom-cockpit']")).to_be_visible()

    @pytest.mark.smoke
    def test_lead_dashboard_renders(self, lead_page, base_url):
        page = lead_page
        page.goto(f"{base_url}/start/")
        page.wait_for_load_state("domcontentloaded")

        expect(page.locator("h1")).to_contain_text("Leitungs-Arbeitszentrale")
        expect(page.locator("[data-testid='lead-dashboard-cards']")).to_be_visible()
        expect(page.locator("[data-testid='card-deletion-requests']")).to_be_visible()
        expect(page.locator("[data-testid='card-retention-proposals']")).to_be_visible()
        expect(page.locator("[data-testid='card-legal-holds']")).to_be_visible()
        expect(page.locator("[data-testid='card-last-snapshot']")).to_be_visible()

    @pytest.mark.smoke
    def test_facility_admin_dashboard_renders(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/start/")
        page.wait_for_load_state("domcontentloaded")

        expect(page.locator("h1")).to_contain_text("Admin-Arbeitszentrale")
        expect(page.locator("[data-testid='facility-admin-dashboard-cards']")).to_be_visible()
        expect(page.locator("[data-testid='card-users-without-mfa']")).to_be_visible()
        expect(page.locator("[data-testid='card-settings-warnings']")).to_be_visible()

    @pytest.mark.smoke
    def test_super_admin_dashboard_renders(self, super_admin_page, base_url):
        page = super_admin_page
        page.goto(f"{base_url}/start/")
        page.wait_for_load_state("domcontentloaded")

        expect(page.locator("h1")).to_contain_text("System-Arbeitszentrale")
        expect(page.locator("[data-testid='super-admin-dashboard-cards']")).to_be_visible()
        expect(page.locator("[data-testid='card-facilities']")).to_be_visible()
        expect(page.locator("[data-testid='card-users-total']")).to_be_visible()
        expect(page.locator("[data-testid='card-audit-events']")).to_be_visible()
        expect(page.locator("[data-testid='card-critical-events']")).to_be_visible()


class TestNavLinkDashboard:
    """Nav-Link "Arbeitszentrale" nur fuer Leitung/Admin; Fachkraft sieht stattdessen das Cockpit (Refs #1124)."""

    @pytest.mark.smoke
    def test_nav_link_visible_for_lead_and_navigates(self, lead_page, base_url):
        page = lead_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        nav_link = page.locator("[data-testid='nav-dashboard']").first
        expect(nav_link).to_be_visible()
        nav_link.click()
        page.wait_for_url(f"{base_url}/start/")
        expect(page.locator("[data-testid='lead-dashboard-cards']")).to_be_visible()

    @pytest.mark.smoke
    def test_staff_has_no_arbeitszentrale_or_uebergabe_nav(self, staff_page, base_url):
        """Fachkraft: kein Arbeitszentrale- und kein Uebergabe-Eintrag in der Hauptnav (Refs #1124)."""
        page = staff_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        nav = page.locator("nav[aria-label='Hauptnavigation']")
        expect(nav.locator("[data-testid='nav-dashboard']")).to_have_count(0)
        expect(nav.locator("[data-testid='nav-handover']")).to_have_count(0)
        expect(page.locator("[data-testid='zeitstrom-cockpit']")).to_be_visible()
