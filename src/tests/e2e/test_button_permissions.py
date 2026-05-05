"""E2E-Tests: Button/Link-Sichtbarkeit je nach Rolle und Ownership (Refs #457).

Prüft, dass Buttons wie "Bearbeiten", "Löschen", Status-Aktionen und
Schnellzugriffe auf Detail-Seiten nur für berechtigte Rollen sichtbar sind.
"""

import re
import uuid

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

SUBMIT = "#main-content button[type='submit']"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _navigate_to_first_workitem_detail(page, base_url):
    """Navigate to the detail page of the first workitem in the inbox."""
    page.goto(f"{base_url}/workitems/", wait_until="domcontentloaded")
    page.locator("#inbox-content a[href*='/workitems/']").first.click()
    page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/"))
    page.wait_for_load_state("domcontentloaded")


def _navigate_to_first_client_detail(page, base_url):
    """Navigate to the detail page of the first client in the list."""
    page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")
    # Klientel-Pseudonym-Link in der Liste (#663: text-sm → text-[14px], font-medium → font-semibold)
    # Klientel-Pseudonym-Link in der Liste (Visual-Refresh #663: Linktext span hat font-semibold text-accent)
    page.locator(".client-list a[href^='/clients/']").first.click()
    page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/"), wait_until="domcontentloaded")
    page.wait_for_load_state("domcontentloaded")


def _create_event_and_go_to_detail(page, base_url):
    """Create a Kontakt event and navigate to its detail page. Returns the page URL."""
    page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
    page.select_option("select[name='document_type']", label="Kontakt")
    page.wait_for_load_state("domcontentloaded")
    page.fill("input[name='occurred_at']", "2026-03-20T10:00")

    # No client selected → automatically anonymous

    # Fill optional duration field if present
    dauer = page.locator("input[name='dauer']")
    if dauer.count() > 0:
        dauer.fill("5")

    page.locator(SUBMIT).click()
    page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/$"), timeout=15000)
    page.wait_for_load_state("domcontentloaded")
    return page.url


# ---------------------------------------------------------------------------
# WorkItem Detail Permissions
# ---------------------------------------------------------------------------


class TestWorkItemDetailPermissions:
    """Button-Sichtbarkeit auf der WorkItem-Detail-Seite."""

    def test_assistant_no_edit_button_on_workitem_detail(self, assistant_page, base_url):
        """Assistenz sieht keinen 'Bearbeiten'-Link auf der WorkItem-Detail-Seite."""
        page = assistant_page
        _navigate_to_first_workitem_detail(page, base_url)

        # "Bearbeiten" link should NOT be visible for assistant role
        edit_link = page.locator("a:has-text('Bearbeiten')")
        expect(edit_link).not_to_be_visible()

    def test_staff_sees_edit_button_on_workitem_detail(self, staff_page, base_url):
        """Fachkraft sieht den 'Bearbeiten'-Link auf einem selbst erstellten WorkItem.

        Vorher klickte der Test auf den ersten WorkItem-Link in der Inbox — bei
        kleiner Seed-Menge (5 WorkItems, 60% mit Assignee) hatte Miriam aber
        nicht zwingend Edit-Rechte auf das oberste Element, was im
        Parallellauf reproduzierbar zu Flakes fuehrte (Refs #761).
        """
        page = staff_page

        # Eigenes WorkItem anlegen, damit die Fachkraft garantiert
        # Edit-Rechte hat (created_by == user, siehe can_user_mutate_workitem).
        unique_title = f"E2E-Perm-Edit-{uuid.uuid4().hex[:6]}"
        page.goto(f"{base_url}/workitems/new/", wait_until="domcontentloaded")
        page.fill("input[name='title']", unique_title)
        page.select_option("select[name='item_type']", index=1)
        page.locator(SUBMIT).click()
        page.wait_for_url(f"{base_url}/workitems/")
        page.wait_for_load_state("domcontentloaded")

        page.locator(f"a:has-text('{unique_title}')").click()
        page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/"))
        page.wait_for_load_state("domcontentloaded")

        edit_link = page.locator("a:has-text('Bearbeiten')")
        expect(edit_link).to_be_visible()

    def test_creator_sees_status_buttons(self, staff_page, base_url):
        """Fachkraft sieht Status-Aktionsbuttons auf einem selbst erstellten WorkItem."""
        page = staff_page

        # Create a new workitem as staff user
        unique_title = f"E2E-Perm-{uuid.uuid4().hex[:6]}"
        page.goto(f"{base_url}/workitems/new/", wait_until="domcontentloaded")
        page.fill("input[name='title']", unique_title)
        page.select_option("select[name='item_type']", index=1)
        page.locator(SUBMIT).click()
        page.wait_for_url(f"{base_url}/workitems/")
        page.wait_for_load_state("domcontentloaded")

        # Navigate to its detail page
        page.locator(f"a:has-text('{unique_title}')").click()
        page.wait_for_url(re.compile(r"/workitems/[0-9a-f-]+/"))
        page.wait_for_load_state("domcontentloaded")

        # Status action buttons should be visible (open workitem has "Annehmen" + "Verwerfen")
        expect(page.locator("button:has-text('Annehmen')")).to_be_visible()
        expect(page.locator("button:has-text('Verwerfen')")).to_be_visible()


# ---------------------------------------------------------------------------
# Event Detail Permissions
# ---------------------------------------------------------------------------


class TestEventDetailPermissions:
    """Button-Sichtbarkeit auf der Event-Detail-Seite."""

    def test_assistant_no_edit_delete_on_other_event(self, assistant_page, authenticated_page, base_url):
        """Assistenz sieht weder 'Bearbeiten' noch 'Löschen' bei fremden Events."""
        # Create an event as admin
        admin = authenticated_page
        event_url = _create_event_and_go_to_detail(admin, base_url)
        event_path = re.search(r"/events/[0-9a-f-]+/", event_url).group(0)

        # Visit the same event as assistant
        page = assistant_page
        page.goto(f"{base_url}{event_path}", wait_until="domcontentloaded")

        # Desktop buttons area: neither "Bearbeiten" nor "Löschen"
        edit_link = page.locator(".hidden.md\\:flex a:has-text('Bearbeiten')")
        delete_link = page.locator(".hidden.md\\:flex a:has-text('Löschen')")
        expect(edit_link).not_to_be_visible()
        expect(delete_link).not_to_be_visible()

    def test_staff_sees_edit_and_delete_on_own_event(self, staff_page, base_url):
        """Fachkraft sieht 'Bearbeiten' und 'Löschen' bei eigenem Event."""
        page = staff_page
        _create_event_and_go_to_detail(page, base_url)

        # Desktop: "Bearbeiten" visible
        edit_link = page.locator(".hidden.md\\:flex a:has-text('Bearbeiten')")
        expect(edit_link).to_be_visible()

        # Desktop: "Löschen" visible (staff can delete own events)
        delete_link = page.locator(".hidden.md\\:flex a:has-text('Löschen')")
        expect(delete_link).to_be_visible()

    def test_lead_sees_all_buttons_on_any_event(self, lead_page, authenticated_page, base_url):
        """Leitung sieht 'Bearbeiten' und 'Löschen' bei jedem Event."""
        # Create an event as admin so it's someone else's event
        admin = authenticated_page
        event_url = _create_event_and_go_to_detail(admin, base_url)
        event_path = re.search(r"/events/[0-9a-f-]+/", event_url).group(0)

        # Visit as lead
        page = lead_page
        page.goto(f"{base_url}{event_path}", wait_until="domcontentloaded")

        # Desktop: both buttons visible
        edit_link = page.locator(".hidden.md\\:flex a:has-text('Bearbeiten')")
        delete_link = page.locator(".hidden.md\\:flex a:has-text('Löschen')")
        expect(edit_link).to_be_visible()
        expect(delete_link).to_be_visible()


# ---------------------------------------------------------------------------
# Client Detail Permissions
# ---------------------------------------------------------------------------


class TestClientDetailPermissions:
    """Button-Sichtbarkeit auf der Client-Detail-Seite."""

    def test_assistant_limited_buttons_on_client_detail(self, assistant_page, base_url):
        """Assistenz sieht nur 'Neuer Kontakt', nicht 'Bearbeiten', 'Neue Aufgabe', 'Neuer Fall'."""
        page = assistant_page
        _navigate_to_first_client_detail(page, base_url)

        # Desktop buttons area
        desktop_area = page.locator(".hidden.md\\:flex")

        # "Neuer Kontakt" IS visible (no role restriction)
        expect(desktop_area.locator("a:has-text('Neuer Kontakt')")).to_be_visible()

        # "Bearbeiten" should NOT be visible (staff_or_above only)
        expect(desktop_area.locator("a:has-text('Bearbeiten')")).not_to_be_visible()

        # "Neue Aufgabe" should NOT be visible (staff_or_above only)
        expect(desktop_area.locator("a:has-text('Neue Aufgabe')")).not_to_be_visible()

        # "Neuer Fall" link below the cases section should NOT be visible (staff_or_above only)
        expect(page.locator("a:has-text('Neuer Fall')")).not_to_be_visible()


# ---------------------------------------------------------------------------
# Event Create Validation
# ---------------------------------------------------------------------------


class TestEventCreateValidation:
    """Validierung im Event-Erstellungsformular."""

    def test_event_without_client_when_min_stage_required(self, staff_page, base_url):
        """Beratungsgespräch ohne Klientel-Auswahl zeigt Fehlermeldung."""
        page = staff_page
        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")

        # Select "Beratungsgespräch" which has min_contact_stage=qualified
        page.select_option("select[name='document_type']", label="Beratungsgespräch")
        page.wait_for_load_state("domcontentloaded")

        # Fill occurred_at (required field)
        page.fill("input[name='occurred_at']", "2026-03-20T10:00")

        # Fill optional dynamic fields if present
        thema = page.locator("input[name='thema']")
        if thema.count() > 0:
            thema.fill("Testthema")

        # Do NOT select a client — leave it empty
        # Submit the form
        page.locator(SUBMIT).click()
        page.wait_for_load_state("domcontentloaded")

        # Expect a validation error about requiring a client for this document type
        # (Sprachleitlinie #604: "Klientel" -> "Person")
        error_text = page.locator(".text-red-600:has-text('Person')")
        expect(error_text).to_be_visible(timeout=5000)
