"""E2E-Tests für Audit-Log-Detail-Seite: Strukturierter Inhalt, Navigation, Berechtigungen."""

import re

import pytest

pytestmark = pytest.mark.e2e


class TestAuditDetail:
    """Audit-Log Detail-Seite: Inhalt und Navigation."""

    def test_audit_list_entry_links_to_detail(self, authenticated_page, base_url):
        """Klick auf Zeitstempel in Audit-Liste → Detail-Seite öffnet."""
        page = authenticated_page
        page.goto(f"{base_url}/audit/")
        page.wait_for_load_state("domcontentloaded")

        # Ersten Zeitstempel-Link in der Tabelle anklicken
        first_link = page.locator("#audit-table table tbody tr a").first
        assert first_link.is_visible(), "Audit-Tabelle sollte mindestens einen Eintrag haben"
        first_link.click()

        page.wait_for_url(re.compile(r"/audit/[0-9a-f-]+/"))
        assert page.locator("h1").inner_text() == "Audit-Log Detail"

    def test_audit_detail_shows_structured_content(self, authenticated_page, base_url):
        """Detail-Seite zeigt Zeitstempel, Benutzer, Aktion, IP-Adresse."""
        page = authenticated_page
        page.goto(f"{base_url}/audit/")
        page.wait_for_load_state("domcontentloaded")

        page.locator("#audit-table table tbody tr a").first.click()
        page.wait_for_url(re.compile(r"/audit/[0-9a-f-]+/"))

        # Strukturierte Felder prüfen (Labels aus dem Template)
        assert page.locator("text=Zeitstempel").is_visible()
        assert page.locator("text=Benutzer").is_visible()
        assert page.locator("text=Aktion").is_visible()
        assert page.locator("text=IP-Adresse").is_visible()

        # Aktion als Badge sichtbar (z.B. "Anmeldung") — #663: rounded → rounded-full
        action_badge = page.locator("span.rounded-full.bg-accent-light")
        assert action_badge.first.is_visible()

        # Details-Abschnitt vorhanden
        assert page.locator("text=Details").is_visible()

    def test_audit_detail_back_link(self, authenticated_page, base_url):
        """'Zurück zur Liste'-Link navigiert zur Audit-Liste."""
        page = authenticated_page
        page.goto(f"{base_url}/audit/")
        page.wait_for_load_state("domcontentloaded")

        page.locator("#audit-table table tbody tr a").first.click()
        page.wait_for_url(re.compile(r"/audit/[0-9a-f-]+/"))

        # "Zurück zur Liste"-Link klicken
        page.click("a:has-text('Zurück zur Liste')")
        page.wait_for_url(re.compile(r"/audit/$"))
        assert page.locator("h1").filter(has_text="Audit-Log").is_visible()


class TestAuditDetailPermissions:
    """Berechtigungsprüfungen für Audit-Detail."""

    def test_staff_cannot_access_audit_detail(self, staff_page, authenticated_page, base_url):
        """Staff-User bekommt 403 auf Audit-Detail-URL."""
        # Als Admin eine gültige Audit-UUID holen
        admin = authenticated_page
        admin.goto(f"{base_url}/audit/")
        admin.wait_for_load_state("domcontentloaded")
        admin.locator("#audit-table table tbody tr a").first.click()
        admin.wait_for_url(re.compile(r"/audit/[0-9a-f-]+/"))
        audit_pk = re.search(r"/audit/([0-9a-f-]+)/", admin.url).group(1)

        # Als Staff auf Detail zugreifen → 403
        resp = staff_page.goto(f"{base_url}/audit/{audit_pk}/")
        assert resp.status == 403 or "/login/" in staff_page.url
