"""E2E-Tests: Button/Link-Sichtbarkeit je nach Rolle und Ownership (Refs #457).

Prüft, dass Buttons wie "Bearbeiten", "Löschen", Status-Aktionen und
Schnellzugriffe auf Detail-Seiten nur für berechtigte Rollen sichtbar sind.
"""

import re
import uuid

import pytest
from playwright.sync_api import expect

from ._selectors import action_bar, open_desktop_overflow

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

    # Refs #1349: seit dem zweiten Submit-Button ("Speichern & nächster
    # Kontakt") ist der generische SUBMIT-Selektor auf dieser Seite
    # mehrdeutig — gezielt den primären Button ansprechen.
    page.locator("#event-submit-btn").click()
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

        # Status action buttons should be visible (Refs #1130: Detailansicht
        # bietet "Aufgabe übernehmen" + "Als nicht relevant schließen").
        # Refs #1813: die Statusaktionen stehen jetzt in der Aktionsleiste, die
        # zweimal gerendert wird (Desktop/Mobil) — ungescopt waere der Selektor
        # mehrdeutig, daher immer die Desktop-Leiste als Scope.
        desktop = action_bar(page, "workitem")
        expect(desktop.locator("button:has-text('Aufgabe übernehmen')")).to_be_visible()
        expect(desktop.locator("button:has-text('Als nicht relevant schließen')")).to_be_visible()


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

        # Weder "Bearbeiten" noch "Löschen" — Refs #1813: beide Aktionen tragen
        # jetzt eigene Testids; "Löschen" liegt hinter dem ⋮-Knopf. Für die
        # Assistenz rendert die Seite beide Einträge gar nicht erst, deshalb
        # zählen statt auf Sichtbarkeit prüfen (der Overflow bliebe sonst zu).
        expect(page.locator("[data-testid='event-action-edit']")).to_have_count(0)
        expect(page.locator("[data-testid='event-action-delete']")).to_have_count(0)

    def test_staff_sees_edit_and_delete_on_own_event(self, staff_page, base_url):
        """Fachkraft sieht 'Bearbeiten' und 'Löschen' bei eigenem Event."""
        page = staff_page
        _create_event_and_go_to_detail(page, base_url)

        # Desktop: "Bearbeiten" steht primär in der Aktionsleiste
        expect(action_bar(page, "event").locator("[data-testid='event-action-edit']")).to_be_visible()

        # Refs #1813: "Löschen" ist folgenschwer und liegt deshalb nie primär,
        # sondern im ⋮-Menü — sichtbar, sobald das Menü offen ist.
        overflow = open_desktop_overflow(page, "event")
        expect(overflow.locator("[data-testid='event-action-delete']")).to_be_visible()

    def test_lead_sees_all_buttons_on_any_event(self, lead_page, authenticated_page, base_url):
        """Leitung sieht 'Bearbeiten' und 'Löschen' bei jedem Event."""
        # Create an event as admin so it's someone else's event
        admin = authenticated_page
        event_url = _create_event_and_go_to_detail(admin, base_url)
        event_path = re.search(r"/events/[0-9a-f-]+/", event_url).group(0)

        # Visit as lead
        page = lead_page
        page.goto(f"{base_url}{event_path}", wait_until="domcontentloaded")

        # Desktop: "Bearbeiten" primär, "Löschen" im ⋮-Menü (Refs #1813)
        expect(action_bar(page, "event").locator("[data-testid='event-action-edit']")).to_be_visible()
        overflow = open_desktop_overflow(page, "event")
        expect(overflow.locator("[data-testid='event-action-delete']")).to_be_visible()


# ---------------------------------------------------------------------------
# Client Detail Permissions
# ---------------------------------------------------------------------------


class TestClientDetailPermissions:
    """Button-Sichtbarkeit auf der Client-Detail-Seite."""

    def test_assistant_limited_buttons_on_client_detail(self, assistant_page, base_url):
        """Assistenz sieht nur 'Kontakt dokumentieren', nicht 'Bearbeiten', 'Aufgabe erstellen', 'Fall anlegen'.

        Refs #1813 (F6): die Aktionen tragen die Verb-Vokabel („Kontakt
        dokumentieren" statt „Neuer Kontakt") und eigene Testids — die
        Rollen-Aussage dieses Tests bleibt unverändert.
        """
        page = assistant_page
        _navigate_to_first_client_detail(page, base_url)

        # Desktop-Aktionsleiste
        desktop_area = action_bar(page, "client")

        # "Kontakt dokumentieren" IS visible (no role restriction)
        expect(desktop_area.locator("[data-testid='client-action-contact']")).to_be_visible()

        # "Bearbeiten" should NOT exist (staff_or_above only)
        expect(page.locator("[data-testid='client-action-edit']")).to_have_count(0)

        # "Aufgabe erstellen" should NOT exist (staff_or_above only)
        expect(page.locator("[data-testid='client-action-task']")).to_have_count(0)

        # "Fall anlegen" link below the cases section should NOT be visible (staff_or_above only)
        expect(page.locator("a:has-text('Fall anlegen')")).to_have_count(0)

        # Refs #1734: auch die beiden neuen Flächen (Aktionsleiste, Overflow-Menü)
        # tragen denselben Guard — keine davon darf für die Assistenz existieren.
        for testid in ("client-new-case-btn", "mobile-client-new-case-btn", "client-new-case-link"):
            expect(page.locator(f"[data-testid='{testid}']")).to_have_count(0)

    def test_staff_sees_new_case_button_in_action_bar(self, staff_page, base_url):
        """Refs #1734: „Fall anlegen" ist aus der Desktop-Aktionsleiste erreichbar,
        nicht nur als Fuß-Textlink unter der Fallliste.

        Refs #1813: Die Fall-Anlage ist folgenschwer genug, um nicht primär zu
        stehen (§6.2) — sie sitzt jetzt im ⋮-Menü „Weitere Aktionen" derselben
        Leiste. Die Aussage bleibt: erreichbar ohne Scrollen bis unter die
        Chronik.
        """
        page = staff_page
        _navigate_to_first_client_detail(page, base_url)

        overflow = open_desktop_overflow(page, "client")
        button = overflow.locator("[data-testid='client-new-case-btn']")
        expect(button).to_be_visible()
        button.click()
        page.wait_for_url(re.compile(r"/cases/new/\?client=[0-9a-f-]+"))
        # Person ist im Zielformular vorbelegt (``?client=<pk>``).
        expect(page.locator("input[name='client']")).not_to_have_value("")


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
        # Submit the form (Refs #1349: primärer Button, SUBMIT ist wegen des
        # zweiten Buttons "Speichern & nächster Kontakt" mehrdeutig)
        page.locator("#event-submit-btn").click()
        page.wait_for_load_state("domcontentloaded")

        # Expect a validation error about requiring a client for this document type
        # (Sprachleitlinie #604: "Klientel" -> "Person")
        error_text = page.locator(".text-red-600:has-text('Person')")
        expect(error_text).to_be_visible(timeout=5000)
