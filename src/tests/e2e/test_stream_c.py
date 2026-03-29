"""E2E-Tests für Stream C: Aktivitätslog, Clients, Events, Suche.

Automatisiert die 11 Verifikationsschritte aus dem Stream-C-Plan.
"""

import re

import pytest

pytestmark = pytest.mark.e2e


class TestAktivitaetslogStartseite:
    """1. Login → Aktivitätslog-Startseite mit TimeFilter-Tabs."""

    def test_login_redirects_to_dashboard(self, authenticated_page):
        page = authenticated_page
        assert page.url.endswith("/")
        assert page.locator("h1").inner_text() == "Zeitstrom"

    def test_time_filter_tabs_visible(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")
        tabs = page.locator("[data-testid='time-filter-tabs']")
        assert tabs.locator("button:has-text('Frühdienst')").is_visible()
        assert tabs.locator("button:has-text('Spätdienst')").is_visible()
        assert tabs.locator("button:has-text('Nachtdienst')").is_visible()

    def test_navigation_links(self, authenticated_page):
        page = authenticated_page
        nav = page.locator("nav[aria-label='Hauptnavigation']")
        assert nav.locator("text=Zeitstrom").is_visible()
        assert nav.locator("a[href='/clients/']").is_visible()


class TestTimeFilterHTMX:
    """2. TimeFilter wechseln → Event-Liste aktualisiert sich (HTMX)."""

    def test_time_filter_switch_updates_event_list(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")
        event_list = page.locator("#feed-list")
        assert event_list.is_visible()

        # Klick auf Spätdienst-Tab
        page.locator("[data-testid='time-filter-tabs'] button:has-text('Spätdienst')").click()
        # HTMX sollte den Inhalt aktualisieren (Warten auf Netzwerk-Idle)
        page.wait_for_load_state("domcontentloaded")
        assert event_list.is_visible()


class TestEventErstellung:
    """3 + 4 + 5. Event-Erstellung mit Dokumenttyp, dynamische Felder, Autocomplete, Speichern."""

    def test_event_create_with_dynamic_fields(self, authenticated_page, base_url):
        page = authenticated_page

        # 3. Neuer Kontakt → Dokumenttyp-Auswahl → dynamische Felder
        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")
        assert page.locator("h1").inner_text() == "Neuer Kontakt"

        # Dokumenttyp wählen → dynamische Felder laden (HTMX)
        page.select_option("select[name='document_type']", label="Kontakt")

        # HTMX lädt Felder asynchron — auf konkretes Element warten
        page.locator("label:has-text('Dauer')").wait_for(state="visible", timeout=10000)

        # Dynamische Felder sollten sichtbar sein
        assert page.locator("label:has-text('Dauer')").is_visible()
        assert page.locator("label:has-text('Notiz')").is_visible()

    def test_client_autocomplete(self, authenticated_page, base_url):
        """4. Client-Autocomplete tippen → Vorschläge erscheinen."""
        page = authenticated_page
        page.goto(f"{base_url}/events/new/")

        page.select_option("select[name='document_type']", label="Kontakt")
        page.wait_for_load_state("domcontentloaded")

        # Autocomplete tippen
        autocomplete = page.locator("input[placeholder='Pseudonym eingeben...']")
        autocomplete.fill("Stern")
        # Warten auf Alpine.js Debounce (200ms) + Fetch
        suggestion = page.locator("button:has-text('Stern-42')")
        suggestion.wait_for(state="visible", timeout=5000)

        # Vorschlag sollte erscheinen
        assert suggestion.is_visible()

        # Auswählen
        page.locator("button:has-text('Stern-42')").click()
        assert autocomplete.input_value() == "Stern-42"

    def test_event_save_and_appears_in_detail(self, authenticated_page, base_url):
        """5. Event speichern → Detail-Seite mit Daten."""
        page = authenticated_page
        page.goto(f"{base_url}/events/new/")

        # Formular ausfüllen
        page.select_option("select[name='document_type']", label="Kontakt")
        page.wait_for_load_state("domcontentloaded")
        page.fill("input[name='dauer']", "20")
        page.fill("textarea[name='notiz']", "E2E-Test Kontakt")

        # Anonym-Checkbox
        page.check("input[name='is_anonymous']")

        page.click("button:has-text('Speichern')")
        page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/$"))

        # Detail-Seite prüfen
        assert page.locator("[role='alert']:has-text('Kontakt wurde dokumentiert.')").first.is_visible()
        assert page.locator("dd:has-text('E2E-Test Kontakt')").first.is_visible()
        assert page.locator("dd:has-text('Anonym')").is_visible()

        # EventHistory-Eintrag CREATE
        assert page.locator("h2:has-text('Änderungshistorie')").is_visible()


class TestClientManagement:
    """6 + 7 + 8. Klientel-Liste, Detail, Erstellen."""

    def test_client_list_search(self, authenticated_page, base_url):
        """6. Klientel-Liste → Suche funktioniert."""
        page = authenticated_page
        page.goto(f"{base_url}/clients/")

        assert page.locator("h1").inner_text() == "Klientel"

        # HTMX-Suche nach Seed-Client
        page.fill("input[name='q']", "Blitz")
        page.wait_for_timeout(500)
        page.wait_for_load_state("domcontentloaded")
        assert page.locator("a:has-text('Blitz-08')").first.is_visible()

    def test_client_detail_event_timeline(self, authenticated_page, base_url):
        """7. Client-Detail → Event-Chronik sichtbar."""
        page = authenticated_page
        page.goto(f"{base_url}/clients/?q=Stern-42")
        page.wait_for_load_state("domcontentloaded")

        page.click("a:has-text('Stern-42')")
        page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))

        assert page.locator("h1").inner_text() == "Stern-42"
        assert page.locator("text=Qualifiziert").first.is_visible()
        assert page.locator("text=Kontakt-Chronik").is_visible()

    def test_client_create_pseudonym_uniqueness(self, authenticated_page, base_url):
        """8. Client erstellen → Pseudonym-Uniqueness-Validierung."""
        page = authenticated_page
        page.goto(f"{base_url}/clients/new/")

        assert page.locator("h1").inner_text() == "Neues Klientel"

        # Doppeltes Pseudonym
        page.fill("input[name='pseudonym']", "Stern-42")
        page.click("button:has-text('Klientel erstellen')")
        page.wait_for_load_state("domcontentloaded")

        assert page.locator("text=existiert bereits").is_visible()

        # Neues Pseudonym → Erfolg
        import uuid

        unique_name = f"E2E-{uuid.uuid4().hex[:6]}"
        page.fill("input[name='pseudonym']", unique_name)
        page.click("button:has-text('Klientel erstellen')")
        page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))
        assert page.locator("h1").inner_text() == unique_name


class TestEventEditAndDelete:
    """9 + 10. Event bearbeiten und löschen."""

    def _create_identified_event(self, page, base_url):
        """Hilfsfunktion: Event für identified Client erstellen."""
        page.goto(f"{base_url}/events/new/")
        page.select_option("select[name='document_type']", label="Kontakt")
        page.wait_for_load_state("domcontentloaded")

        # Autocomplete: identifizierten Client wählen
        autocomplete = page.locator("input[placeholder='Pseudonym eingeben...']")
        autocomplete.fill("Blitz")
        page.wait_for_timeout(500)
        page.wait_for_load_state("domcontentloaded")
        page.locator("button:has-text('Blitz-08')").click()

        page.fill("input[name='dauer']", "10")
        page.click("button:has-text('Speichern')")
        page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/$"))
        return page.url

    def test_event_edit_creates_history(self, authenticated_page, base_url):
        """9. Event bearbeiten → EventHistory-Eintrag."""
        page = authenticated_page
        self._create_identified_event(page, base_url)

        # Bearbeiten
        page.click("a:has-text('Bearbeiten')")
        page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/edit/$"))

        page.fill("input[name='dauer']", "45")
        page.click("button:has-text('Änderungen speichern')")
        page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/$"))

        # History prüfen
        assert page.locator("[role='alert']:has-text('Ereignis wurde aktualisiert.')").first.is_visible()
        assert page.locator("span:has-text('Aktualisiert')").first.is_visible()
        assert page.locator("span:has-text('Erstellt')").first.is_visible()

    def test_event_delete_identified_direct(self, authenticated_page, base_url):
        """10. Event löschen (identified) → direkt gelöscht."""
        page = authenticated_page
        self._create_identified_event(page, base_url)

        # Löschen
        page.click("a:has-text('Löschen')")
        page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/delete/$"))

        # Identified Client → kein 4-Augen, direkter Löschen-Button
        assert page.locator("button:has-text('Endgültig löschen')").is_visible()

        page.click("button:has-text('Endgültig löschen')")
        page.wait_for_url(lambda url: "/events/" not in url)

        assert page.locator("[role='alert']:has-text('Ereignis wurde gelöscht.')").first.is_visible()


class TestSuche:
    """11. Suche → Ergebnisse für Pseudonym und Event-Daten."""

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
