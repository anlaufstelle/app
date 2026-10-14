"""E2E: Offline-key notice of the demo (DEMO #2607), complement to the legal-page cases.

Anonymous visitors never see the red key notice, not even on the login page. A
signed-in demo session whose offline key is missing (interrupted start) keeps
seeing it on the next page.

Runs only in the demo profile (``E2E_DEMO_MODE=1 E2E_SEED_SCALE=demo``).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from ._profiles import require_demo
from ._selectors import demo_scenario_start

pytestmark = pytest.mark.e2e


@pytest.fixture
def demo_profile(base_url, browser):
    def probe():
        context = browser.new_context(locale="de-DE")
        try:
            response = context.new_page().goto(f"{base_url}/demo/")
            return response is not None and response.status == 200
        finally:
            context.close()

    require_demo(probe)


@pytest.mark.usefixtures("demo_profile")
def test_anonymous_login_page_has_no_key_notice(base_url, browser):
    context = browser.new_context(locale="de-DE")
    try:
        page = context.new_page()
        response = page.goto(f"{base_url}/login/")
        assert response is not None and response.status == 200
        # The login page is a standalone page without the demo banner. Settle on the concrete state: the form
        # is rendered and the key hydration of the page script (crypto_session.ready) has finished.
        expect(page.locator("#login-form")).to_be_visible()
        assert page.evaluate("() => window.crypto_session.ready().then(() => true)") is True
        assert page.locator("[data-demo-offline-error]").count() == 0
        assert page.locator('[role="alert"]').count() == 0
    finally:
        context.close()


@pytest.mark.usefixtures("demo_profile")
def test_signed_in_session_without_offline_key_keeps_the_notice(base_url, browser):
    context = browser.new_context(locale="de-DE")
    try:
        page = context.new_page()
        page.goto(f"{base_url}/demo/")
        demo_scenario_start(page).click()
        page.wait_for_url(lambda url: "/demo/" not in url, timeout=15000)
        page.locator("[data-testid='demo-banner']").wait_for()
        notice = page.locator("[data-demo-offline-error]")

        # Regular start: the key is derived, the notice stays hidden.
        page.wait_for_function("() => window.crypto_session && window.crypto_session.hasSessionKey()", timeout=15000)
        expect(notice).to_be_hidden()

        # Interrupted start: the key is gone; the next page reports it in the signed-in session.
        page.evaluate("() => window.crypto_session.clearSessionKey()")
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        expect(page.locator("[data-demo-offline-error]")).to_be_visible(timeout=15000)
        assert page.locator("[data-demo-offline-error]").get_attribute("role") == "alert"
    finally:
        context.close()
