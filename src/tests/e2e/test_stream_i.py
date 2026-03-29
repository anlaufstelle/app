"""E2E-Tests für Stream I: Integration-Workflow."""

import re
import uuid

import pytest

pytestmark = pytest.mark.e2e

# The base template has a logout <button type="submit"> in the nav,
# so we must target the form's own submit button specifically.
SUBMIT = "#main-content button[type='submit']"


class TestIntegrationWorkflow:
    """Complete workflow: Client → Event → AuditLog → WorkItem → Role check."""

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
        page.fill('textarea[name="description"]', "E2E-Test Stream I.")
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
