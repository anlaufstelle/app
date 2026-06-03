"""E2E-Smoke-Tests fuer Custom AdminSite (Refs #785, Refs #992).

Abgeleitet aus manueller Playwright-Verifikation 2026-05-21 auf E2E-Server
Port 8844. Wait-Strategie: wait_for_load_state("domcontentloaded") +
Element-Anker; nie networkidle, nie wait_for_url() mit kurzem Timeout
(racet unter playwright 1.60 und Worker-Last).

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
    """Login via /login/-Form. Wartet auf Logout-Button als Login-Anker."""
    page.goto(f"{base_url}/login/")
    page.locator("input[name='username']").fill(username)
    page.locator("input[name='password']").fill(password)
    page.get_by_role("button", name="Anmelden").click()
    page.wait_for_load_state("domcontentloaded")
    page.get_by_role("button", name="Abmelden").first.wait_for(timeout=30_000)


def _do_sudo(page: Page, password: str = "anlaufstelle2026") -> None:
    """Sudo-Form ausfuellen, wenn die Seite auf /sudo/ steht."""
    page.locator("input[name='password']").fill(password)
    page.get_by_role("button", name="Bestätigen und fortfahren").click()


def _login_and_sudo(page: Page, base_url: str, username: str) -> None:
    """Login + Sudo-Re-Auth bis zum Admin-Index (Refs #785)."""
    _login(page, base_url, username)
    page.goto(f"{base_url}/admin-mgmt/")
    page.wait_for_load_state("domcontentloaded")
    page.locator("input[name='password']").wait_for(timeout=30_000)
    _do_sudo(page)
    page.wait_for_url(lambda url: url.rstrip("/").endswith("/admin-mgmt"), timeout=30_000)
    page.wait_for_load_state("domcontentloaded")


def _role_option_values(page: Page, base_url: str, username: str) -> list[str]:
    """Change-Page eines Users via Changelist oeffnen und die role-Select-Optionen lesen."""
    page.goto(f"{base_url}/admin-mgmt/core/user/")
    page.wait_for_load_state("domcontentloaded")
    page.get_by_role("link", name=username, exact=True).first.click()
    page.wait_for_load_state("domcontentloaded")
    role_select = page.locator("select[name='role']")
    role_select.wait_for(state="attached", timeout=30_000)
    return role_select.evaluate("el => Array.from(el.options).map(o => o.value)")


def test_superadmin_accesses_admin_via_sudo(page: Page, base_url: str) -> None:
    """super_admin: /admin-mgmt/ -> Sudo-Form -> Re-Auth -> Admin-Index."""
    _login(page, base_url, "superadmin")

    page.goto(f"{base_url}/admin-mgmt/")
    page.wait_for_load_state("domcontentloaded")
    # Sudo-Form: Password-Input + Bestaetigen-Button als Anker.
    page.locator("input[name='password']").wait_for(timeout=30_000)

    _do_sudo(page)
    # Nach Sudo: zurueck auf /admin-mgmt/ (URL-Anker, 30s gegen Worker-Last).
    page.wait_for_url(lambda url: url.rstrip("/").endswith("/admin-mgmt"), timeout=30_000)
    page.wait_for_load_state("domcontentloaded")
    assert "Anlaufstelle" in page.title()


def test_facility_admin_accesses_admin_via_sudo(page: Page, base_url: str) -> None:
    """facility_admin: /admin-mgmt/ -> Sudo -> Admin-Index."""
    _login(page, base_url, "admin")

    page.goto(f"{base_url}/admin-mgmt/")
    page.wait_for_load_state("domcontentloaded")
    page.locator("input[name='password']").wait_for(timeout=30_000)

    _do_sudo(page)
    page.wait_for_url(lambda url: url.rstrip("/").endswith("/admin-mgmt"), timeout=30_000)
    page.wait_for_load_state("domcontentloaded")


def test_lead_blocked_from_admin(page: Page, base_url: str) -> None:
    """lead: /admin-mgmt/ -> /admin-mgmt/login/ (Rollen-Block)."""
    _login(page, base_url, "thomas")

    page.goto(f"{base_url}/admin-mgmt/")
    page.wait_for_load_state("domcontentloaded")
    # AdminSite-Login-Form als Anker (Rollen-Block landet hier statt /sudo/).
    page.locator("input[name='username']").wait_for(timeout=30_000)
    assert "/admin-mgmt/login/" in page.url


def test_staff_blocked_from_admin(page: Page, base_url: str) -> None:
    """staff: /admin-mgmt/ -> /admin-mgmt/login/."""
    _login(page, base_url, "miriam")

    page.goto(f"{base_url}/admin-mgmt/")
    page.wait_for_load_state("domcontentloaded")
    page.locator("input[name='username']").wait_for(timeout=30_000)
    assert "/admin-mgmt/login/" in page.url


def test_assistant_blocked_from_admin(page: Page, base_url: str) -> None:
    """assistant: /admin-mgmt/ -> /admin-mgmt/login/."""
    _login(page, base_url, "lena")

    page.goto(f"{base_url}/admin-mgmt/")
    page.wait_for_load_state("domcontentloaded")
    page.locator("input[name='username']").wait_for(timeout=30_000)
    assert "/admin-mgmt/login/" in page.url


def test_facility_admin_cannot_assign_super_admin_role(page: Page, base_url: str) -> None:
    """A2.1 (Refs #1020): facility_admin sieht super_admin NICHT als vergebbare Rolle.

    Abgeleitet aus manueller Playwright-Verifikation 2026-06-03: in der User-
    Change-Page bietet das role-Select fuer facility_admin nur facility_admin/
    lead/staff/assistant an — keine Eskalation auf die installationsweite
    super_admin-Rolle.
    """
    _login_and_sudo(page, base_url, "admin")
    values = _role_option_values(page, base_url, "thomas")
    assert "super_admin" not in values
    assert "staff" in values


def test_super_admin_can_assign_super_admin_role(page: Page, base_url: str) -> None:
    """A2.1 (Refs #1020): super_admin behaelt die volle Rollen-Auswahl inkl. super_admin."""
    _login_and_sudo(page, base_url, "superadmin")
    values = _role_option_values(page, base_url, "thomas")
    assert "super_admin" in values
