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
    _login(page, base_url, "emma")

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
    values = _role_option_values(page, base_url, "emma")
    assert "super_admin" not in values
    assert "staff" in values


def test_super_admin_can_assign_super_admin_role(page: Page, base_url: str) -> None:
    """A2.1 (Refs #1020): super_admin behaelt die volle Rollen-Auswahl inkl. super_admin."""
    _login_and_sudo(page, base_url, "superadmin")
    values = _role_option_values(page, base_url, "emma")
    assert "super_admin" in values


# ---------------------------------------------------------------------------
# A2.3 (Refs #1021): OrganizationAdmin nur fuer super_admin
# Abgeleitet aus manueller Playwright-Verifikation 2026-06-03: facility_admin
# sieht Organisationen weder im Admin-Index noch per Direktzugriff (403),
# super_admin behaelt vollen Zugriff (200).
# ---------------------------------------------------------------------------
def test_facility_admin_cannot_access_organization_admin(page: Page, base_url: str) -> None:
    """A2.3: facility_admin -> /admin-mgmt/core/organization/ -> 403."""
    _login_and_sudo(page, base_url, "admin")
    response = page.goto(f"{base_url}/admin-mgmt/core/organization/")
    page.wait_for_load_state("domcontentloaded")
    assert response is not None and response.status == 403


def test_super_admin_can_access_organization_admin(page: Page, base_url: str) -> None:
    """A2.3: super_admin behaelt Zugriff auf OrganizationAdmin (200)."""
    _login_and_sudo(page, base_url, "superadmin")
    response = page.goto(f"{base_url}/admin-mgmt/core/organization/")
    page.wait_for_load_state("domcontentloaded")
    assert response is not None and response.status == 200


# ---------------------------------------------------------------------------
# A2.2 (Refs #1021): facility-FK zentral gescopt (FacilityScopedAdminMixin)
# ---------------------------------------------------------------------------
def _facility_option_labels(page: Page, base_url: str) -> list[str]:
    """Einrichtung-Select-Optionen einer schreibbaren facility-gescopten Add-Page lesen.

    Refs #1341: ClientAdmin ist jetzt read-only (Add gesperrt) — die A2.2-Scope-
    Pruefung nutzt daher die weiterhin schreibbare FieldTemplate-Add-Page, die
    denselben zentralen ``FacilityScopedAdminMixin.formfield_for_foreignkey``
    durchlaeuft.
    """
    page.goto(f"{base_url}/admin-mgmt/core/fieldtemplate/add/")
    page.wait_for_load_state("domcontentloaded")
    sel = page.locator("select[name='facility']")
    sel.wait_for(state="attached", timeout=30_000)
    return sel.evaluate("el => Array.from(el.options).map(o => o.text)")


def _create_facility(page: Page, base_url: str, name: str) -> None:
    """Zweite Facility als super_admin ueber die Admin-UI anlegen (committed)."""
    page.goto(f"{base_url}/admin-mgmt/core/facility/add/")
    page.wait_for_load_state("domcontentloaded")
    page.locator("input[name='name']").wait_for(timeout=30_000)
    page.locator("input[name='name']").fill(name)
    page.locator("select[name='organization']").select_option(index=1)
    page.get_by_role("button", name="Sichern", exact=True).click()
    # Erfolg: Redirect auf die Facility-Changelist (URL-Anker, 30s gegen Worker-Last).
    page.wait_for_url(lambda url: url.rstrip("/").endswith("/core/facility"), timeout=30_000)
    page.wait_for_load_state("domcontentloaded")


def test_facility_admin_facility_select_excludes_other_facility(page: Page, base_url: str) -> None:
    """A2.2: facility_admin sieht im facility-FK NUR die eigene Facility.

    Abgeleitet aus manueller Playwright-Verifikation 2026-06-03: super_admin legt
    eine zweite Facility an und sieht beide im Einrichtung-Select der Client-Add-
    Page; facility_admin sieht nach Session-Wechsel weiterhin nur die eigene
    ('Hauptstelle'). Beweist das zentrale Scoping im FacilityScopedAdminMixin —
    es greift fuer ClientAdmin (nicht nur den lokal in A2.1 abgesicherten User).
    """
    other = "Zweigstelle E2E"
    _login_and_sudo(page, base_url, "superadmin")
    _create_facility(page, base_url, other)
    super_labels = _facility_option_labels(page, base_url)
    assert any(other in label for label in super_labels), super_labels
    assert any("Hauptstelle" in label for label in super_labels), super_labels

    # Session droppen -> als facility_admin neu anmelden + Sudo.
    page.context.clear_cookies()
    _login_and_sudo(page, base_url, "admin")
    admin_labels = _facility_option_labels(page, base_url)
    assert any("Hauptstelle" in label for label in admin_labels), admin_labels
    assert not any(other in label for label in admin_labels), admin_labels


# ---------------------------------------------------------------------------
# AUTHZ-1 (Refs #1341): Fachobjekte im Admin strikt read-only.
# Ein Admin-Save wuerde die Service-Invarianten (Feld-Krypto, EventHistory-Diff,
# Vier-Augen-Loeschung, Legal-Hold) und das Domaenen-AuditLog umgehen -> Add/
# Change/Delete fuer Client/Case/Event/WorkItem sind gesperrt, die Read-Only-
# Sicht bleibt.
# ---------------------------------------------------------------------------
def test_client_admin_add_is_forbidden(page: Page, base_url: str) -> None:
    """AUTHZ-1: super_admin -> /admin-mgmt/core/client/add/ -> 403."""
    _login_and_sudo(page, base_url, "superadmin")
    response = page.goto(f"{base_url}/admin-mgmt/core/client/add/")
    page.wait_for_load_state("domcontentloaded")
    assert response is not None and response.status == 403


def test_client_admin_change_is_read_only(page: Page, base_url: str) -> None:
    """AUTHZ-1: Client-Change-Page laedt (200), aber ohne Speichern-Button.

    Django rendert bei ``has_change_permission=False`` + ``has_view_permission=
    True`` die read-only-Ansicht ohne Submit-Row (kein ``_save``).
    """
    _login_and_sudo(page, base_url, "superadmin")
    page.goto(f"{base_url}/admin-mgmt/core/client/")
    page.wait_for_load_state("domcontentloaded")
    change_link = page.locator("a[href*='/core/client/'][href$='/change/']").first
    change_link.wait_for(state="attached", timeout=30_000)
    href = change_link.get_attribute("href")
    response = page.goto(f"{base_url}{href}")
    page.wait_for_load_state("domcontentloaded")
    assert response is not None and response.status == 200
    # Read-Only: kein Speichern-Button in der Submit-Row.
    # Unfold rendert den ``_save``-Button als <button name="_save"> (nie als
    # <input>); daher elementtyp-agnostisch per Attribut selektieren, sonst
    # matcht der Selektor nie und die Assertion bleibt trivial gruen.
    assert page.locator("[name='_save']").count() == 0
