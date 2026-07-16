"""E2E-Tests: globaler Auf-/Zuklapp-Toggle im Zeitstrom (Refs #1588).

Prüft die persistente Voreinstellung (localStorage), die Barrierefreiheit
(``aria-pressed`` am Toggle, ``aria-expanded`` pro Card, Tastaturbedienung)
sowie die Übernahme des globalen Zustands durch HTMX-nachgeladene Cards.

Diese Datei wird NICHT von parallelen Agents ausgeführt — seriell in der
Integration laufen lassen (E2E-Port 8844).
"""

import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

TOGGLE = "[data-testid='zeitstrom-collapse-toggle']"
# Kopfzeilen-Buttons der Event-Cards tragen aria-controls="event-detail-<pk>".
EVENT_CARD_BUTTON = "#feed-list button[aria-controls^='event-detail-']"


def _create_kontakt_event(page, base_url):
    """Legt einen anonymen Kontakt (occurred_at=jetzt) an → erscheint im Feed."""
    page.goto(f"{base_url}/events/new/")
    page.wait_for_load_state("domcontentloaded")
    with page.expect_response(lambda r: "fields" in r.url and r.request.method == "GET"):
        page.select_option("select[name='document_type']", label="Kontakt")
    dauer = page.locator("input[name='dauer']")
    dauer.wait_for(state="visible", timeout=10_000)
    dauer.fill("5")
    page.click("button:has-text('Speichern')")
    page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/$"))


class TestZeitstromCollapseToggle:
    def test_toggle_visible_and_keyboard_focusable(self, authenticated_page, base_url):
        """Der Toggle ist sichtbar, per Tastatur erreichbar und beschriftet."""
        page = authenticated_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        toggle = page.locator(TOGGLE)
        expect(toggle).to_be_visible()
        # Default eingeklappt → aria-pressed=false, Label "Alle aufklappen".
        expect(toggle).to_have_attribute("aria-pressed", "false")
        expect(toggle).to_contain_text("Alle aufklappen")

        # Fokussierbar per Tastatur (nativer <button>).
        toggle.focus()
        assert toggle.evaluate("el => el === document.activeElement")

    def test_toggle_expands_all_cards_and_persists_after_reload(self, authenticated_page, base_url):
        """Toggle klappt alle Cards auf; der Zustand überdauert einen Reload."""
        page = authenticated_page
        _create_kontakt_event(page, base_url)

        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        card_button = page.locator(EVENT_CARD_BUTTON).first
        card_button.wait_for(state="visible", timeout=10_000)
        # Ausgangszustand: eingeklappt.
        expect(card_button).to_have_attribute("aria-expanded", "false")

        toggle = page.locator(TOGGLE)
        toggle.click()
        # Toggle meldet den aufgeklappten Zustand, Label wechselt.
        expect(toggle).to_have_attribute("aria-pressed", "true")
        expect(toggle).to_contain_text("Alle einklappen")
        # Bereits gerenderte Card zieht per Fensterevent nach.
        expect(card_button).to_have_attribute("aria-expanded", "true")

        # Reload → persistierter Zustand (localStorage) bleibt aufgeklappt.
        page.reload()
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator(TOGGLE)).to_have_attribute("aria-pressed", "true")
        reloaded_card = page.locator(EVENT_CARD_BUTTON).first
        reloaded_card.wait_for(state="visible", timeout=10_000)
        expect(reloaded_card).to_have_attribute("aria-expanded", "true")

    def test_toggle_collapse_again_persists(self, authenticated_page, base_url):
        """Erneutes Betätigen klappt wieder ein und persistiert den Zustand."""
        page = authenticated_page
        _create_kontakt_event(page, base_url)

        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")
        toggle = page.locator(TOGGLE)

        # Aufklappen …
        toggle.click()
        expect(toggle).to_have_attribute("aria-pressed", "true")
        # … und wieder einklappen.
        toggle.click()
        expect(toggle).to_have_attribute("aria-pressed", "false")

        page.reload()
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator(TOGGLE)).to_have_attribute("aria-pressed", "false")
        card_button = page.locator(EVENT_CARD_BUTTON).first
        card_button.wait_for(state="visible", timeout=10_000)
        expect(card_button).to_have_attribute("aria-expanded", "false")

    def test_toggle_via_keyboard_enter(self, authenticated_page, base_url):
        """Der Toggle lässt sich per Tastatur (Enter) betätigen."""
        page = authenticated_page
        _create_kontakt_event(page, base_url)

        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")
        toggle = page.locator(TOGGLE)
        toggle.focus()
        page.keyboard.press("Enter")
        expect(toggle).to_have_attribute("aria-pressed", "true")

        card_button = page.locator(EVENT_CARD_BUTTON).first
        expect(card_button).to_have_attribute("aria-expanded", "true")

    def test_single_card_toggle_stays_local(self, authenticated_page, base_url):
        """Einzel-Toggle einer Card ändert den globalen Modus nicht."""
        page = authenticated_page
        _create_kontakt_event(page, base_url)

        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        card_button = page.locator(EVENT_CARD_BUTTON).first
        card_button.wait_for(state="visible", timeout=10_000)
        # Einzelne Card aufklappen …
        card_button.click()
        expect(card_button).to_have_attribute("aria-expanded", "true")
        # … der globale Toggle bleibt unberührt (weiterhin "eingeklappt").
        expect(page.locator(TOGGLE)).to_have_attribute("aria-pressed", "false")

    def test_htmx_reloaded_cards_adopt_global_state(self, authenticated_page, base_url):
        """Nach 'Alle aufklappen' übernehmen HTMX-nachgeladene Cards den Zustand."""
        page = authenticated_page
        _create_kontakt_event(page, base_url)

        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        # Global aufklappen.
        page.locator(TOGGLE).click()
        expect(page.locator(TOGGLE)).to_have_attribute("aria-pressed", "true")

        # 'Alle'-Tab → HTMX-innerHTML-Swap von #feed-list (Tagesansicht, damit der
        # eben erstellte Kontakt unabhängig von der aktuellen Schicht sichtbar ist).
        all_tab = page.locator("[data-testid='time-filter-tabs']").get_by_role("tab", name="Alle", exact=True)
        with page.expect_response(lambda r: "time_filter=all" in r.url):
            all_tab.click()

        # Die frisch eingeswappte Card übernimmt per x-init den globalen
        # (aufgeklappten) Zustand — ohne dass ein voller Reload nötig war.
        card_button = page.locator(EVENT_CARD_BUTTON).first
        card_button.wait_for(state="visible", timeout=10_000)
        expect(card_button).to_have_attribute("aria-expanded", "true")
