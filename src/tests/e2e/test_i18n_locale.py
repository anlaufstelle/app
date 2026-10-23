"""E2E coverage for visit and account language selection (Refs #2609, #928)."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from ._profiles import require_demo


@pytest.mark.e2e
class TestAnonymousLocale:
    """Use fresh cookie contexts for direct links, switches and errors."""

    def test_login_renders_german_by_default(self, browser, base_url):
        context = browser.new_context(locale="en-US")
        try:
            page = context.new_page()
            page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
            expect(page.locator("html")).to_have_attribute("lang", "de")
            expect(page.locator("#login-form button[type='submit']")).to_have_text("Anmelden")
        finally:
            context.close()

    @pytest.mark.parametrize("width", [390, 1280])
    def test_login_query_switch_and_error(self, browser, base_url, width):
        context = browser.new_context(viewport={"width": width, "height": 844})
        try:
            page = context.new_page()
            page.goto(f"{base_url}/login/?lang=en&next=%2Fclients%2F", wait_until="domcontentloaded")
            expect(page.locator("html")).to_have_attribute("lang", "en")
            expect(page.locator("button[value='en']")).to_have_attribute("aria-pressed", "true")
            german = page.locator("button[value='de']")
            german.focus()
            german.press("Space")
            expect(page.locator("html")).to_have_attribute("lang", "de")
            assert "next=%2Fclients%2F" in page.url
            page.locator("button[value='en']").click()
            expect(page.locator("html")).to_have_attribute("lang", "en")
            page.locator("#id_username").fill("missing-language-test-user")
            page.locator("#id_password").fill("incorrect-language-test-password")
            page.locator("#login-form button[type='submit']").click()
            expect(page.locator("#login-errors")).to_contain_text("Please enter a correct")
            expect(page.locator("html")).to_have_attribute("lang", "en")
            expect(page.locator("[data-testid='app-version']")).to_have_count(0)
        finally:
            context.close()

    def test_demo_query_switch_error_and_start(self, browser, base_url):
        """Requires E2E_DEMO_MODE=1 and E2E_SEED_SCALE=demo."""
        context = browser.new_context(viewport={"width": 390, "height": 844})
        try:
            page = context.new_page()
            response = page.goto(f"{base_url}/demo/?lang=en&bereich=kontaktladen", wait_until="domcontentloaded")
            require_demo(lambda: response is not None and response.status == 200)
            expect(page.locator("html")).to_have_attribute("lang", "en")
            page.locator("button[value='de']").click()
            expect(page.locator("html")).to_have_attribute("lang", "de")
            assert "bereich=kontaktladen" in page.url
            page.locator("button[value='en']").click()
            expect(page.locator("html")).to_have_attribute("lang", "en")
            token = page.locator("input[name='csrfmiddlewaretoken']").first.input_value()
            failure = page.request.post(
                f"{base_url}/demo/start/",
                form={"scenario": "invalid", "csrfmiddlewaretoken": token},
                max_redirects=0,
            )
            assert failure.status == 302
            page.goto(base_url + failure.headers["location"], wait_until="domcontentloaded")
            expect(page.locator("html")).to_have_attribute("lang", "en")
            expect(page.locator("[role='alert']").first).to_contain_text("not available")
            expect(page.locator("[data-testid='app-version']")).to_have_count(0)
            start = page.locator("[data-testid='demo-scenario-start']").first
            expect(start).to_be_visible()
            start.click()
            page.wait_for_url(lambda url: "/demo/" not in url, wait_until="domcontentloaded")
            expect(page.locator("html")).to_have_attribute("lang", "en")
        finally:
            context.close()


@pytest.mark.e2e
class TestAuthenticatedLocaleSwitch:
    """Eingeloggte User können per Sprach-Switcher die Locale ändern.

    Der Switcher (POST zu ``/i18n/setlang/``, View ``set_user_language``)
    speichert die Auswahl als ``User.preferred_language`` und aktiviert sie
    via ``UserLanguageMiddleware`` ab dem nächsten Request.
    """

    def test_switch_to_english_persists(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")

        # Sprach-Switcher hat DE/EN-Buttons; EN klicken.
        page.locator("form[action='/i18n/setlang/'] button[value='en']").first.click()
        page.wait_for_load_state("domcontentloaded")

        # Logout-Button im Top-Bar zeigt jetzt englischen Text.
        assert page.locator("button:has-text('Log out'),button:has-text('Sign out')").first.is_visible(), (
            "Nach Sprach-Wechsel auf EN muss der Abmelden-Button englisch rendern."
        )

        # Verifikation persistiert: neuer Request → weiterhin EN.
        page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")
        assert page.locator("button:has-text('Log out'),button:has-text('Sign out')").first.is_visible()

        # Zurückwechseln auf DE, damit nachfolgende Tests in derselben Session
        # nicht auf englischer Locale starten (Storage-State ist session-scoped).
        page.locator("form[action='/i18n/setlang/'] button[value='de']").first.click()
        page.wait_for_load_state("domcontentloaded")
        assert page.locator("button:has-text('Abmelden')").first.is_visible()
