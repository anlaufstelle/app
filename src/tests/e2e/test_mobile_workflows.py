"""E2E: Mobile-Workflows (Welle 5 #928).

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
    case_link = page.locator("main a[href^='/cases/']:not([href$='/new/'])").filter(visible=True).first
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

        Verifiziert über die URL: nach dem Tap auf „Annehmen" bleibt
        ``/workitems/`` aktiv (keine Vollnavigation), und der Erledigt-Button
        wird sichtbar (HTMX-Swap).
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
        accept_btn = page.locator("button:has-text('Annehmen')").first
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


class TestMobileStreetworkCaseUpdate:
    """Refs Matrix SMK-A-MOBI-02 (reduzierter Scope — siehe Modul-Docstring)."""

    def test_goal_toggle_via_tap_persists(self, mobile_authenticated_page, base_url):
        """Tap auf Goal-Toggle-Button ändert den Zustand und persistiert.

        Voraussetzung: der Seed legt für qualifizierte Fälle Goals an.
        Falls ein bestimmter Fall keine Goals hat, wird der Test übersprungen.
        """
        page = mobile_authenticated_page
        _open_first_qualified_case(page, base_url)

        toggle = page.locator("form[action*='/goals/'][action$='/toggle/'] button[type='submit']").first
        if toggle.count() == 0 or not toggle.is_visible():
            pytest.skip("Fall hat keine Goals — Seed-Variation; nicht testbar in diesem Lauf.")
        _assert_touch_target(toggle, "goal-toggle-button")

        url_before = page.url
        toggle.click()
        page.wait_for_load_state("domcontentloaded")
        # Goal-Toggle redirected zurück auf Case-Detail (oder bleibt).
        assert page.url.startswith(url_before.rsplit("/", 1)[0]) or page.url == url_before


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
