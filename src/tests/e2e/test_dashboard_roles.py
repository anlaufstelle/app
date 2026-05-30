"""E2E-Tests: Rollenbezogene Arbeitszentrale unter /start/ (Refs #920)."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


class TestRoleDashboardLanding:
    """Smoke pro Rolle: /start/ rendert das rollenspezifische Template."""

    @pytest.mark.smoke
    def test_staff_dashboard_renders(self, staff_page, base_url):
        page = staff_page
        page.goto(f"{base_url}/start/")
        page.wait_for_load_state("domcontentloaded")

        expect(page.locator("h1")).to_contain_text("Arbeitszentrale")
        expect(page.locator("[data-testid='staff-dashboard-cards']")).to_be_visible()
        expect(page.locator("[data-testid='card-today-events']")).to_be_visible()
        expect(page.locator("[data-testid='card-my-workitems']")).to_be_visible()
        expect(page.locator("[data-testid='card-recent-clients']")).to_be_visible()

    @pytest.mark.smoke
    def test_assistant_dashboard_renders(self, assistant_page, base_url):
        page = assistant_page
        page.goto(f"{base_url}/start/")
        page.wait_for_load_state("domcontentloaded")

        expect(page.locator("[data-testid='staff-dashboard-cards']")).to_be_visible()

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
    """Der neue Nav-Link "Arbeitszentrale" fuehrt zu /start/."""

    @pytest.mark.smoke
    def test_nav_link_visible_and_navigates(self, staff_page, base_url):
        page = staff_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        nav_link = page.locator("[data-testid='nav-dashboard']").first
        expect(nav_link).to_be_visible()
        nav_link.click()
        page.wait_for_url(f"{base_url}/start/")
        expect(page.locator("[data-testid='staff-dashboard-cards']")).to_be_visible()
