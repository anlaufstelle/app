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

pytestmark = pytest.mark.e2e


class TestClientManagement:
    """Klientel-Liste, Detail, Erstellen."""

    def test_client_list_search(self, authenticated_page, base_url):
        """Klientel-Liste → Suche funktioniert."""
        page = authenticated_page
        page.goto(f"{base_url}/clients/")

        assert page.locator("h1").inner_text() == "Klientel"

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
        assert page.locator("text=Qualifiziert").first.is_visible()
        assert page.locator("text=Kontakt-Chronik").is_visible()

    def test_client_create_pseudonym_uniqueness(self, authenticated_page, base_url):
        """Client erstellen → Pseudonym-Uniqueness-Validierung."""
        page = authenticated_page
        page.goto(f"{base_url}/clients/new/")

        assert page.locator("h1").inner_text() == "Neues Klientel"

        # Doppeltes Pseudonym
        page.fill("input[name='pseudonym']", "Stern-42")
        page.click("button:has-text('Klientel erstellen')")
        page.wait_for_load_state("domcontentloaded")

        assert page.locator("text=existiert bereits").is_visible()

        # Neues Pseudonym → Erfolg
        unique_name = f"E2E-{uuid.uuid4().hex[:6]}"
        page.fill("input[name='pseudonym']", unique_name)
        page.click("button:has-text('Klientel erstellen')")
        page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))
        assert page.locator("h1").inner_text() == unique_name


class TestSuche:
    """Suche → Ergebnisse für Pseudonym und Event-Daten."""

    def test_search_finds_client_and_events(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/search/?q=Stern")

        # Klientel gefunden
        assert page.locator("h2:has-text('Klientel')").is_visible()
        assert page.locator("#search-results a:has-text('Stern-42')").first.is_visible()

        # Events des Klientel gefunden
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

    def test_filter_by_stage_qualified(self, authenticated_page, base_url):
        """Filter nach Stage 'qualified' zeigt nur qualifizierte Klientel."""
        page = authenticated_page
        page.goto(f"{base_url}/clients/?stage=qualified")
        page.wait_for_load_state("domcontentloaded")

        # Qualifizierte Klientel sichtbar
        assert page.locator("a:has-text('Stern-42')").first.is_visible()
        assert page.locator("a:has-text('Wolke-17')").first.is_visible()

        # Identifizierte Klientel nicht sichtbar
        assert page.locator("a:has-text('Blitz-08')").count() == 0

    def test_filter_by_stage_identified(self, authenticated_page, base_url):
        """Filter nach Stage 'identified' zeigt nur identifizierte Klientel."""
        page = authenticated_page
        page.goto(f"{base_url}/clients/?stage=identified")
        page.wait_for_load_state("domcontentloaded")

        # Identifizierte Klientel sichtbar
        assert page.locator("a:has-text('Blitz-08')").first.is_visible()
        assert page.locator("a:has-text('Regen-55')").first.is_visible()

        # Qualifizierte Klientel nicht sichtbar
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
        page.goto(f"{base_url}/clients/?age=u18")
        page.wait_for_load_state("domcontentloaded")

        # Blitz-08 ist U18
        assert page.locator("a:has-text('Blitz-08')").first.is_visible()
        # Stern-42 ist AGE_18_26 → nicht sichtbar
        assert page.locator("a:has-text('Stern-42')").count() == 0
