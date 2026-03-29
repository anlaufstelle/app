"""E2E-Tests für WorkItem-Bearbeitung: Titel/Priorität ändern, Berechtigungen."""

import re
import uuid

import pytest

pytestmark = pytest.mark.e2e


class TestWorkItemEdit:
    """WorkItem-Edit-Formular: Laden, Bearbeiten, Speichern."""

    def _navigate_to_first_workitem_edit(self, page, base_url):
        """Hilfsfunktion: Navigiert zum Edit-Formular des ersten WorkItems."""
        page.goto(f"{base_url}/workitems/")
        page.wait_for_load_state("domcontentloaded")

        # Erste Aufgabe in der Inbox anklicken → Detail
        page.locator("#inbox-content a[href*='/workitems/']").first.click()
        page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/"))

        # "Bearbeiten"-Link klicken
        page.click("a:has-text('Bearbeiten')")
        page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/edit/"))

    def test_edit_form_loads_with_existing_data(self, authenticated_page, base_url):
        """Edit-Formular zeigt aktuellen Titel vorausgefüllt."""
        page = authenticated_page
        self._navigate_to_first_workitem_edit(page, base_url)

        # Titel-Feld ist nicht leer
        title_value = page.locator('input[name="title"]').input_value()
        assert title_value != "", "Titel-Feld sollte vorausgefüllt sein"

        # Priorität-Dropdown ist sichtbar
        assert page.locator('select[name="priority"]').is_visible()

    def test_edit_title_and_priority_saves(self, authenticated_page, base_url):
        """Titel + Priorität ändern → Speichern → Inbox/Detail zeigt neue Werte."""
        page = authenticated_page

        # Erst neuen WorkItem erstellen, um ihn dann zu editieren
        unique_title = f"E2E-WI-{uuid.uuid4().hex[:6]}"
        page.goto(f"{base_url}/workitems/new/")
        page.fill('input[name="title"]', unique_title)
        page.select_option('select[name="item_type"]', index=1)
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(f"{base_url}/workitems/")

        # WorkItem in Inbox finden und zur Detail-Seite navigieren
        page.locator(f"a:has-text('{unique_title}')").click()
        page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/"))

        # Bearbeiten
        page.click("a:has-text('Bearbeiten')")
        page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/edit/"))

        updated_title = f"E2E-Updated-{uuid.uuid4().hex[:6]}"
        page.fill('input[name="title"]', updated_title)
        page.select_option('select[name="priority"]', value="important")
        page.locator("#main-content button[type='submit']").click()

        # Redirect zur Inbox
        page.wait_for_url(f"{base_url}/workitems/")
        page.wait_for_load_state("domcontentloaded")

        # Aktualisierter Titel in Inbox sichtbar
        assert page.locator(f"text={updated_title}").is_visible()

    def test_edit_due_date_saves(self, authenticated_page, base_url):
        """Fristdatum setzen → Speichern → Detail zeigt Datum."""
        page = authenticated_page

        # Neuen WorkItem erstellen
        unique_title = f"E2E-Due-{uuid.uuid4().hex[:6]}"
        page.goto(f"{base_url}/workitems/new/")
        page.fill('input[name="title"]', unique_title)
        page.select_option('select[name="item_type"]', index=1)
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(f"{base_url}/workitems/")

        # Zur Detail-Seite → Bearbeiten
        page.locator(f"a:has-text('{unique_title}')").click()
        page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/"))
        page.click("a:has-text('Bearbeiten')")
        page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/edit/"))

        # Fristdatum setzen
        page.fill('input[name="due_date"]', "2026-12-31")
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(f"{base_url}/workitems/")

        # Detail-Seite prüfen: Fristdatum angezeigt
        page.locator(f"a:has-text('{unique_title}')").click()
        page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/"))
        assert page.locator("text=31.12.2026").first.is_visible()


class TestWorkItemEditPermissions:
    """Berechtigungsprüfungen für WorkItem-Edit."""

    def test_assistant_cannot_edit_workitem(self, assistant_page, authenticated_page, base_url):
        """Assistenz bekommt 403 auf WorkItem-Edit-URL."""
        # Als Admin eine gültige WorkItem-UUID holen
        admin = authenticated_page
        admin.goto(f"{base_url}/workitems/")
        admin.wait_for_load_state("domcontentloaded")
        admin.locator("#inbox-content a[href*='/workitems/']").first.click()
        admin.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/"))
        wi_pk = re.search(r"/workitems/([0-9a-f-]+)/", admin.url).group(1)

        # Als Assistenz auf Edit zugreifen → 403
        resp = assistant_page.goto(f"{base_url}/workitems/{wi_pk}/edit/")
        assert resp.status == 403
