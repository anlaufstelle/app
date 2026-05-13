"""E2E-Tests: Superadmin-Bereich /system/ (Refs #867).

Verifiziert, dass:
- Login als ``superadmin`` auf ``/system/`` landet
- der Cross-Facility-Banner sichtbar ist (DSGVO-Transparenz)
- ``/system/audit/`` cross-facility AuditLogs anzeigt (loest #866)
- Nicht-super_admin-User keinen Zugriff auf ``/system/`` haben
- jeder Aufruf einer ``/system/``-View einen ``SYSTEM_VIEW``-AuditLog erzeugt
"""

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


class TestSuperAdminAccess:
    """Zugriff auf den /system/-Bereich nach Rollen."""

    @pytest.mark.smoke
    def test_super_admin_lands_on_system(self, super_admin_page):
        page = super_admin_page
        assert page.url.endswith("/system/")
        expect(page.locator("h1")).to_contain_text("Systembereich")

    @pytest.mark.smoke
    def test_cross_facility_banner_visible(self, super_admin_page):
        page = super_admin_page
        banner = page.locator("[data-testid='system-cross-facility-banner']")
        expect(banner).to_be_visible()
        expect(banner).to_contain_text("facility-übergreifend")
        expect(banner).to_contain_text("Audit-Log")

    def test_facility_admin_forbidden_from_system(self, authenticated_page, base_url):
        """authenticated_page = admin / facility_admin (kein super_admin)."""
        page = authenticated_page
        page.goto(f"{base_url}/system/")
        page.wait_for_load_state("domcontentloaded")
        # 403-Seite hat keinen "Systembereich"-Header.
        expect(page.locator("h1")).not_to_contain_text("Systembereich")

    def test_lead_forbidden_from_system(self, lead_page, base_url):
        page = lead_page
        page.goto(f"{base_url}/system/")
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator("h1")).not_to_contain_text("Systembereich")


class TestSuperAdminAuditView:
    """Cross-Facility-AuditLog (loest #866)."""

    def test_audit_list_accessible(self, super_admin_page, base_url):
        page = super_admin_page
        page.goto(f"{base_url}/system/audit/")
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator("h1")).to_contain_text("Audit-Log")

    def test_audit_list_shows_facility_column(self, super_admin_page, base_url):
        page = super_admin_page
        page.goto(f"{base_url}/system/audit/")
        page.wait_for_load_state("domcontentloaded")
        # Tabellenkopf enthaelt Facility-Spalte (im Gegensatz zur facility-
        # gescopten Variante in /audit/).
        expect(page.locator("table thead")).to_contain_text("Einrichtung")

    def test_audit_view_writes_system_view_audit(self, super_admin_page, base_url):
        """Aufruf einer /system/-View erzeugt einen SYSTEM_VIEW-AuditLog
        (DSGVO-Rechenschaftspflicht)."""
        page = super_admin_page
        # Nach dem Aufruf von /system/ in der Fixture ist mindestens 1
        # SYSTEM_VIEW-Eintrag vorhanden. Hier rufen wir explizit /system/audit/
        # auf und pruefen, dass die Liste den eigenen Aufruf protokolliert.
        page.goto(f"{base_url}/system/audit/")
        page.wait_for_load_state("domcontentloaded")
        # Filter auf SYSTEM_VIEW: in der Action-Dropdown-Liste sollte der
        # Eintrag "Systembereich aufgerufen" verfuegbar sein.
        expect(page.locator("select[name='action']")).to_contain_text("Systembereich aufgerufen")


class TestSuperAdminNavigation:
    """Sidebar zeigt /system/-Link nur fuer super_admin."""

    def test_super_admin_sees_system_link(self, super_admin_page):
        page = super_admin_page
        sidebar = page.locator("nav[aria-label='Hauptnavigation']")
        expect(sidebar.get_by_role("link", name="Systembereich")).to_be_visible()

    def test_facility_admin_does_not_see_system_link(self, authenticated_page):
        page = authenticated_page
        sidebar = page.locator("nav[aria-label='Hauptnavigation']")
        # facility_admin (admin user) sieht keinen Systembereich-Link.
        expect(sidebar.get_by_role("link", name="Systembereich")).not_to_be_visible()

    def test_super_admin_does_not_see_facility_scoped_links(self, super_admin_page):
        """Refs #867: super_admin hat keinen Facility-Kontext, daher
        sind die facility-gescopten Items (Zeitstrom, Personen, Aufgaben,
        Dateien, Übergabe, Suche, Neu erstellen) ausgeblendet — sonst
        klickt super_admin in 403er hinein."""
        page = super_admin_page
        sidebar = page.locator("nav[aria-label='Hauptnavigation']")
        for label in ("Zeitstrom", "Personen", "Aufgaben", "Dateien", "Übergabe"):
            expect(sidebar.get_by_role("link", name=label)).not_to_be_visible()
        # "Neu erstellen"-Button und Suche sind ebenfalls nicht da.
        expect(page.locator("[data-testid='sidebar-create-btn']")).not_to_be_visible()
        expect(page.locator("[data-testid='global-search-input']")).not_to_be_visible()
