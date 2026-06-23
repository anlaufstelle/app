"""E2E-Smoke-Tests fuer Externe Berichte (Refs #921, gehärtet Refs #992).

Abgeleitet aus manueller Playwright-Verifikation 2026-05-21 auf E2E-Server
Port 8844. Wait-Strategie: domcontentloaded + Anchor-First (locator.wait_for),
nie networkidle, nie wait_for_url mit kurzem Timeout.

Beobachtetes Verhalten:
- lead (emma) -> HTML-Bericht 200 mit Datenschutzprofil-Block, K-Anon=5,
  Aggregate < 5 als "unterdrueckt" angezeigt
- ?format=json liefert JSON ohne "top_clients", mit "metadata"-Block
- staff (miriam) -> 403 Zugriff verweigert
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

pytestmark = pytest.mark.e2e


def _login(page: Page, base_url: str, username: str, password: str = "anlaufstelle2026") -> None:
    """Login via /login/-Form. Wartet auf Navigations-Abmelden-Button als Login-Anker."""
    page.goto(f"{base_url}/login/")
    page.locator("input[name='username']").fill(username)
    page.locator("input[name='password']").fill(password)
    page.get_by_role("button", name="Anmelden").click()
    page.wait_for_load_state("domcontentloaded")
    # Abmelden-Button in Desktop-Navigation erscheint nur nach erfolgreichem Login.
    page.get_by_role("button", name="Abmelden").first.wait_for(timeout=30_000)


def test_lead_can_view_external_report_html(page: Page, base_url: str) -> None:
    """lead: /statistics/external/ -> 200 HTML mit Datenschutzprofil."""
    _login(page, base_url, "emma")

    page.goto(f"{base_url}/statistics/external/")
    page.wait_for_load_state("domcontentloaded")

    # Datenschutzprofil-Heading (H2) als Anker — danach Title-Assertion.
    heading = page.get_by_role("heading", name="Datenschutzprofil", exact=True)
    heading.wait_for(timeout=30_000)
    assert "Externer Bericht" in page.title()
    assert heading.is_visible()


def test_external_report_json_endpoint(page: Page, base_url: str) -> None:
    """?format=json liefert JSON ohne Pseudonym-Ranking, mit metadata-Block."""
    _login(page, base_url, "emma")

    page.goto(f"{base_url}/statistics/external/?format=json")
    page.wait_for_load_state("domcontentloaded")
    body_text = page.evaluate("() => document.body.innerText")

    # Wichtige Strukturen muessen drin sein:
    assert "metadata" in body_text
    assert "privacy_profile" in body_text
    assert "k_anonymity_threshold" in body_text
    # Pseudonym-Ranking darf NICHT drin sein:
    assert "top_clients" not in body_text


def test_staff_blocked_from_external_report(page: Page, base_url: str) -> None:
    """staff: /statistics/external/ -> 403 (LeadOrAdminRequiredMixin)."""
    _login(page, base_url, "miriam")

    page.goto(f"{base_url}/statistics/external/")
    page.wait_for_load_state("domcontentloaded")
    # Erwartet: 403-Page mit "Zugriff verweigert"-Title.
    assert "403" in page.title() or "verweigert" in page.title().lower()
