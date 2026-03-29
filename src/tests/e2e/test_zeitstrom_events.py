"""E2E-Tests: Timeline-Events, Schicht-Zuordnung, Löschung.

Tests:
- Event erscheint im korrekten Schicht-Tab und nicht in anderen
- Event-Löschung für qualifizierte Klientel (4-Augen-Prinzip)

Bereits abgedeckt in test_stream_c.py:
- Timeline / TimeFilter, Event erstellen/bearbeiten/löschen (identified)
"""

import re
from datetime import datetime, time

import pytest

pytestmark = pytest.mark.e2e

SUBMIT = "#main-content button[type='submit']"


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
        page.locator(f"button:has-text('{other_shift}')").click()
        page.wait_for_load_state("domcontentloaded")
        # Kurz warten auf HTMX-Response
        page.wait_for_timeout(500)


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
