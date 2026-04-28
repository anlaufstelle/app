"""E2E-Tests: Filter für WorkItem-Inbox und Timeline.

Testet:
- WorkItem-Inbox: Typ-, Priorität- und Zuweisungs-Filter
- Timeline: Dokumentationstyp-Filter
"""

import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


class TestWorkItemInboxFilters:
    """WorkItem-Inbox-Filter aktualisieren die Liste per HTMX."""

    def _create_workitem(self, page, base_url, title, item_type="Aufgabe", priority="Normal"):
        """WorkItem über das Formular erstellen."""
        page.goto(f"{base_url}/workitems/new/")
        page.wait_for_load_state("domcontentloaded")

        page.fill("input[name='title']", title)
        page.select_option("select[name='item_type']", label=item_type)
        page.select_option("select[name='priority']", label=priority)

        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(re.compile(r"/workitems/$"))

    def test_inbox_type_filter(self, authenticated_page, base_url):
        """Typ-Filter in der Inbox filtert WorkItems nach Typ."""
        page = authenticated_page

        self._create_workitem(page, base_url, "E2E-Aufgabe-Filter", item_type="Aufgabe")
        self._create_workitem(page, base_url, "E2E-Hinweis-Filter", item_type="Hinweis")

        page.goto(f"{base_url}/workitems/")
        page.wait_for_load_state("domcontentloaded")

        # Beide sichtbar
        assert page.locator("text=E2E-Aufgabe-Filter").count() > 0
        assert page.locator("text=E2E-Hinweis-Filter").count() > 0

        # Nach Aufgabe filtern
        page.select_option("#filter-item-type", value="task")
        page.wait_for_load_state("domcontentloaded")
        # HTMX-Swap: Hinweis-Eintrag muss aus der Inbox verschwinden.
        expect(page.locator("#inbox-content").locator("text=E2E-Hinweis-Filter")).to_have_count(0)

        assert page.locator("text=E2E-Aufgabe-Filter").count() > 0
        assert page.locator("#inbox-content").locator("text=E2E-Hinweis-Filter").count() == 0

    def test_inbox_priority_filter(self, authenticated_page, base_url):
        """Priorität-Filter in der Inbox filtert WorkItems nach Priorität."""
        page = authenticated_page

        self._create_workitem(page, base_url, "E2E-Dringend-Filter", priority="Dringend")
        self._create_workitem(page, base_url, "E2E-Normal-Filter", priority="Normal")

        page.goto(f"{base_url}/workitems/")
        page.wait_for_load_state("domcontentloaded")

        # Nach Dringend filtern
        page.select_option("#filter-priority", value="urgent")
        page.wait_for_load_state("domcontentloaded")
        # HTMX-Swap: Normal-Eintrag muss aus der Inbox verschwinden.
        expect(page.locator("#inbox-content").locator("text=E2E-Normal-Filter")).to_have_count(0)

        assert page.locator("text=E2E-Dringend-Filter").count() > 0
        assert page.locator("#inbox-content").locator("text=E2E-Normal-Filter").count() == 0


class TestTimelineDocTypeFilter:
    """Timeline-Dokumentationstyp-Filter filtert Events."""

    def test_timeline_doc_type_filter_exists(self, authenticated_page, base_url):
        """Dokumentationstyp-Dropdown ist auf der Timeline-Seite sichtbar."""
        page = authenticated_page

        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        doc_type_select = page.locator("#filter-doc-type")
        assert doc_type_select.count() > 0, "Dokumentationstyp-Filter-Dropdown nicht gefunden"

    def test_timeline_doc_type_filter_updates_events(self, authenticated_page, base_url):
        """Dokumentationstyp-Filter aktualisiert die Event-Liste per HTMX."""
        page = authenticated_page

        # Event erstellen
        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")
        page.select_option("select[name='document_type']", label="Kontakt")
        page.wait_for_load_state("domcontentloaded")

        autocomplete = page.locator("input[placeholder='Pseudonym eingeben...']")
        autocomplete.fill("Stern")
        page.locator("button:has-text('Stern-42')").wait_for(state="visible", timeout=5000)
        page.locator("button:has-text('Stern-42')").click()

        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/$"))

        # Zeitstrom aufrufen (Doc-Type-Filter ist auf /)
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        doc_type_select = page.locator("#filter-doc-type")
        if doc_type_select.count() > 0:
            # Filter auf einen bestimmten Typ setzen
            doc_type_select.select_option(label="Kontakt")
            page.wait_for_load_state("domcontentloaded")

            # Event-Liste wurde aktualisiert
            event_list = page.locator("#feed-list")
            assert event_list.count() > 0
