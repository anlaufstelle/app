"""E2E-Tests: Anonymität wird aus fehlender Klientel-Auswahl abgeleitet.

Verifiziert, dass Events ohne Klientel automatisch als anonym gespeichert werden
und dass bei Dokumentationstypen mit Mindest-Kontaktstufe ein Pflichtfeld-Hinweis erscheint.
Refs #394, #472, #486
"""

import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


class TestAnonymousMinContactStageGate:
    """Klientel-Hinweise je nach Dokumentationstyp und automatische Anonymität."""

    def test_required_hint_for_min_stage_doctype(self, authenticated_page, base_url):
        """Beratungsgespräch hat min_contact_stage → Pflichtfeld-Hinweis sichtbar."""
        page = authenticated_page
        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")

        page.select_option("select[name='document_type']", label="Beratungsgespräch")

        hint = page.locator("text=Mindest-Kontaktstufe")
        expect(hint).to_be_visible()

    def test_anonymous_hint_for_no_min_stage_doctype(self, authenticated_page, base_url):
        """Kontakt hat kein min_contact_stage → Anonym-Hinweis sichtbar ohne Client."""
        page = authenticated_page
        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")

        page.select_option("select[name='document_type']", label="Kontakt")

        hint = page.locator("text=anonym gespeichert")
        expect(hint).to_be_visible()

    def test_hints_toggle_on_doctype_change(self, authenticated_page, base_url):
        """Hinweis-Texte wechseln bei Dokumentationstyp-Änderung."""
        page = authenticated_page
        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")

        anon_hint = page.locator("text=anonym gespeichert")
        required_hint = page.locator("text=Mindest-Kontaktstufe")

        # Kontakt (kein min_contact_stage) → Anonym-Hinweis
        page.select_option("select[name='document_type']", label="Kontakt")
        expect(anon_hint).to_be_visible()
        expect(required_hint).to_be_hidden()

        # Beratungsgespräch (min_contact_stage=qualified) → Pflicht-Hinweis
        page.select_option("select[name='document_type']", label="Beratungsgespräch")
        expect(required_hint).to_be_visible()
        expect(anon_hint).to_be_hidden()

        # Zurück zu Kontakt → Anonym-Hinweis
        page.select_option("select[name='document_type']", label="Kontakt")
        expect(anon_hint).to_be_visible()
        expect(required_hint).to_be_hidden()

    def test_submit_without_client_auto_sets_anonymous(self, authenticated_page, base_url):
        """Kontakt ohne Klientel wird automatisch als anonym gespeichert. Refs #472"""
        page = authenticated_page
        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")

        page.select_option("select[name='document_type']", label="Kontakt")

        # Absenden ohne Klientel
        page.locator("#event-submit-btn").click()
        page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/$"), timeout=15000)

        # Event-Detailseite zeigt "Anonym"
        expect(page.locator("text=Anonym")).to_be_visible()
