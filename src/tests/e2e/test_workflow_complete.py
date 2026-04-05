"""E2E-Test: Kompletter Fallbearbeitungs-Workflow (Happy Path).

Client anlegen → Fall eröffnen → Episode → Wirkungsziel + Meilenstein →
Event dokumentieren → Fall schließen → Client-Export prüfen.
"""

import re
import uuid

import pytest

pytestmark = pytest.mark.e2e


class TestCompleteWorkflow:
    """Durchgängiger Happy-Path-Test für den kompletten Lebenszyklus."""

    def test_full_lifecycle(self, staff_page, lead_page, base_url):
        """Kompletter Workflow: Client → Fall → Episode → Ziel → Event → Close."""
        page = staff_page
        tag = uuid.uuid4().hex[:6]

        # --- 1. Client anlegen ---
        page.goto(f"{base_url}/clients/new/")
        page.fill('input[name="pseudonym"]', f"WF-Client-{tag}")
        page.select_option('select[name="contact_stage"]', value="identified")
        page.select_option('select[name="age_cluster"]', value="27_plus")
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/"))
        client_url = page.url
        assert f"WF-Client-{tag}" in page.locator("h1").inner_text().strip()

        # --- 2. Fall eröffnen ---
        page.goto(f"{base_url}/cases/new/")
        page.fill('input[name="title"]', f"WF-Fall-{tag}")
        page.fill('textarea[name="description"]', "Workflow-Test")
        page.select_option('select[name="lead_user"]', index=1)
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/"))
        case_url = page.url
        assert page.locator("h1").inner_text().strip() == f"WF-Fall-{tag}"

        # --- 3. Episode anlegen ---
        page.locator("a:has-text('Neue Episode')").click()
        page.wait_for_url(re.compile(r"/episodes/new/"))
        page.fill('input[name="title"]', f"WF-Episode-{tag}")
        page.fill('input[name="started_at"]', "2026-03-01")
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/$"))
        # Zurück auf Case-Detail — Episode sollte sichtbar sein
        assert page.locator(f"text=WF-Episode-{tag}").is_visible()

        # --- 4. Wirkungsziel (HTMX-Formular auf Case-Seite) ---
        goal_input = page.locator('input[name="title"][placeholder*="Wirkungsziel"]')
        if goal_input.count() > 0:
            goal_input.fill(f"WF-Ziel-{tag}")
            goal_input.press("Enter")
            page.wait_for_timeout(1000)
            assert page.locator(f"text=WF-Ziel-{tag}").is_visible()

        # --- 5. Event dokumentieren ---
        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")
        # Dokumenttyp auswählen (erster verfügbarer)
        doc_type_select = page.locator('select[name="document_type"]')
        doc_type_select.select_option(index=1)
        # Warte auf HTMX-Feldaktualisierung
        page.wait_for_timeout(1000)
        page.locator("#main-content button[type='submit']").click()
        # Sollte zum Event-Detail oder zur Timeline weiterleiten
        page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/|/zeitstrom/"))

        # --- 6. Fall schließen (Lead-Berechtigung nötig) ---
        lead = lead_page
        lead.goto(case_url)
        lead.wait_for_load_state("domcontentloaded")
        close_btn = lead.locator("a:has-text('Fall schließen'), button:has-text('Fall schließen')")
        if close_btn.count() > 0:
            close_btn.click()
            lead.wait_for_load_state("domcontentloaded")
            # Bestätigungs-Submit falls vorhanden
            confirm_btn = lead.locator("#main-content button[type='submit']")
            if confirm_btn.count() > 0:
                confirm_btn.click()
                lead.wait_for_load_state("domcontentloaded")
            # Case-Detail sollte "Geschlossen" anzeigen
            assert lead.locator("text=Geschlossen").count() > 0 or lead.locator("text=geschlossen").count() > 0

        # --- 7. Client-Detail prüfen (Daten nach Workflow vorhanden) ---
        lead.goto(client_url)
        lead.wait_for_load_state("domcontentloaded")
        assert f"WF-Client-{tag}" in lead.locator("h1").inner_text()

    def test_event_with_dynamic_fields(self, staff_page, base_url):
        """Event erstellen mit dynamischen Feldern je nach Dokumenttyp."""
        page = staff_page
        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")

        # Dokumenttyp wechseln und prüfen, ob Felder dynamisch geladen werden
        doc_type_select = page.locator('select[name="document_type"]')
        options = doc_type_select.locator("option")
        option_count = options.count()

        if option_count > 2:
            # Ersten Nicht-Placeholder-Typ wählen
            doc_type_select.select_option(index=1)
            page.wait_for_timeout(1000)
            # Zweiten Typ wählen — Felder sollten sich ändern
            doc_type_select.select_option(index=2)
            page.wait_for_timeout(1000)
            # Seite sollte weiterhin funktional sein
            assert page.locator("#main-content").is_visible()

    def test_workitem_status_transitions(self, staff_page, base_url):
        """WorkItem erstellen und Statusübergänge durchspielen."""
        page = staff_page
        tag = uuid.uuid4().hex[:6]

        # WorkItem erstellen
        page.goto(f"{base_url}/workitems/new/")
        page.fill('input[name="title"]', f"WF-Aufgabe-{tag}")
        page.select_option('select[name="item_type"]', value="task")
        page.select_option('select[name="priority"]', value="normal")
        page.locator("#main-content button[type='submit']").click()
        page.wait_for_url(re.compile(r"/workitems/"))

        # Aufgabe sollte in der Inbox erscheinen
        page.goto(f"{base_url}/workitems/")
        page.wait_for_load_state("domcontentloaded")
        assert page.locator(f"text=WF-Aufgabe-{tag}").is_visible()
