"""E2E-Tests: ``/start/`` je Rolle (Refs #920, neu gefasst Refs #1813).

``/start/`` ist kein Ort mehr, sondern eine Weiche: jede Rolle landet an ihrem
Arbeitsplatz (Konzept #1801 §3.1, Entscheid F5). Nur die Leitung hat dort noch
eine eigene Seite — Anwendungsbetreuung und Systemadministration bekamen nach
der Bereichs-Konsolidierung unter ``/start/`` nur noch eine Kachelwand, deren
Inhalte auf ihren Zielseiten stehen; ihre beiden Templates sind entfallen.

Dieser Spec prüft deshalb **Weiterleitungsziele** statt Template-Marker — und
je Ziel einen Marker, den nur die richtige Seite trägt.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


class TestRoleDashboardLanding:
    """Smoke pro Rolle: wo landet ``/start/``?"""

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
        """Leitung: die einzige Rolle mit einer eigenen Arbeitszentrale."""
        page = lead_page
        page.goto(f"{base_url}/start/")
        page.wait_for_load_state("domcontentloaded")

        expect(page.locator("h1")).to_contain_text("Leitungs-Arbeitszentrale")
        expect(page.locator("[data-testid='lead-dashboard-cards']")).to_be_visible()
        expect(page.locator("[data-testid='card-deletion-requests']")).to_be_visible()
        expect(page.locator("[data-testid='card-retention-proposals']")).to_be_visible()
        expect(page.locator("[data-testid='card-last-snapshot']")).to_be_visible()
        # Refs #1759: Die Legal-Holds-Kachel verlinkte eine super_admin-only-Seite
        # (``SystemAuditMixin``) — für die Leitung endete der Klick im 403 (#1735-
        # Guard). Da super_admin nie dieses Template sieht, war die Kachel fuer
        # jede Rolle tot und ist seit #1759 vollstaendig entfernt (nicht nur
        # geguardet).
        expect(page.locator("[data-testid='card-legal-holds']")).to_have_count(0)

    @pytest.mark.smoke
    def test_facility_admin_start_redirects_to_verwaltung(self, authenticated_page, base_url):
        """Anwendungsbetreuung: /start/ leitet auf die Verwaltungs-Uebersicht (Refs #1813)."""
        page = authenticated_page
        page.goto(f"{base_url}/start/")
        page.wait_for_url(f"{base_url}/verwaltung/")

        expect(page.locator("[data-testid='subnav-verwaltung']")).to_be_visible()
        expect(page.locator("[data-testid='card-users-without-mfa']")).to_be_visible()
        expect(page.locator("[data-testid='card-settings-warnings']")).to_be_visible()

    @pytest.mark.smoke
    def test_super_admin_start_redirects_to_system(self, super_admin_page, base_url):
        """Systemadministration: /start/ leitet in den Systembereich (Entscheid F5).

        Die Kennzahlen der frueheren System-Arbeitszentrale stehen dort als
        Karte „Audit-Aktivitaet (24 h)".
        """
        page = super_admin_page
        page.goto(f"{base_url}/start/")
        page.wait_for_url(f"{base_url}/system/")

        expect(page.locator("[data-testid='system-audit-activity-card']")).to_be_visible()
        expect(page.locator("[data-testid='card-audit-events']")).to_be_visible()
        expect(page.locator("[data-testid='card-critical-events']")).to_be_visible()


class TestNavLinkDashboard:
    """Der Nav-Eintrag „Arbeitszentrale" gehoert seit Regel N3 nur noch der Leitung."""

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
    def test_staff_has_no_arbeitszentrale_nav(self, staff_page, base_url):
        """Fachkraft: kein Arbeitszentrale-Eintrag in der Hauptnav (Refs #1124)."""
        page = staff_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        nav = page.locator("nav[aria-label='Hauptnavigation']")
        expect(nav.locator("[data-testid='nav-dashboard']")).to_have_count(0)
        expect(page.locator("[data-testid='zeitstrom-cockpit']")).to_be_visible()

    @pytest.mark.smoke
    def test_facility_admin_has_no_arbeitszentrale_nav(self, authenticated_page, base_url):
        """Refs #1813 (Regel N3): fuer die Anwendungsbetreuung waere der Eintrag
        nur noch ein Link auf eine Weiterleitung."""
        page = authenticated_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        nav = page.locator("nav[aria-label='Hauptnavigation']")
        expect(nav.locator("[data-testid='nav-dashboard']")).to_have_count(0)
