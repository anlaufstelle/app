"""E2E-Tests für Client-Bearbeitung: Pseudonym/Stage ändern, Berechtigungen."""

import re
import uuid

import pytest

pytestmark = pytest.mark.e2e


class TestClientEdit:
    """Client-Edit-Formular: Laden, Bearbeiten, Speichern."""

    def _create_test_client(self, page, base_url, pseudonym=None):
        """Eigenen Test-Client erstellen, um Seed-Daten nicht zu mutieren."""
        pseudonym = pseudonym or f"E2E-Client-{uuid.uuid4().hex[:6]}"
        page.goto(f"{base_url}/clients/new/")
        page.fill('input[name="pseudonym"]', pseudonym)
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))
        return pseudonym

    def _navigate_to_client_edit(self, page, base_url, pseudonym):
        """Navigiert zum Edit-Formular eines bestimmten Clients."""
        page.goto(f"{base_url}/clients/?q={pseudonym}")
        page.wait_for_load_state("domcontentloaded")
        page.locator(f"a:has-text('{pseudonym}')").first.click()
        page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/"))
        page.click("a:has-text('Bearbeiten')")
        page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/edit/"))

    @pytest.mark.smoke
    def test_edit_form_loads_with_existing_data(self, authenticated_page, base_url):
        """Edit-Formular zeigt aktuelles Pseudonym vorausgefüllt."""
        page = authenticated_page
        pseudonym = self._create_test_client(page, base_url)
        self._navigate_to_client_edit(page, base_url, pseudonym)

        # Pseudonym-Feld ist nicht leer (vorausgefüllt mit bestehendem Wert)
        pseudonym_value = page.locator('input[name="pseudonym"]').input_value()
        assert pseudonym_value == pseudonym, "Pseudonym-Feld sollte vorausgefüllt sein"

        # Contact-Stage Dropdown ist sichtbar
        assert page.locator('select[name="contact_stage"]').is_visible()

    @pytest.mark.smoke
    def test_edit_pseudonym_saves_and_redirects(self, authenticated_page, base_url):
        """Pseudonym ändern → Speichern → Detail zeigt neuen Wert."""
        page = authenticated_page
        pseudonym = self._create_test_client(page, base_url)
        self._navigate_to_client_edit(page, base_url, pseudonym)

        new_pseudonym = f"E2E-Edit-{uuid.uuid4().hex[:6]}"
        page.fill('input[name="pseudonym"]', new_pseudonym)
        page.locator("#main-content button[type='submit']").click()

        # Redirect zur Detail-Seite
        page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))

        # Detail-Seite zeigt das neue Pseudonym im h1
        assert page.locator("h1").inner_text() == new_pseudonym

    def test_edit_contact_stage_saves(self, authenticated_page, base_url):
        """Contact-Stage ändern → Speichern → Detail zeigt neue Stage."""
        page = authenticated_page
        pseudonym = self._create_test_client(page, base_url)
        self._navigate_to_client_edit(page, base_url, pseudonym)

        # Aktuelle Stage merken, dann wechseln
        current_stage = page.locator('select[name="contact_stage"]').input_value()
        new_stage = "qualified" if current_stage != "qualified" else "identified"

        page.select_option('select[name="contact_stage"]', value=new_stage)
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))

        # Detail-Seite zeigt die neue Stage als Badge
        expected_text = "Qualifiziert" if new_stage == "qualified" else "Identifiziert"
        assert page.locator(f"span.rounded-full:has-text('{expected_text}')").is_visible()


class TestClientEditPermissions:
    """Berechtigungsprüfungen für Client-Edit."""

    @pytest.mark.smoke
    def test_assistant_cannot_edit_client(self, assistant_page, authenticated_page, base_url):
        """Assistenz bekommt 403 auf Client-Edit-URL."""
        # Als Admin einen Seed-Client (Sonne-99) für den Test nutzen
        admin = authenticated_page
        admin.goto(f"{base_url}/clients/?q=Sonne-99")
        admin.wait_for_load_state("domcontentloaded")
        admin.locator("a:has-text('Sonne-99')").first.click()
        admin.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/"))
        client_pk = re.search(r"/clients/([0-9a-f-]+)/", admin.url).group(1)

        # Als Assistenz auf Edit zugreifen → 403
        resp = assistant_page.goto(f"{base_url}/clients/{client_pk}/edit/")
        assert resp.status == 403
