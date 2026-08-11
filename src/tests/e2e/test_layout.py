"""E2E-Tests: Layout — Unified Desktop/Mobile, Kontext-Kopf, Accessibility, Print."""

import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


class TestUnifiedLayout:
    """Content muss auf Desktop und Mobile sichtbar sein."""

    def test_desktop_content_visible(self, authenticated_page, base_url):
        page = authenticated_page
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{base_url}/")
        assert page.locator("main#main-content").is_visible()
        # h1 ist eindeutig, "text=Zeitstrom" matched seit #663 auch den Create-Dropdown-Subtitle.
        assert page.locator("main#main-content h1").is_visible()

    def test_desktop_clients_visible(self, authenticated_page, base_url):
        page = authenticated_page
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{base_url}/clients/")
        assert page.locator("h1:has-text('Personen')").is_visible()
        # Personen-Liste seit Refs #643 als CSS-Grid statt <table>; bei 1280px greift sm:grid.
        assert page.locator(".client-list").is_visible()
        # Mindestens eine Person als Link
        assert page.locator(".client-list a[href^='/clients/']").first.is_visible()

    def test_desktop_statistics_visible(self, authenticated_page, base_url):
        page = authenticated_page
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{base_url}/statistics/")
        assert page.locator("text=Gesamtkontakte").is_visible()

    def test_mobile_content_visible(self, authenticated_page, base_url):
        page = authenticated_page
        page.set_viewport_size({"width": 375, "height": 812})
        page.goto(f"{base_url}/")
        assert page.locator("main#main-content").is_visible()
        assert page.locator("main#main-content h1").is_visible()

    def test_mobile_clients_visible(self, authenticated_page, base_url):
        page = authenticated_page
        page.set_viewport_size({"width": 375, "height": 812})
        page.goto(f"{base_url}/clients/")
        assert page.locator("main#main-content h1").is_visible()
        # Mobile card layout: zumindest ein Personen-Detail-Link (UUID-href, nicht "/clients/new/")
        # Der "Neue Person"-Button ist auf Mobile via hidden md:block versteckt.
        assert page.locator(".client-list a[href^='/clients/']").first.is_visible()

    def test_mobile_statistics_visible(self, authenticated_page, base_url):
        page = authenticated_page
        page.set_viewport_size({"width": 375, "height": 812})
        page.goto(f"{base_url}/statistics/")
        assert page.locator("text=Gesamtkontakte").is_visible()


class TestAccessibility:
    """WCAG 2.1 AA Verbesserungen."""

    def test_skip_to_content_link(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/")
        skip_link = page.locator("a[href='#main-content']")
        assert skip_link.count() == 1
        assert "Zum Inhalt springen" in skip_link.inner_text()


class TestPrintStyles:
    """Druckansicht."""

    def test_nav_hidden_in_print(self, authenticated_page, base_url):
        page = authenticated_page
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{base_url}/")
        page.emulate_media(media="print")
        nav = page.locator("nav[aria-label='Hauptnavigation']")
        assert not nav.is_visible()


# ---------------------------------------------------------------------------
# Kontext-Kopf (Refs #1802)
# ---------------------------------------------------------------------------


def _create_event_with_client(page, base_url):
    """Legt ein Ereignis mit Personenbezug an und landet auf dessen Detailseite.

    Der Personenbezug ist hier nicht Beiwerk: ohne ihn gäbe es kein
    Kontext-Badge zu messen. ``Stern-42`` ist Seed-Bestand (wie in
    ``test_filters.py``).
    """
    page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
    page.select_option("select[name='document_type']", label="Kontakt")
    page.wait_for_load_state("domcontentloaded")
    page.fill("input[name='occurred_at']", "2026-03-20T10:00")

    autocomplete = page.locator("input[placeholder='Pseudonym eingeben...']")
    autocomplete.fill("Stern")
    page.locator("button:has-text('Stern-42')").wait_for(state="visible", timeout=5000)
    page.locator("button:has-text('Stern-42')").click()

    page.locator("#event-submit-btn").click()
    page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/$"), timeout=15000)
    page.wait_for_load_state("domcontentloaded")


class TestKontextKopf:
    """Der Orientierungs-Kopf auf der schmalsten getesteten Breite (Konzept #1802 §3.10).

    375 px ist die Messlatte, nicht 412: wer dort besteht, besteht auf 412
    erst recht. Gemessen wird das, was das Konzept zusagt — Sichtbarkeit,
    kein horizontaler Überlauf, 44 px Trefferfläche.
    """

    def test_kontext_kopf_survives_375px(self, mobile_authenticated_page, base_url):
        page = mobile_authenticated_page
        _create_event_with_client(page, base_url)

        back_link = page.locator("[data-testid='back-link']")
        expect(back_link).to_be_visible()
        expect(page.locator("main#main-content h1")).to_be_visible()
        expect(page.locator("[data-testid='context-badge-client']")).to_be_visible()

        # Touch: 44 px Trefferfläche (WCAG 2.5.5 / Konzept 3.10).
        box = back_link.bounding_box()
        assert box is not None and box["height"] >= 44, f"Rücklink nur {box} hoch"

        # Kein horizontaler Überlauf: der Kopf darf die Seite nicht breiter machen.
        # +1 px Toleranz gegen subpixel-Rundung bei device_scale_factor=2.
        scroll_width = page.evaluate("() => document.documentElement.scrollWidth")
        assert scroll_width <= 376, f"Horizontaler Überlauf: scrollWidth={scroll_width} bei 375px Viewport"

    def test_reihenfolge_ruecklink_h1_badges_bei_1280px(self, authenticated_page, base_url):
        """Reihenfolge laut Konzept 3.1: Rücklink, H1, Badges — von oben nach unten."""
        page = authenticated_page
        page.set_viewport_size({"width": 1280, "height": 800})
        _create_event_with_client(page, base_url)

        oben = {
            name: page.locator(selector).bounding_box()["y"]
            for name, selector in (
                ("back", "[data-testid='back-link']"),
                ("h1", "main#main-content h1"),
                ("badge", "[data-testid='context-badge-client']"),
            )
        }

        assert oben["back"] < oben["h1"] < oben["badge"], f"Reihenfolge im Kontext-Kopf verletzt: {oben}"
