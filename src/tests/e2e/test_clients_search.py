"""E2E-Tests: Klientel-Suche, Navigation, Filter.

Seed-Klientel:
- Stern-42: QUALIFIED, AGE_18_26
- Wolke-17: QUALIFIED, AGE_27_PLUS
- Blitz-08: IDENTIFIED, U18
- Regen-55: IDENTIFIED, AGE_27_PLUS
- Wind-33: QUALIFIED, AGE_18_26
- Nebel-71: IDENTIFIED, UNKNOWN
- Sonne-99: QUALIFIED, AGE_27_PLUS
"""

import re
import uuid

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


class TestClientManagement:
    """Klientel-Liste, Detail, Erstellen."""

    def test_client_list_search(self, authenticated_page, base_url):
        """Klientel-Liste → Suche funktioniert."""
        page = authenticated_page
        page.goto(f"{base_url}/clients/")

        assert page.locator("h1").inner_text() == "Personen"

        # HTMX-Suche nach Seed-Client
        page.fill("input[name='q']", "Blitz")
        page.wait_for_load_state("domcontentloaded")
        # Auf das konkrete Ergebnis warten, statt blind auf den Debounce.
        result = page.locator("a:has-text('Blitz-08')").first
        result.wait_for(state="visible", timeout=5000)
        assert result.is_visible()

    def test_client_detail_event_timeline(self, authenticated_page, base_url):
        """Client-Detail → Event-Chronik sichtbar."""
        page = authenticated_page
        page.goto(f"{base_url}/clients/?q=Stern-42")
        page.wait_for_load_state("domcontentloaded")

        page.click("a:has-text('Stern-42')")
        page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))

        assert page.locator("h1").inner_text() == "Stern-42"
        assert page.locator("text=Mit Zusatzangaben").first.is_visible()
        assert page.locator("text=Kontakt-Chronik").is_visible()

    def test_client_create_pseudonym_uniqueness(self, authenticated_page, base_url):
        """Client erstellen → Pseudonym-Uniqueness-Validierung."""
        page = authenticated_page
        page.goto(f"{base_url}/clients/new/")

        assert page.locator("h1").inner_text() == "Person anlegen"

        # Doppeltes Pseudonym
        page.fill("input[name='pseudonym']", "Stern-42")
        page.click("button:has-text('Person anlegen')")
        page.wait_for_load_state("domcontentloaded")

        assert page.locator("text=existiert bereits").is_visible()

        # Neues Pseudonym → Erfolg
        unique_name = f"E2E-{uuid.uuid4().hex[:6]}"
        page.fill("input[name='pseudonym']", unique_name)
        page.click("button:has-text('Person anlegen')")
        page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))
        assert page.locator("h1").inner_text() == unique_name


class TestSuche:
    """Suche → Ergebnisse für Pseudonym und Event-Daten."""

    def test_search_finds_client_and_events(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/search/?q=Stern")

        # Personen gefunden
        assert page.locator("h2:has-text('Personen')").is_visible()
        assert page.locator("#search-results a:has-text('Stern-42')").first.is_visible()

        # Events der Person gefunden
        assert page.locator("h2:has-text('Ereignisse')").is_visible()

    def test_search_no_results(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/search/?q=Gibtsgarantiertnicht12345")

        assert page.locator("text=Keine Ergebnisse").is_visible()

    def test_search_empty_state(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/search/")

        assert page.locator("text=Suchbegriff eingeben").is_visible()


class TestClientListFilter:
    """Klientel-Liste: Filterung nach Stage und Altersgruppe."""

    @staticmethod
    def _open_list(page, base_url, query):
        """Opens the filtered list and waits for the list page itself (not an error page)."""
        page.goto(f"{base_url}/clients/?{query}")
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator("#main-content h1:has-text('Personen')")).to_be_visible()

    def test_filter_by_stage_qualified(self, authenticated_page, base_url):
        """Filter nach Stage 'qualified' zeigt nur qualifizierte Klientel."""
        page = authenticated_page

        # Qualified clients are listed. The list is paginated (25 per page, sorted by
        # pseudonym), so each seed record is found through a search on its own pseudonym
        # instead of relying on its position on page 1.
        for pseudonym in ("Stern-42", "Wolke-17"):
            self._open_list(page, base_url, f"stage=qualified&q={pseudonym}")
            assert page.locator(f"a:has-text('{pseudonym}')").first.is_visible()

        # Identified clients are not listed: the search alone would find Blitz-08, so only
        # the stage filter can keep it out of the result.
        self._open_list(page, base_url, "stage=qualified&q=Blitz-08")
        assert page.locator("a:has-text('Blitz-08')").count() == 0

    def test_filter_by_stage_identified(self, authenticated_page, base_url):
        """Filter nach Stage 'identified' zeigt nur identifizierte Klientel."""
        page = authenticated_page

        # Identified clients are listed (found through a search, see above).
        for pseudonym in ("Blitz-08", "Regen-55"):
            self._open_list(page, base_url, f"stage=identified&q={pseudonym}")
            assert page.locator(f"a:has-text('{pseudonym}')").first.is_visible()

        # Qualified clients are not listed: the search alone would find Stern-42, so only
        # the stage filter can keep it out of the result.
        self._open_list(page, base_url, "stage=identified&q=Stern-42")
        assert page.locator("a:has-text('Stern-42')").count() == 0

    def test_no_stage_filter_shows_all(self, authenticated_page, base_url):
        """Ohne Stage-Filter werden Klientel beider Stufen gefunden."""
        page = authenticated_page

        # Qualifizierte via Suche erreichbar
        page.goto(f"{base_url}/clients/?q=Stern")
        page.wait_for_load_state("domcontentloaded")
        assert page.locator("a:has-text('Stern-42')").first.is_visible()

        # Identifizierte via Suche erreichbar
        page.goto(f"{base_url}/clients/?q=Blitz")
        page.wait_for_load_state("domcontentloaded")
        assert page.locator("a:has-text('Blitz-08')").first.is_visible()

    def test_filter_by_age_cluster(self, authenticated_page, base_url):
        """Filter nach Altersgruppe per URL-Parameter."""
        page = authenticated_page

        # Blitz-08 is under 18 (found through a search, the list is paginated)
        self._open_list(page, base_url, "age=u18&q=Blitz-08")
        assert page.locator("a:has-text('Blitz-08')").first.is_visible()

        # Stern-42 is in the 18-26 cluster and not listed: the search alone would find it, so only the
        # age filter can keep it out of the result.
        self._open_list(page, base_url, "age=u18&q=Stern-42")
        assert page.locator("a:has-text('Stern-42')").count() == 0
