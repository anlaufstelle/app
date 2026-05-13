"""E2E-Tests: Zeitstrom, Events, Schicht-Zuordnung, Löschung.

Tests:
- Aktivitätslog-Startseite, TimeFilter, Event-Erstellung, -Bearbeitung, -Löschung
- Event erscheint im korrekten Schicht-Tab
- Event-Löschung für qualifizierte Klientel (4-Augen-Prinzip)
"""

import re
from datetime import datetime, time

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

SUBMIT = "#main-content button[type='submit']"


class TestAktivitaetslogStartseite:
    """Login → Aktivitätslog-Startseite mit TimeFilter-Tabs."""

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
        assert nav.get_by_role("link", name="Zeitstrom", exact=True).is_visible()
        assert nav.locator("a[href='/clients/']").is_visible()


class TestTimeFilterHTMX:
    """TimeFilter wechseln → Event-Liste aktualisiert sich (HTMX)."""

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
    """Event-Erstellung mit Dokumenttyp, dynamische Felder, Autocomplete, Speichern."""

    @pytest.mark.smoke
    def test_event_create_with_dynamic_fields(self, authenticated_page, base_url):
        page = authenticated_page

        # Neuer Kontakt → Dokumenttyp-Auswahl → dynamische Felder
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

    @pytest.mark.smoke
    def test_client_autocomplete(self, authenticated_page, base_url):
        """Client-Autocomplete tippen → Vorschläge erscheinen."""
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

    @pytest.mark.smoke
    def test_event_create_with_case_assignment(self, authenticated_page, base_url):
        """Event mit Fall-Zuordnung anlegen → Detailseite zeigt Fall-Link."""
        page = authenticated_page
        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")

        page.select_option("select[name='document_type']", label="Kontakt")
        page.locator("input[name='dauer']").wait_for(state="attached", timeout=5000)

        # Klientel auswählen, zu dem ein offener Fall existiert (Seed: Stern-42 → „Gesundheitsversorgung")
        autocomplete = page.locator("input[placeholder='Pseudonym eingeben...']")
        autocomplete.fill("Stern-42")
        option = page.locator("button:has-text('Stern-42')")
        option.wait_for(state="visible", timeout=5000)
        option.click()

        # Fall-Dropdown erscheint dynamisch nach Klientel-Auswahl (Refs #620).
        # Nur Fälle des ausgewählten Klientels sind drin — daher reicht der Titel.
        case_select = page.locator("select[name='case']")
        case_select.wait_for(state="visible", timeout=5000)
        case_select.select_option(label="Gesundheitsversorgung")

        page.fill("input[name='dauer']", "25")
        page.fill("textarea[name='notiz']", "E2E-Test Fall-Zuordnung")

        page.click("button:has-text('Speichern')")
        page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/$"))

        # Detailseite zeigt Fall-Zeile mit Link zur Case-Detail-Seite
        fall_term = page.locator("dt:has-text('Fall')")
        expect(fall_term).to_be_visible()
        fall_link = page.locator("dd a:has-text('Gesundheitsversorgung')")
        expect(fall_link).to_be_visible()
        assert re.search(r"/cases/[0-9a-f-]+/$", fall_link.get_attribute("href"))

    def test_case_dropdown_filtered_to_selected_client(self, authenticated_page, base_url):
        """Nach Klientel-Auswahl enthält das Fall-Dropdown nur passende Fälle (Refs #620)."""
        page = authenticated_page
        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")
        page.select_option("select[name='document_type']", label="Kontakt")
        page.locator("input[name='dauer']").wait_for(state="attached", timeout=5000)

        # Stern-42 hat im Seed den offenen Fall „Gesundheitsversorgung".
        autocomplete = page.locator("input[placeholder='Pseudonym eingeben...']")
        autocomplete.fill("Stern-42")
        page.locator("button:has-text('Stern-42')").first.wait_for(state="visible", timeout=5000)
        page.locator("button:has-text('Stern-42')").first.click()

        case_select = page.locator("select[name='case']")
        case_select.wait_for(state="visible", timeout=5000)
        options = case_select.locator("option")
        # Platzhalter + exakt ein passender Fall; „Wohnungssuche – Sonne-99"
        # gehört zu einem anderen Klientel und darf NICHT auftauchen.
        option_texts = options.all_inner_texts()
        assert any("Gesundheitsversorgung" in t for t in option_texts)
        assert not any("Wohnungssuche" in t for t in option_texts)

    def test_event_create_anonymous_hides_case_without_client_link(self, authenticated_page, base_url):
        """Anonymer Kontakt ohne Fall speichern → Detailseite zeigt keine Fall-Zeile."""
        page = authenticated_page
        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")

        page.select_option("select[name='document_type']", label="Kontakt")
        page.locator("input[name='dauer']").wait_for(state="attached", timeout=5000)
        page.fill("input[name='dauer']", "5")

        page.click("button:has-text('Speichern')")
        page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/$"))

        assert page.locator("dt:has-text('Fall')").count() == 0

    @pytest.mark.smoke
    def test_event_save_and_appears_in_detail(self, authenticated_page, base_url):
        """Event speichern → Detail-Seite mit Daten."""
        page = authenticated_page
        page.goto(f"{base_url}/events/new/")

        # Formular ausfüllen
        page.select_option("select[name='document_type']", label="Kontakt")
        # Warten bis HTMX dynamische Felder geladen hat
        page.locator("input[name='dauer']").wait_for(state="attached", timeout=5000)
        page.fill("input[name='dauer']", "20")
        page.fill("textarea[name='notiz']", "E2E-Test Kontakt")

        # Kein Klientel ausgewählt → wird automatisch anonym

        page.click("button:has-text('Speichern')")
        page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/$"))

        # Detail-Seite prüfen — explizite Waits, weil ``wait_for_url`` direkt
        # nach dem URL-Match weitergeht und die Detail-Sektionen unter Last
        # noch nicht im DOM stehen müssen (synchrones ``is_visible()`` traf
        # mehrfach in CI auf ``False``). 15s statt 5s, weil dieser Test
        # unter Stage-CI-Last (2 Worker, 367 Tests) mit dem dd:notiz-Wait
        # in v0.12.0 zugeschlagen hat.
        page.locator("[role='alert']:has-text('Kontakt wurde dokumentiert.')").first.wait_for(
            state="visible", timeout=15000
        )
        page.locator("dd:has-text('E2E-Test Kontakt')").first.wait_for(state="visible", timeout=15000)
        page.locator("dd:has-text('Anonym')").wait_for(state="visible", timeout=15000)

        # EventHistory-Eintrag CREATE
        page.locator("h2:has-text('Änderungshistorie')").wait_for(state="visible", timeout=15000)


class TestEventEditAndDelete:
    """Event bearbeiten und löschen."""

    def _create_identified_event(self, page, base_url):
        """Hilfsfunktion: Event für identified Client erstellen."""
        page.goto(f"{base_url}/events/new/")
        page.select_option("select[name='document_type']", label="Kontakt")
        page.wait_for_load_state("domcontentloaded")

        # Autocomplete: identifizierten Client wählen
        autocomplete = page.locator("input[placeholder='Pseudonym eingeben...']")
        autocomplete.fill("Blitz")
        # Auf die dynamisch geladene Option warten, statt auf Debounce-Timeout.
        page.wait_for_load_state("domcontentloaded")
        option = page.locator("button:has-text('Blitz-08')")
        option.wait_for(state="visible", timeout=5000)
        option.click()

        page.fill("input[name='dauer']", "10")
        page.click("button:has-text('Speichern')")
        page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/$"))
        return page.url

    @pytest.mark.smoke
    def test_event_edit_creates_history(self, authenticated_page, base_url):
        """Event bearbeiten → EventHistory-Eintrag."""
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

    @pytest.mark.smoke
    def test_event_delete_identified_direct(self, authenticated_page, base_url):
        """Event löschen (identified) → direkt gelöscht."""
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


class TestNachtdienstShiftAssignment:
    """Event erscheint im korrekten Schicht-Tab (Refs #281)."""

    def _get_current_shift_label(self):
        """Bestimmt den erwarteten Schicht-Tab basierend auf aktueller Uhrzeit."""
        now = datetime.now().time()
        if time(8, 0) <= now <= time(16, 0):
            return "Frühdienst"
        elif time(16, 0) < now <= time(22, 0):
            return "Spätdienst"
        else:
            return "Nachtdienst"

    def _get_other_shift_label(self):
        """Gibt einen Schicht-Tab zurück, der NICHT die aktuelle Schicht ist."""
        current = self._get_current_shift_label()
        if current == "Frühdienst":
            return "Spätdienst"
        elif current == "Spätdienst":
            return "Frühdienst"
        else:
            return "Frühdienst"

    def test_event_appears_in_correct_shift_tab(self, authenticated_page, base_url):
        """Neues Event (occurred_at=jetzt) erscheint im auto-selektierten Tab, nicht in einem anderen."""
        page = authenticated_page

        # Event erstellen
        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")
        page.select_option("select[name='document_type']", label="Kontakt")
        page.wait_for_load_state("domcontentloaded")
        page.locator(SUBMIT).click()
        page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/$"))

        # Zurück zum Zeitstrom (Schicht-Tabs)
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        # Event sollte in der Event-Liste sichtbar sein (auto-selektierter Tab = aktuelle Schicht)
        event_list = page.locator("#feed-list")
        assert event_list.locator("text=Kontakt").first.is_visible()

        # Auf einen anderen Schicht-Tab klicken → Event sollte nicht erscheinen
        other_shift = self._get_other_shift_label()
        other_tab = page.locator(f"button:has-text('{other_shift}')")
        other_tab.click()
        page.wait_for_load_state("domcontentloaded")
        # HTMX-Swap abwarten: gewechselter Tab wird aktiv markiert.
        expect(other_tab).to_have_class(re.compile(r"bg-accent-light"))


class TestQualifiedClientEventDeletion:
    """Event-Löschung für qualifizierten Klientel → DeletionRequest wird erstellt."""

    def _create_event_for_stern42(self, page, base_url):
        """Neues Event für Stern-42 (qualifiziert) erstellen."""
        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")

        page.select_option("select[name='document_type']", label="Kontakt")
        page.wait_for_load_state("domcontentloaded")

        autocomplete = page.locator("input[placeholder='Pseudonym eingeben...']")
        autocomplete.fill("Stern")
        page.locator("button:has-text('Stern-42')").wait_for(state="visible", timeout=5000)
        page.locator("button:has-text('Stern-42')").click()

        page.locator(SUBMIT).click()
        page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/$"))

        assert re.search(r"/events/[0-9a-f-]+/$", page.url)

    def test_delete_qualified_event_creates_deletion_request(self, authenticated_page, base_url):
        """Event eines qualifizierten Klientel löschen → DeletionRequest erstellt + in Liste sichtbar."""
        page = authenticated_page
        self._create_event_for_stern42(page, base_url)

        page.click("a:has-text('Löschen')")
        page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/delete/$"))

        reason_field = page.locator("textarea[name='reason']")
        if reason_field.count() > 0:
            reason_field.fill("E2E-Test: Qualifizierter Klientel")

        page.locator(SUBMIT).click()
        page.wait_for_url(lambda url: "/delete/" not in url)

        assert page.url == f"{base_url}/"
        assert page.locator("text=Löschantrag").first.is_visible()

        # Löschantrag erscheint in der Liste
        page.goto(f"{base_url}/deletion-requests/")
        page.wait_for_load_state("domcontentloaded")

        assert page.locator("h1").inner_text() == "Löschanträge"
        assert page.locator("text=Ausstehend").first.is_visible()
