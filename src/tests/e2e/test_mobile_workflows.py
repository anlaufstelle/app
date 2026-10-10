"""E2E: Mobile-Workflows (#928).

Refs Master #922. Deckt die fünf Mobile-Test-Cases der Matrix ab:

- SMK-A-MOBI-02 — Mobile Case-Update (Goal-Toggle, Sektionen sichtbar).
- ENT-CASE-12  — Mobile Case-Detail: Sektionen ohne horizontalen Scroll.
- ENT-WI-10    — Mobile WorkItem-Inbox: Single-Column, Status-Toggle (HTMX).
- ENT-CLIENT-14 — Mobile Klient-Liste: Card-Layout, ≥44px-Touch-Targets.
- ENT-A11Y-09  — Reflow bei reduzierter Viewport-Breite (200%-Zoom-Sim).

**Spec-Lücke zu SMK-A-MOBI-02:** Der Original-TC verlangt „Foto aufnehmen"
über den nativen Mobile-Datei-Picker. Das kann Playwright headless nicht
verifizieren (kein Kamera-Zugriff). Der TC ist hier auf die in der UI
testbaren Aspekte reduziert — Case-Update + Goal-Toggle —, der Foto-
Capture-Anteil bleibt manuell.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_no_horizontal_overflow(page) -> None:
    """Verifiziert, dass die Seite nicht horizontal scrollt."""
    overflow = page.evaluate(
        "() => Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth)"
    )
    assert overflow == 0, f"Horizontale Überlauf-Breite: {overflow}px (Layout reflowt nicht)."


def _assert_touch_target(locator, label: str, min_px: int = 44) -> None:
    """WCAG 2.5.5 / iOS-HIG: Touch-Targets sollen mindestens 44×44 px sein."""
    box = locator.bounding_box()
    assert box is not None, f"Touch-Target {label!r} ist nicht sichtbar / hat keine Box."
    assert box["width"] >= min_px and box["height"] >= min_px, (
        f"Touch-Target {label!r} zu klein: {box['width']:.0f}×{box['height']:.0f}px (erwartet ≥ {min_px}×{min_px}px)."
    )


def _open_first_qualified_case(page, base_url) -> str:
    """Navigiert zum ersten Fall der Liste und liefert die Detail-URL.

    Nutzt ``/cases/`` direkt — der Seed legt qualifizierten Klientinnen
    mindestens einen Fall an, also ist der erste Eintrag der Liste in jedem
    Lauf vorhanden.
    """
    page.goto(f"{base_url}/cases/", wait_until="domcontentloaded")
    # Auf Mobile rendert die Liste Cards in einem ``.sm:hidden``-Container; auf
    # Desktop die Tabelle in ``.hidden.sm:block``. ``visible``-Filter selektiert
    # automatisch die im aktuellen Viewport sichtbare Variante.
    # ``/trash/`` ausschliessen: seit dem Fall-Papierkorb (Refs #1818) steht in
    # der Kopfleiste der Fallliste ein Admin-Link auf ``/cases/trash/`` — im
    # DOM VOR der Tabelle. ``.first`` traf dann den Papierkorb statt eines
    # Fall-Details, und ``wait_for_url(…/<uuid>/)`` lief in den Timeout.
    case_link = (
        page.locator("main a[href^='/cases/']:not([href$='/new/']):not([href$='/trash/'])").filter(visible=True).first
    )
    case_link.wait_for(state="visible", timeout=10000)
    case_link.click()
    page.wait_for_url(re.compile(r"/cases/[0-9a-f-]{36}/$"), timeout=10000)
    return page.url


# ---------------------------------------------------------------------------
# TC ENT-CLIENT-14 — Mobile-Liste auf iPhone-Viewport
# ---------------------------------------------------------------------------


class TestMobileClientList:
    """Refs Matrix ENT-CLIENT-14."""

    def test_card_layout_on_mobile_no_horizontal_scroll(self, mobile_authenticated_page, base_url):
        page = mobile_authenticated_page
        page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")
        _assert_no_horizontal_overflow(page)

    def test_mobile_nav_present_on_client_list(self, mobile_authenticated_page, base_url):
        """Bottom-Nav muss auf Mobile sichtbar sein."""
        page = mobile_authenticated_page
        page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")
        nav_link = page.locator("[data-testid='mobile-nav-clients']")
        nav_link.wait_for(state="visible", timeout=5000)
        _assert_touch_target(nav_link, "mobile-nav-clients")


# ---------------------------------------------------------------------------
# TC ENT-WI-10 — Mobile-WorkItem-Inbox
# ---------------------------------------------------------------------------


class TestMobileWorkItemInbox:
    """Refs Matrix ENT-WI-10."""

    def test_workitem_inbox_no_horizontal_scroll(self, mobile_authenticated_page, base_url):
        page = mobile_authenticated_page
        page.goto(f"{base_url}/workitems/", wait_until="domcontentloaded")
        _assert_no_horizontal_overflow(page)

    def test_workitem_mobile_nav_touch_target(self, mobile_authenticated_page, base_url):
        page = mobile_authenticated_page
        page.goto(f"{base_url}/workitems/", wait_until="domcontentloaded")
        nav = page.locator("[data-testid='mobile-nav-workitems']")
        nav.wait_for(state="visible", timeout=5000)
        _assert_touch_target(nav, "mobile-nav-workitems")

    def test_workitem_status_toggle_via_tap(self, mobile_authenticated_page, base_url):
        """HTMX-Status-Toggle funktioniert per Tap, kein Full-Reload.

        Verifiziert über die URL: nach dem Tap auf „Übernehmen" (Refs #1146)
        bleibt ``/workitems/`` aktiv (keine Vollnavigation), und der
        Erledigt-Button wird sichtbar (HTMX-Swap).
        """
        page = mobile_authenticated_page
        # Aufgabe erzeugen, damit garantiert eine offene Karte da ist.
        page.goto(f"{base_url}/workitems/new/", wait_until="domcontentloaded")
        page.select_option("select[name='item_type']", value="task")
        page.fill("input[name='title']", "Mobile-Statustest")
        page.select_option("select[name='priority']", value="normal")
        page.click("button:has-text('Speichern')")
        page.wait_for_url(re.compile(r"/workitems/$"), timeout=10000)

        url_before = page.url
        accept_btn = page.locator("button:has-text('Übernehmen')").first
        accept_btn.wait_for(state="visible", timeout=5000)
        accept_btn.click()
        # HTMX-Swap: kein Full-Reload, URL bleibt gleich.
        page.locator("button:has-text('Erledigt')").first.wait_for(state="visible", timeout=5000)
        assert page.url == url_before, "HTMX-Swap darf nicht zu Voll-Navigation führen."


# ---------------------------------------------------------------------------
# TC ENT-CASE-12 — Mobile-Case-Detail mit Sektionen
# ---------------------------------------------------------------------------


class TestMobileCaseDetail:
    """Refs Matrix ENT-CASE-12."""

    def test_case_detail_no_horizontal_scroll(self, mobile_authenticated_page, base_url):
        page = mobile_authenticated_page
        _open_first_qualified_case(page, base_url)
        _assert_no_horizontal_overflow(page)

    def test_case_detail_sections_visible(self, mobile_authenticated_page, base_url):
        """Sektionen Episoden, Goals (Wirkungsziele) sind erreichbar."""
        page = mobile_authenticated_page
        _open_first_qualified_case(page, base_url)
        # Wirkungsziele-Sektion existiert (Heading oder Container).
        assert page.locator(":text-matches('Wirkungsziele|Goals', 'i')").first.is_visible()


# ---------------------------------------------------------------------------
# TC SMK-A-MOBI-02 — Streetwork: Goal-Toggle auf Mobile
# ---------------------------------------------------------------------------


_GOAL_PREFIX = "E2E Mobil"

_GOAL_CLEANUP = f"""
from core.models import Case, Client

Case.objects.filter(title__startswith='{_GOAL_PREFIX}').delete()
Client.objects.filter(pseudonym__startswith='{_GOAL_PREFIX}').delete()
"""

_GOAL_SETUP = f"""
from core.models import Case, Client, Facility, OutcomeGoal, User

facility = Facility.objects.get(name='Hauptstelle')
admin = User.objects.get(username='admin')
person = Client.objects.create(
    facility=facility, contact_stage='identified', pseudonym='{_GOAL_PREFIX}-Person', created_by=admin
)
case = Case.objects.create(facility=facility, client=person, title='{_GOAL_PREFIX} Fall', created_by=admin)
goal = OutcomeGoal.objects.create(case=case, title='{_GOAL_PREFIX} Ziel', created_by=admin)
print('GOAL_CASE', case.pk, goal.pk)
"""


@pytest.fixture(scope="module")
def open_case_with_goal(e2e_env):
    """An open case with exactly one open goal, created for this module.

    The seeded goals are spread over the cases at random, so no seeded case is
    guaranteed to carry one; the precondition is built here and handed over as
    the real primary keys.
    """
    django_shell(e2e_env, _GOAL_CLEANUP)
    lines = django_shell(e2e_env, _GOAL_SETUP)
    row = next((line.split() for line in lines if line.startswith("GOAL_CASE ")), None)
    assert row and len(row) == 3, f"Goal setup did not report the created case: {lines}"
    yield {"case": row[1], "goal": row[2]}
    django_shell(e2e_env, _GOAL_CLEANUP)


def _goal_toggle(page, base_url, fixture):
    """Open the fixture case and return its goal-toggle button."""
    page.goto(f"{base_url}/cases/{fixture['case']}/", wait_until="domcontentloaded")
    return page.locator(f"form[hx-post$='/goals/{fixture['goal']}/toggle/'] button[type='submit']")


class TestMobileStreetworkCaseUpdate:
    """Refs Matrix SMK-A-MOBI-02 (reduzierter Scope — siehe Modul-Docstring)."""

    def test_goal_toggle_via_tap_persists(self, mobile_authenticated_page, base_url, open_case_with_goal):
        """Tap auf Goal-Toggle-Button ändert den Zustand und persistiert."""
        page = mobile_authenticated_page
        toggle = _goal_toggle(page, base_url, open_case_with_goal)
        goal_card = page.locator("#goals-section .card", has=toggle)
        expect(goal_card.get_by_text("offen", exact=True)).to_be_visible()
        expect(toggle).to_have_text("Ziel erreicht")

        toggle.tap()

        # The HTMX swap replaces the goals section; the new state is rendered ...
        expect(goal_card.get_by_text("erreicht", exact=True)).to_be_visible()
        expect(toggle).to_have_text("Nicht erreicht")

        # ... and survives a reload, i.e. it was persisted.
        page.reload(wait_until="domcontentloaded")
        expect(goal_card.get_by_text("erreicht", exact=True)).to_be_visible()
        expect(toggle).to_have_text("Nicht erreicht")

    def test_goal_toggle_touch_target(self, mobile_authenticated_page, base_url, open_case_with_goal):
        """The toggle is a touch target of at least 44 px (the matrix case asks for it)."""
        page = mobile_authenticated_page
        toggle = _goal_toggle(page, base_url, open_case_with_goal)
        toggle.wait_for(state="visible", timeout=10000)
        _assert_touch_target(toggle, "goal-toggle-button")


# ---------------------------------------------------------------------------
# TC ENT-A11Y-09 — Reflow bei reduzierter Viewport-Breite (200%-Zoom-Sim)
# ---------------------------------------------------------------------------


class TestMobileReflowAtZoom:
    """Refs Matrix ENT-A11Y-09.

    200%-Zoom wird simuliert, indem die Viewport-Breite halbiert wird
    (375/2 ≈ 187 px). Das ist die visuelle Fläche, die der Nutzer bei
    aktivem Browser-Zoom 200% sieht. Erwartung: kein horizontaler
    Scrollbalken auf den Kern-Pfaden.

    **A11y-Hinweis:** Hier wird KEIN axe-core-Audit gefahren —
    systematisches WCAG-Testing ist bewusst out-of-scope.
    """

    def test_reflow_dashboard(self, mobile_authenticated_page, base_url):
        page = mobile_authenticated_page
        page.set_viewport_size({"width": 187, "height": 406})
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        _assert_no_horizontal_overflow(page)

    def test_reflow_client_list(self, mobile_authenticated_page, base_url):
        page = mobile_authenticated_page
        page.set_viewport_size({"width": 187, "height": 406})
        page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")
        _assert_no_horizontal_overflow(page)

    def test_reflow_event_create_form(self, mobile_authenticated_page, base_url):
        page = mobile_authenticated_page
        page.set_viewport_size({"width": 187, "height": 406})
        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
        _assert_no_horizontal_overflow(page)
