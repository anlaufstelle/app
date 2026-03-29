"""E2E-Tests fuer Kontaktstufen-Dropdown im Admin.

Verifiziert, dass min_contact_stage als Dropdown (select) statt Freitext (input)
im Django-Admin angezeigt wird und Validierung korrekt funktioniert.
"""

import pytest

pytestmark = pytest.mark.e2e


class TestContactStageDropdown:
    """Admin-Seite zeigt min_contact_stage als Dropdown."""

    def test_min_contact_stage_is_select(self, authenticated_page, admin_url):
        """min_contact_stage wird als <select>-Element dargestellt, nicht als <input>."""
        page = authenticated_page
        page.goto(f"{admin_url}/core/documenttype/add/", wait_until="domcontentloaded")

        select = page.locator("select#id_min_contact_stage")
        assert select.count() == 1, "min_contact_stage sollte ein <select>-Element sein"

        # Kein <input> mit diesem Namen
        text_input = page.locator("input#id_min_contact_stage")
        assert text_input.count() == 0, "min_contact_stage sollte kein <input>-Element sein"

    def test_dropdown_has_expected_options(self, authenticated_page, admin_url):
        """Das Dropdown enthaelt die erwarteten Kontaktstufen-Optionen."""
        page = authenticated_page
        page.goto(f"{admin_url}/core/documenttype/add/", wait_until="domcontentloaded")

        select = page.locator("select#id_min_contact_stage")
        options = select.locator("option")

        # Erwartete Werte: leere Option + identified + qualified
        option_values = [options.nth(i).get_attribute("value") for i in range(options.count())]
        assert "" in option_values, "Leere Option (keine Auswahl) fehlt"
        assert "identified" in option_values, "Option 'identified' fehlt"
        assert "qualified" in option_values, "Option 'qualified' fehlt"

    def test_dropdown_selection_saves_correctly(self, authenticated_page, admin_url):
        """Ein ausgewaehlter Wert wird korrekt gespeichert."""
        page = authenticated_page
        page.goto(f"{admin_url}/core/documenttype/add/", wait_until="domcontentloaded")

        # Formular ausfuellen
        page.fill("input#id_name", "E2E-Test-Typ")
        page.select_option("select#id_min_contact_stage", "qualified")

        # Facility auswaehlen (Pflichtfeld)
        facility_select = page.locator("select#id_facility")
        facility_options = facility_select.locator("option")
        for i in range(facility_options.count()):
            val = facility_options.nth(i).get_attribute("value")
            if val:
                page.select_option("select#id_facility", val)
                break

        page.locator("button[name='_save']").click()
        page.wait_for_url(lambda url: "/add/" not in url, timeout=10000)

        # Erfolg pruefen: Keine Fehlermeldung, Weiterleitung zur Liste
        assert "/add/" not in page.url, "Formular wurde nicht erfolgreich gespeichert"
