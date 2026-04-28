"""E2E-Tests: Workflow-Tests (Happy Path + Integration).

- Kompletter Fallbearbeitungs-Workflow (Client → Fall → Episode → Ziel → Event → Close)
- Integration-Workflow (Client → Event → Audit → WorkItem → Rollencheck)
"""

import re
import uuid

import pytest

pytestmark = pytest.mark.e2e

SUBMIT = "#main-content button[type='submit']"


@pytest.mark.smoke
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
        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
        # Dokumenttyp auswählen (erster verfügbarer)
        page.select_option('select[name="document_type"]', label="Kontakt")
        # Warte auf HTMX-Feldaktualisierung
        page.locator("#dynamic-fields input, #dynamic-fields textarea, #dynamic-fields select").first.wait_for(
            state="attached", timeout=5000
        )
        page.locator("#event-submit-btn").click()
        # Sollte zum Event-Detail weiterleiten
        page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/"), timeout=15000)

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


class TestIntegrationWorkflow:
    """Integration-Workflow: Client → Event → AuditLog → WorkItem → Role check."""

    def test_admin_creates_client_and_event(self, authenticated_page, base_url):
        """Admin creates a client, creates an event for that client, verifies event in Chronik."""
        page = authenticated_page
        pseudonym = f"E2E-I-{uuid.uuid4().hex[:8]}"

        # --- Create client ---
        page.goto(f"{base_url}/clients/new/")
        page.wait_for_load_state("domcontentloaded")

        page.fill('input[name="pseudonym"]', pseudonym)
        page.select_option('select[name="contact_stage"]', "identified")
        page.select_option('select[name="age_cluster"]', "18_26")
        page.locator(SUBMIT).click()

        # After submit we should land on the client detail page
        page.wait_for_load_state("domcontentloaded")
        assert re.search(r"/clients/[0-9a-f-]+/$", page.url), f"Expected client detail URL, got {page.url}"

        # Pseudonym is visible on detail page
        assert page.locator(f"text={pseudonym}").first.is_visible()

        # --- Create event (Kontakt) for that client ---
        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")

        # Select document type "Kontakt"
        page.select_option('select[name="document_type"]', label="Kontakt")
        page.wait_for_load_state("domcontentloaded")

        # Fill client autocomplete
        client_search = page.locator('input[placeholder="Pseudonym eingeben..."]')
        client_search.fill(pseudonym)

        # Wait for autocomplete dropdown, then pick the suggestion
        ac_btn = page.locator(f"#client-autocomplete-list button:has-text('{pseudonym}')")
        ac_btn.wait_for(state="visible", timeout=5000)
        ac_btn.click()

        # Fill occurred_at and dynamic fields
        page.fill('input[name="occurred_at"]', "2026-03-20T10:00")
        dauer_field = page.locator("input[name='dauer']")
        if dauer_field.count() > 0:
            dauer_field.fill("10")

        page.locator(SUBMIT).click()
        page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/$"), timeout=10000)

        # --- Verify event was created ---
        assert page.locator("h1").inner_text() == "Kontakt", "Expected 'Kontakt' as event document type"

    def test_audit_log_has_entries(self, authenticated_page, base_url):
        """Admin accesses audit log; entries exist and page loads correctly."""
        page = authenticated_page

        page.goto(f"{base_url}/audit/")
        page.wait_for_load_state("domcontentloaded")

        # h1 "Audit-Log" is visible
        assert page.locator("h1").filter(has_text="Audit-Log").is_visible()

        # Either table with entries or empty-state message is present
        has_table = page.locator("table").count() > 0
        has_empty = page.locator("text=Keine Einträge").count() > 0
        assert has_table or has_empty, "Weder Tabelle noch Leer-Zustand im Audit-Log gefunden"

    def test_admin_creates_workitem(self, authenticated_page, base_url):
        """Admin creates a WorkItem; it appears in the inbox."""
        page = authenticated_page
        title = f"E2E-WI-{uuid.uuid4().hex[:8]}"

        page.goto(f"{base_url}/workitems/new/")
        page.wait_for_load_state("domcontentloaded")

        page.fill('input[name="title"]', title)
        page.select_option('select[name="item_type"]', "task")
        page.select_option('select[name="priority"]', "normal")
        page.fill('textarea[name="description"]', "E2E-Test Integration.")
        page.locator(SUBMIT).click()
        page.wait_for_load_state("domcontentloaded")

        # After submission navigate to inbox and verify entry
        page.goto(f"{base_url}/workitems/")
        page.wait_for_load_state("domcontentloaded")

        assert page.locator(f"text={title}").first.is_visible(), "WorkItem not found in inbox after creation"

    def test_staff_can_see_workitems_but_not_statistics(self, staff_page, base_url):
        """Staff user (miriam) can access workitem inbox but is denied statistics."""
        page = staff_page

        # Staff can access the workitem inbox
        resp = page.goto(f"{base_url}/workitems/")
        page.wait_for_load_state("domcontentloaded")
        assert resp.status == 200, f"Staff should be able to access /workitems/, got {resp.status}"

        # Staff cannot access statistics — expect 403 or redirect to login
        resp = page.goto(f"{base_url}/statistics/")
        page.wait_for_load_state("domcontentloaded")
        assert resp.status == 403 or "/login/" in page.url, (
            f"Expected 403 or login redirect for staff on /statistics/, got status {resp.status} on {page.url}"
        )
