"""E2E-Tests für Fuzzy Search via PostgreSQL pg_trgm (Refs #536)."""

import re
import uuid

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


class TestFuzzySearch:
    """Tippfehlertolerante Pseudonym-Suche."""

    def _create_client(self, page, base_url, pseudonym):
        page.goto(f"{base_url}/clients/new/")
        page.wait_for_load_state("domcontentloaded")
        page.fill("input[name='pseudonym']", pseudonym)
        page.click("#main-content button[type='submit']")
        page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))

    def test_full_search_page_shows_similar_section(self, authenticated_page, base_url):
        """Volltextsuche: 'Schmitt' findet angelegtes 'Schmidt' in 'Ähnliche Pseudonyme'."""
        page = authenticated_page
        suffix = uuid.uuid4().hex[:4]
        pseudo = f"Schmidt-{suffix}"
        typo = f"Schmitt-{suffix}"
        self._create_client(page, base_url, pseudo)

        page.goto(f"{base_url}/search/?q={typo}")
        page.wait_for_load_state("domcontentloaded")
        content = page.content()
        assert "Ähnliche Pseudonyme" in content
        assert pseudo in content

    def test_exact_match_not_duplicated_in_similar(self, authenticated_page, base_url):
        """Exakter Treffer erscheint nur in 'Klientel', nicht zusätzlich in 'Ähnliche'."""
        page = authenticated_page
        suffix = uuid.uuid4().hex[:4]
        pseudo = f"Müller-{suffix}"
        self._create_client(page, base_url, pseudo)

        page.goto(f"{base_url}/search/?q={pseudo}")
        page.wait_for_load_state("domcontentloaded")
        content = page.content()
        assert "Personen (1)" in content
        assert "Ähnliche Pseudonyme" not in content

    def test_global_dropdown_shows_similar(self, authenticated_page, base_url):
        """Globale Sidebar-Suche zeigt Fuzzy-Treffer unter 'Ähnliche Pseudonyme'."""
        page = authenticated_page
        suffix = uuid.uuid4().hex[:4]
        pseudo = f"Schmidt-{suffix}"
        typo = f"Schmitt-{suffix}"
        self._create_client(page, base_url, pseudo)

        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")
        search_input = page.locator("[data-testid='global-search-input']")
        search_input.click()
        search_input.press_sequentially(typo, delay=50)

        # Der Results-Container wird schon vor dem HTMX-Fetch sichtbar
        # — warten, bis die Fuzzy-Sektion wirklich mit Daten gefüllt ist.
        results = page.locator("[data-testid='global-search-results']")
        results.wait_for(state="visible", timeout=5000)
        expect(results).to_contain_text(pseudo, timeout=5000)
        text = results.inner_text()
        assert "ähnliche pseudonyme" in text.lower()
