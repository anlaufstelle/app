"""E2E-Tests für Stream G: Security & Audit."""

import pytest


@pytest.mark.e2e
class TestAuditLogAccess:
    """G.5: Audit-Log Admin View."""

    def test_admin_can_access_audit_log(self, authenticated_page, base_url):
        """Admin kann /audit/ aufrufen und sieht h1 sowie die Tabelle oder Leer-Zustand."""
        page = authenticated_page
        page.goto(f"{base_url}/audit/")

        # h1 "Audit-Log" ist sichtbar
        assert page.locator("h1").filter(has_text="Audit-Log").is_visible()

        # Entweder Tabelle oder "Keine Einträge gefunden." ist vorhanden
        has_table = page.locator("#audit-table table").count() > 0
        has_empty = page.locator("text=Keine Einträge gefunden.").count() > 0
        assert has_table or has_empty, "Weder Tabelle noch Leer-Zustand gefunden"

    def test_action_filter_works(self, authenticated_page, base_url):
        """Der Aktion-Filter schränkt die Ergebnisse ein und gibt valides HTML zurück."""
        page = authenticated_page

        # Direkt mit Filter-Parameter aufrufen (umgeht HTMX-Abhängigkeit)
        page.goto(f"{base_url}/audit/?action=login")
        page.wait_for_load_state("domcontentloaded")

        # Seite zeigt Audit-Log-Überschrift
        assert page.locator("h1").filter(has_text="Audit-Log").is_visible()

        # Filter-Dropdown ist sichtbar und auf "login" voreingestellt
        assert page.locator("select[name='action']").is_visible()
        selected = page.locator("select[name='action']").input_value()
        assert selected == "login", f"Erwarteter Filter 'login', bekam '{selected}'"

        # Entweder Einträge mit action=login oder Leer-Zustand
        has_table = page.locator("#audit-table table").count() > 0
        has_empty = page.locator("text=Keine Einträge gefunden.").count() > 0
        assert has_table or has_empty, "Weder Tabelle noch Leer-Zustand nach Filter gefunden"

    def test_non_admin_rejected(self, staff_page, base_url):
        """Staff-User (miriam) kann /audit/ nicht aufrufen — 403 oder Redirect auf Login."""
        page = staff_page

        # Zugriff auf /audit/ versuchen
        resp = page.goto(f"{base_url}/audit/")

        # Nicht-Admin wird abgewiesen: 403 oder Redirect auf Login
        assert resp.status == 403 or "/login/" in page.url, (
            f"Erwartet 403 oder Login-Redirect, bekam Status {resp.status} auf {page.url}"
        )
