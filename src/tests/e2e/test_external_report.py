"""E2E-Tests: Die alte Adresse des externen Berichts (Refs #921, #2505).

Seit #2505 (E-1 a) gibt es keinen Reiter „Externer Bericht" mehr. Die Adresse
``/statistics/external/`` leitet — auch mit ``?format=json`` — mit Zeitraum auf
die Karte „Allgemeine Kennzahlen" der Datensatz-Berichte um und liefert keine
Datei. Wait-Strategie: domcontentloaded + Anchor-First (locator.wait_for),
nie networkidle, nie wait_for_url mit kurzem Timeout.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from ._helpers import SEED_PASSWORD

pytestmark = pytest.mark.e2e


def _login(page: Page, base_url: str, username: str, password: str = SEED_PASSWORD) -> None:
    """Login via /login/-Form. Wartet auf Navigations-Abmelden-Button als Login-Anker."""
    page.goto(f"{base_url}/login/")
    page.locator("input[name='username']").fill(username)
    page.locator("input[name='password']").fill(password)
    page.get_by_role("button", name="Anmelden").click()
    page.wait_for_load_state("domcontentloaded")
    # Abmelden-Button in Desktop-Navigation erscheint nur nach erfolgreichem Login.
    page.get_by_role("button", name="Abmelden").first.wait_for(timeout=30_000)


def test_lead_is_redirected_to_the_general_key_figures_card(page: Page, base_url: str) -> None:
    """lead: /statistics/external/ -> Karte „Allgemeine Kennzahlen" mit Vorschau."""
    _login(page, base_url, "emma")

    page.goto(f"{base_url}/statistics/external/")
    page.wait_for_load_state("domcontentloaded")

    bar = page.get_by_test_id("export-bar")
    bar.wait_for(timeout=30_000)
    assert "/statistics/" in page.url
    assert "bericht=general-key-figures" in page.url, page.url
    assert "ansicht=datensaetze" in page.url, page.url
    expect(page.locator("#report-panel h3")).to_have_text("Allgemeine Kennzahlen")
    # Refs #1743: sichtbarer Quellenausweis der Vorschau.
    assert "Live-Abfrage zum Erstellungszeitpunkt" in page.locator("#report-panel").inner_text()


def test_json_address_redirects_and_keeps_the_period(page: Page, base_url: str) -> None:
    """?format=json liefert keine Datei mehr, sondern leitet mit Zeitraum um."""
    _login(page, base_url, "emma")

    page.goto(f"{base_url}/statistics/external/?format=json&zeitraum=jahr&year=2025")
    page.wait_for_load_state("domcontentloaded")

    page.get_by_test_id("export-bar").wait_for(timeout=30_000)
    assert "zeitraum=jahr" in page.url, page.url
    assert "year=2025" in page.url, page.url
    assert "format=json" not in page.url, page.url
    # Kein JSON-Dokument: die Seite ist die Anwendung, nicht ein Datensatz.
    assert "privacy_profile" not in page.evaluate("() => document.body.innerText")
    assert "2025" in page.get_by_test_id("statistics-period-range").inner_text()


def test_the_external_tab_is_gone(page: Page, base_url: str) -> None:
    """Die Reiterzeile kennt nur noch Übersicht, Datensatz-Berichte, Freigaben."""
    _login(page, base_url, "emma")

    page.goto(f"{base_url}/statistics/")
    page.wait_for_load_state("domcontentloaded")
    subnav = page.get_by_test_id("subnav-statistik")
    subnav.wait_for(timeout=30_000)

    assert subnav.locator("[data-testid='subnav-tab-statistik_external_report']").count() == 0
    assert subnav.locator("[data-testid^='subnav-tab-']").count() == 3


def test_staff_blocked_from_the_old_external_address(page: Page, base_url: str) -> None:
    """staff: /statistics/external/ -> 403 (LeadOrAdminRequiredMixin)."""
    _login(page, base_url, "miriam")

    page.goto(f"{base_url}/statistics/external/")
    page.wait_for_load_state("domcontentloaded")
    # Erwartet: 403-Page mit "Zugriff verweigert"-Title.
    assert "403" in page.title() or "verweigert" in page.title().lower()
