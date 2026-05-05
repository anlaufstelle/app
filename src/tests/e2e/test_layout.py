"""E2E-Tests: Layout — Unified Desktop/Mobile, Accessibility, Print."""

import pytest

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
