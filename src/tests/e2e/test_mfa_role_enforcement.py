"""E2E: Rollen-basiertes MFA-Enforcement (A3.1, Refs #1019).

Gegen den ``enforced_base_url``-Server (``E2E_MFA_ENFORCE_ROLES=1`` →
``MFA_ENFORCE_PRIVILEGED_ROLES=True``):

* facility_admin (Seed-Login ``admin``) ohne TOTP-Gerät wird beim ersten
  Request auf ``/mfa/setup/`` gezwungen.
* staff (Seed-Login ``miriam``) ist nicht rollen-erzwungen und landet direkt
  auf dem Dashboard.

Manuell-first verifiziert (Port 8944): admin → /mfa/setup/, miriam → /.
Der Default-Server bleibt MFA-frei (s. Unit-Tests
``TestMFARoleEnforcementDisabledByDefault`` + ``test_settings_mfa_guard``).
"""

import pytest

pytestmark = pytest.mark.e2e


def _login(browser, base_url, username, password="anlaufstelle2026"):
    """Frischer Login gegen den angegebenen Server; gibt (page, context) zurück."""
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    page.set_default_navigation_timeout(30000)
    page.goto(f"{base_url}/login/")
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')
    return page, context


def test_facility_admin_is_forced_into_mfa_setup(enforced_base_url, browser):
    """facility_admin ohne Gerät → Rollen-Redirect auf /mfa/setup/."""
    page, context = _login(browser, enforced_base_url, "admin")
    try:
        page.wait_for_url("**/mfa/setup/", timeout=30000)
        assert page.url.endswith("/mfa/setup/")
    finally:
        context.close()


def test_staff_is_not_forced_into_mfa_setup(enforced_base_url, browser):
    """staff ist nicht rollen-erzwungen → Dashboard statt /mfa/setup/."""
    page, context = _login(browser, enforced_base_url, "miriam")
    try:
        page.wait_for_url(lambda url: "/login/" not in url, timeout=30000)
        assert "/mfa/setup/" not in page.url
        assert page.url.rstrip("/") == enforced_base_url.rstrip("/")
    finally:
        context.close()
