"""E2E-Tests für Stream F: Frontend-Verbesserungen."""

import pytest


@pytest.mark.e2e
class TestUnifiedLayout:
    """F.1: Content muss auf Desktop und Mobile sichtbar sein."""

    def test_desktop_content_visible(self, authenticated_page, base_url):
        page = authenticated_page
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{base_url}/")
        assert page.locator("main#main-content").is_visible()
        assert page.locator("text=Zeitstrom").first.is_visible()

    def test_desktop_clients_visible(self, authenticated_page, base_url):
        page = authenticated_page
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{base_url}/clients/")
        assert page.locator("h1:has-text('Klientel')").is_visible()
        assert page.locator("table").is_visible()

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
        # Mobile card layout should be visible
        assert page.locator("main#main-content .sm\\:hidden a").first.is_visible()

    def test_mobile_statistics_visible(self, authenticated_page, base_url):
        page = authenticated_page
        page.set_viewport_size({"width": 375, "height": 812})
        page.goto(f"{base_url}/statistics/")
        assert page.locator("text=Gesamtkontakte").is_visible()


@pytest.mark.e2e
class TestMobileNav:
    """F.1: Mobile Bottom-Nav mit Mehr-Dropdown."""

    def test_more_dropdown(self, authenticated_page, base_url):
        page = authenticated_page
        page.set_viewport_size({"width": 375, "height": 812})
        page.goto(f"{base_url}/")

        # Mehr-Button should be visible
        more_btn = page.locator("button:has-text('Mehr')")
        assert more_btn.is_visible()

        # Click opens dropdown with Aufgaben, Klientel
        more_btn.click()
        page.wait_for_timeout(300)
        assert page.locator("nav[aria-label='Mobile Navigation'] a:has-text('Aufgaben')").is_visible()
        assert page.locator("nav[aria-label='Mobile Navigation'] a[href='/clients/']").is_visible()

    def test_staff_no_statistik_in_more(self, base_url, browser):
        """Staff-User sieht kein Statistik im Mehr-Menü."""
        context = browser.new_context()
        page = context.new_page()
        page.goto(f"{base_url}/login/")
        page.fill('input[name="username"]', "miriam")
        page.fill('input[name="password"]', "anlaufstelle2026")
        page.click('button[type="submit"]')
        page.wait_for_url(f"{base_url}/")

        page.set_viewport_size({"width": 375, "height": 812})
        page.goto(f"{base_url}/")

        more_btn = page.locator("button:has-text('Mehr')")
        more_btn.click()
        page.wait_for_timeout(300)

        assert page.locator("nav[aria-label='Mobile Navigation'] a:has-text('Statistik')").count() == 0
        context.close()


@pytest.mark.e2e
class TestAccessibility:
    """F.3: WCAG 2.1 AA Verbesserungen."""

    def test_skip_to_content_link(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/")
        skip_link = page.locator("a[href='#main-content']")
        assert skip_link.count() == 1
        assert "Zum Inhalt springen" in skip_link.inner_text()


@pytest.mark.e2e
class TestMobileCards:
    """F.4: Card-Layout auf Mobile."""

    def test_clients_card_layout_mobile(self, authenticated_page, base_url):
        page = authenticated_page
        page.set_viewport_size({"width": 375, "height": 812})
        page.goto(f"{base_url}/clients/")

        # Table should be hidden on mobile
        assert not page.locator(".hidden.sm\\:block table").is_visible()
        # Card layout should be visible
        assert page.locator(".sm\\:hidden").first.is_visible()


@pytest.mark.e2e
class TestPWA:
    """F.5: PWA-Setup."""

    def test_manifest_link_in_head(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/")
        manifest = page.locator("link[rel='manifest']")
        assert manifest.count() == 1
        assert "manifest.json" in manifest.get_attribute("href")

    def test_sw_registration_script(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/")
        html = page.content()
        assert "sw-register.js" in html

    def test_sw_endpoint(self, authenticated_page, base_url):
        page = authenticated_page
        response = page.goto(f"{base_url}/sw.js")
        assert response.status == 200
        assert "javascript" in response.headers.get("content-type", "")


@pytest.mark.e2e
class TestPrintStyles:
    """F.6: Druckansicht."""

    def test_nav_hidden_in_print(self, authenticated_page, base_url):
        page = authenticated_page
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{base_url}/")
        page.emulate_media(media="print")
        nav = page.locator("nav[aria-label='Hauptnavigation']")
        assert not nav.is_visible()
