"""E2E-Tests: Anonym-Checkbox bei min_contact_stage deaktiviert.

Verifiziert, dass die Anonym-Checkbox im Event-Formular deaktiviert wird,
wenn der gewählte Dokumentationstyp eine Mindest-Kontaktstufe erfordert.
Außerdem: Kontakt ohne Klientel wird automatisch als anonym gespeichert.
Refs #394, #472
"""

import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


class TestAnonymousMinContactStageGate:
    """Anonym-Checkbox wird bei min_contact_stage deaktiviert."""

    def test_checkbox_disabled_for_min_stage_doctype(self, authenticated_page, base_url):
        """Beratungsgespräch hat min_contact_stage → Checkbox deaktiviert."""
        page = authenticated_page
        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")

        page.select_option("select[name='document_type']", label="Beratungsgespräch")

        checkbox = page.locator("input[name='is_anonymous']")
        checkbox.wait_for(state="visible")
        assert checkbox.is_disabled()

        hint = page.locator("text=Mindest-Kontaktstufe")
        assert hint.is_visible()

    def test_checkbox_enabled_for_no_min_stage_doctype(self, authenticated_page, base_url):
        """Kontakt hat kein min_contact_stage → Checkbox aktiv."""
        page = authenticated_page
        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")

        page.select_option("select[name='document_type']", label="Kontakt")

        checkbox = page.locator("input[name='is_anonymous']")
        checkbox.wait_for(state="visible")
        assert not checkbox.is_disabled()

    def test_checkbox_toggles_on_doctype_change(self, authenticated_page, base_url):
        """Checkbox-Status wechselt bei Dokumentationstyp-Änderung."""
        page = authenticated_page
        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")

        checkbox = page.locator("input[name='is_anonymous']")

        # Kontakt (kein min_contact_stage) → aktiv
        page.select_option("select[name='document_type']", label="Kontakt")
        checkbox.wait_for(state="visible")
        assert not checkbox.is_disabled()

        # Beratungsgespräch (min_contact_stage=qualified) → deaktiviert
        page.select_option("select[name='document_type']", label="Beratungsgespräch")
        checkbox.wait_for(state="visible")
        assert checkbox.is_disabled()

        # Zurück zu Kontakt → wieder aktiv
        page.select_option("select[name='document_type']", label="Kontakt")
        checkbox.wait_for(state="visible")
        assert not checkbox.is_disabled()

    def test_submit_without_client_auto_sets_anonymous(self, authenticated_page, base_url):
        """Kontakt ohne Klientel wird automatisch als anonym gespeichert. Refs #472"""
        page = authenticated_page
        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")

        # Kontakt (kein min_contact_stage) ist default — kein Klientel auswählen
        page.select_option("select[name='document_type']", label="Kontakt")

        # Checkbox ist NICHT gesetzt
        checkbox = page.locator("input[name='is_anonymous']")
        assert not checkbox.is_checked()

        # Absenden
        page.locator("#event-submit-btn").click()
        page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/$"), timeout=15000)

        # Event-Detailseite zeigt "Anonym"
        expect(page.locator("text=Anonym")).to_be_visible()
