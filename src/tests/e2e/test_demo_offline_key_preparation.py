"""E2E: Offline-key preparation of the demo start (DEMO E2: hint, progress, completion/failure).

Reproduction condition: the key-salt request of the start script is held (progress)
or answered with HTTP 500 (failure). Runs only in the demo profile
(``E2E_DEMO_MODE=1 E2E_SEED_SCALE=demo``); without it the cases skip with a reason.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from ._profiles import require_demo
from ._selectors import demo_scenario_start

pytestmark = pytest.mark.e2e

SALT_ROUTE = "**/auth/offline-key-salt/**"


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


def _start_scenario(page, base_url):
    page.goto(f"{base_url}/demo/", wait_until="domcontentloaded")
    demo_scenario_start(page).click()
    page.wait_for_url(lambda url: "/demo/" not in url, timeout=15000)
    page.locator("[data-testid='demo-banner']").wait_for()


@pytest.mark.usefixtures("demo_profile")
class TestDemoKeyPreparation:
    def test_hint_while_preparing_then_completion(self, base_url, browser):
        context = browser.new_context(locale="de-DE")
        try:
            page = context.new_page()
            held: list = []
            state = {"release": False}

            def hold_until_released(route):
                if state["release"]:
                    route.continue_()
                else:
                    held.append(route)

            page.route(SALT_ROUTE, hold_until_released)
            page.goto(f"{base_url}/demo/", wait_until="domcontentloaded")
            demo_scenario_start(page).click()
            pending = page.locator("[data-demo-offline-pending]")
            expect(pending).to_be_visible(timeout=15000)
            assert pending.get_attribute("role") == "status" and pending.get_attribute("aria-live") == "polite"
            expect(pending).to_contain_text("Offline-Schlüssel wird vorbereitet")
            expect(page.locator("[data-demo-offline-success]")).to_be_hidden()
            assert not page.evaluate("() => window.crypto_session && window.crypto_session.hasSessionKey()")

            state["release"] = True
            for route in held:
                route.continue_()
            success = page.locator("[data-demo-offline-success]")
            expect(success).to_be_visible(timeout=15000)
            expect(success).to_have_text("Offline-Schlüssel ist bereit.")
            expect(pending).to_be_hidden()
            page.wait_for_function(
                "() => window.crypto_session && window.crypto_session.hasSessionKey()", timeout=15000
            )
            expect(page.locator("[data-demo-offline-error]")).to_be_hidden()
        finally:
            context.close()

    def test_failed_preparation_shows_alert_and_no_success(self, base_url, browser):
        context = browser.new_context(locale="de-DE")
        try:
            page = context.new_page()
            page.route(SALT_ROUTE, lambda route: route.fulfill(status=500, body="boom"))
            page.goto(f"{base_url}/demo/", wait_until="domcontentloaded")
            demo_scenario_start(page).click()
            error = page.locator("[data-demo-offline-error]")
            expect(error).to_be_visible(timeout=15000)
            assert error.get_attribute("role") == "alert"
            assert error.inner_text().strip(), "the failure hint must carry text"
            expect(page.locator("[data-demo-offline-success]")).to_be_hidden()
            expect(page.locator("[data-demo-offline-pending]")).to_be_hidden()
        finally:
            context.close()
