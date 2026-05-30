"""E2E-Smoke-Tests fuer Custom AdminSite (Refs #785).

Abgeleitet aus manueller Playwright-Verifikation 2026-05-21 auf E2E-Server
Port 8844. Wait-Strategie: wait_for_url(), nie networkidle.

Beobachtetes Verhalten:
- super_admin/facility_admin -> /admin-mgmt/ -> /sudo/?next=/admin-mgmt/login/?next=/admin-mgmt/
  Nach Sudo-Form-Submit -> /admin-mgmt/ mit Datenverwaltung-Index.
- lead/staff/assistant -> /admin-mgmt/ -> /admin-mgmt/login/?next=/admin-mgmt/
  (Rollen-Block, kein Sudo-Redirect).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

pytestmark = pytest.mark.e2e


def _login(page: Page, base_url: str, username: str, password: str = "anlaufstelle2026") -> None:
    """Login via /login/-Form. Wartet, bis Login-Page verlassen ist."""
    page.goto(f"{base_url}/login/")
    page.locator("input[name='username']").fill(username)
    page.locator("input[name='password']").fill(password)
    page.get_by_role("button", name="Anmelden").click()
    page.wait_for_url(lambda url: "/login/" not in url, timeout=5000)


def _do_sudo(page: Page, password: str = "anlaufstelle2026") -> None:
    """Sudo-Form ausfuellen, wenn die Seite auf /sudo/ steht."""
    page.locator("input[name='password']").fill(password)
    page.get_by_role("button", name="Bestätigen und fortfahren").click()


def test_superadmin_accesses_admin_via_sudo(page: Page, base_url: str) -> None:
    """super_admin: /admin-mgmt/ -> Sudo-Form -> Re-Auth -> Admin-Index."""
    _login(page, base_url, "superadmin")

    page.goto(f"{base_url}/admin-mgmt/")
    # Erwartet: Redirect-Chain endet auf /sudo/ (mit verschachteltem next).
    page.wait_for_url(lambda url: "/sudo/" in url, timeout=5000)

    _do_sudo(page)
    # Nach Sudo: zurueck auf /admin-mgmt/.
    page.wait_for_url(lambda url: url.endswith("/admin-mgmt/"), timeout=5000)
    assert "Anlaufstelle" in page.title()


def test_facility_admin_accesses_admin_via_sudo(page: Page, base_url: str) -> None:
    """facility_admin: /admin-mgmt/ -> Sudo -> Admin-Index."""
    _login(page, base_url, "admin")

    page.goto(f"{base_url}/admin-mgmt/")
    page.wait_for_url(lambda url: "/sudo/" in url, timeout=5000)

    _do_sudo(page)
    page.wait_for_url(lambda url: url.endswith("/admin-mgmt/"), timeout=5000)


def test_lead_blocked_from_admin(page: Page, base_url: str) -> None:
    """lead: /admin-mgmt/ -> /admin-mgmt/login/ (Rollen-Block)."""
    _login(page, base_url, "thomas")

    page.goto(f"{base_url}/admin-mgmt/")
    page.wait_for_url(lambda url: "/admin-mgmt/login/" in url, timeout=5000)


def test_staff_blocked_from_admin(page: Page, base_url: str) -> None:
    """staff: /admin-mgmt/ -> /admin-mgmt/login/."""
    _login(page, base_url, "miriam")

    page.goto(f"{base_url}/admin-mgmt/")
    page.wait_for_url(lambda url: "/admin-mgmt/login/" in url, timeout=5000)


def test_assistant_blocked_from_admin(page: Page, base_url: str) -> None:
    """assistant: /admin-mgmt/ -> /admin-mgmt/login/."""
    _login(page, base_url, "lena")

    page.goto(f"{base_url}/admin-mgmt/")
    page.wait_for_url(lambda url: "/admin-mgmt/login/" in url, timeout=5000)
