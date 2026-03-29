"""E2E-Tests: Audit-Log Zugriffskontrolle für Staff und Assistenz.

Nur neue Tests: Staff und Assistenz können /audit/ nicht aufrufen.

Bereits abgedeckt in test_stream_g.py:
- Admin kann Audit-Log einsehen
- Action-Filter funktioniert
- Non-Admin wird abgewiesen (allgemein)
"""

import pytest

pytestmark = pytest.mark.e2e


class TestStaffCannotAccessAuditLog:
    """Staff-Rolle hat keinen Zugriff auf Audit-Log."""

    def test_staff_no_audit_access(self, staff_page, base_url):
        resp = staff_page.goto(f"{base_url}/audit/")
        assert resp.status == 403

    def test_staff_no_audit_nav_link(self, staff_page):
        nav = staff_page.locator("nav[aria-label='Hauptnavigation']")
        assert nav.locator("a:has-text('Audit')").count() == 0


class TestAssistantCannotAccessAuditLog:
    """Assistenz-Rolle hat keinen Zugriff auf Audit-Log."""

    def test_assistant_no_audit_access(self, assistant_page, base_url):
        resp = assistant_page.goto(f"{base_url}/audit/")
        assert resp.status == 403

    def test_assistant_no_audit_nav_link(self, assistant_page):
        nav = assistant_page.locator("nav[aria-label='Hauptnavigation']")
        assert nav.locator("a:has-text('Audit')").count() == 0
