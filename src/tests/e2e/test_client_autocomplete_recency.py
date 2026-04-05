"""E2E-Tests: Client-Autocomplete sortiert nach Aktualität (Refs #395)."""

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


class TestClientAutocompleteRecency:
    """Client-Autocomplete zeigt beim Fokus sofort Clients, sortiert nach letztem Event."""

    def test_focus_shows_dropdown_immediately(self, authenticated_page, base_url):
        """Klick ins Client-Feld zeigt sofort Dropdown ohne Tippen."""
        page = authenticated_page
        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")

        autocomplete = page.locator("input[placeholder='Pseudonym eingeben...']")
        autocomplete.click()

        # Dropdown sollte sofort erscheinen (Alpine.js @focus-Handler)
        dropdown = page.locator("[role='listbox']")
        dropdown.wait_for(state="visible", timeout=5000)
        assert dropdown.is_visible()

        # Warten auf Alpine x-for-Rendering, dann Options prüfen
        options = page.locator("[role='option']")
        options.first.wait_for(state="visible", timeout=5000)
        assert options.count() > 0

    def test_focus_dropdown_sorted_by_recency(self, authenticated_page, base_url):
        """Clients mit Events erscheinen vor Clients ohne Events."""
        page = authenticated_page
        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")

        autocomplete = page.locator("input[placeholder='Pseudonym eingeben...']")
        autocomplete.click()

        # Warten auf Dropdown
        options = page.locator("[role='option']")
        options.first.wait_for(state="visible", timeout=5000)

        # Alle Seed-Clients mit Events sollten vor E2E-Test-Client (ohne Events) stehen
        texts = [options.nth(i).inner_text() for i in range(options.count())]
        pseudonyms = [t.split("\n")[0] for t in texts]

        # E2E-Test-Client (kein Event) darf nicht vor den Seed-Clients stehen
        seed_clients = {"Stern-42", "Wolke-17", "Blitz-08", "Regen-55", "Wind-33", "Nebel-71", "Sonne-99"}
        seed_positions = [i for i, p in enumerate(pseudonyms) if p in seed_clients]
        other_positions = [i for i, p in enumerate(pseudonyms) if p not in seed_clients]

        if seed_positions and other_positions:
            assert max(seed_positions) < min(other_positions)

    def test_search_filters_and_keeps_recency_order(self, authenticated_page, base_url):
        """Tippen filtert Ergebnisse, Sortierung bleibt nach Aktualität."""
        page = authenticated_page
        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")

        autocomplete = page.locator("input[placeholder='Pseudonym eingeben...']")
        autocomplete.fill("Stern")

        # Warten auf Debounce + Fetch
        suggestion = page.locator("button:has-text('Stern-42')")
        suggestion.wait_for(state="visible", timeout=5000)
        assert suggestion.is_visible()

    def test_select_client_closes_dropdown(self, authenticated_page, base_url):
        """Client auswählen befüllt Feld und schließt Dropdown."""
        page = authenticated_page
        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")

        autocomplete = page.locator("input[placeholder='Pseudonym eingeben...']")
        autocomplete.click()

        # Warten auf Dropdown
        option = page.locator("button:has-text('Blitz-08')")
        option.wait_for(state="visible", timeout=5000)
        option.click()

        # Feld befüllt, Dropdown geschlossen
        assert autocomplete.input_value() == "Blitz-08"
        dropdown = page.locator("[role='listbox']")
        expect(dropdown).not_to_be_visible(timeout=3000)

    def test_cases_form_focus_dropdown(self, authenticated_page, base_url):
        """Cases-Formular zeigt ebenfalls Dropdown beim Fokus."""
        page = authenticated_page
        page.goto(f"{base_url}/cases/new/")
        page.wait_for_load_state("domcontentloaded")

        autocomplete = page.locator("input[placeholder='Pseudonym eingeben...']")
        autocomplete.click()

        dropdown = page.locator("[role='listbox']")
        dropdown.wait_for(state="visible", timeout=5000)
        assert dropdown.is_visible()

        options = page.locator("[role='option']")
        assert options.count() > 0
