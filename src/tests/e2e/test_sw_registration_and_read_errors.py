"""Browser checks for the offline claim when the service worker registration fails or is late,
and for the visible notices when local counters cannot be read."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from tests.e2e._selectors import find_first_client_link, open_desktop_overflow

from ._helpers import SEED_PASSWORD, wait_for_active_service_worker

pytestmark = pytest.mark.e2e

UNAVAILABLE = "Offline-Seiten sind auf diesem Gerät derzeit nicht verfügbar"
AVAILABLE = "Person ist jetzt offline verfügbar."


class _SwScriptRoute:
    """Answers requests for ``/sw.js`` as ``ok``, ``fail`` (HTTP 500) or ``hang`` (never answered)."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.hits = 0
        self.parked: list = []

    def __call__(self, route) -> None:
        self.hits += 1
        if self.mode == "fail":
            route.fulfill(status=500, body="boom", content_type="text/plain")
        elif self.mode == "hang":
            self.parked.append(route)
        else:
            route.continue_()


def _open_first_client(browser, base_url, sw_route):
    context = browser.new_context(locale="de-DE")
    context.route("**/sw.js", sw_route)
    page = context.new_page()
    page.set_default_timeout(30000)
    page.goto(f"{base_url}/login/")
    page.fill('input[name="username"]', "miriam")
    page.fill('input[name="password"]', SEED_PASSWORD)
    page.click('#login-form button[type="submit"]')
    page.wait_for_url(lambda url: "/login/" not in url, timeout=10000)
    page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")
    find_first_client_link(page).click()
    page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))
    return context, page


def _toggle_offline(page) -> None:
    # The menu of the previous toggle may still be fading out; opening it meanwhile would make the
    # visible-panel check pass on the closing panel.
    expect(page.locator("[data-testid='desktop-overflow-menu'] + div")).to_be_hidden()
    open_desktop_overflow(page, "client").locator('[data-testid="take-offline-btn"]').click()


def _worker_status(page) -> str:
    return page.evaluate("() => window.offlineWorker.status()")


class TestWorkerRegistration:
    def test_failed_registration_withdraws_the_offline_claim_and_a_later_attempt_succeeds(self, browser, base_url):
        sw_route = _SwScriptRoute("fail")
        context, page = _open_first_client(browser, base_url, sw_route)
        try:
            page.wait_for_function("() => window.offlineWorker && window.offlineWorker.status() === 'failed'")
            toast = page.locator('[data-testid="offline-toast"]')

            _toggle_offline(page)
            expect(toast).to_contain_text(UNAVAILABLE)
            expect(toast).to_have_class(re.compile(r"border-red"))
            expect(toast).not_to_contain_text(AVAILABLE)
            # The person itself is stored locally and stays marked.
            expect(page.locator('[data-testid="offline-available-badge"]')).to_be_visible()

            # Block lifted, page reloaded: the worker registers and the claim is made again.
            sw_route.mode = "ok"
            page.reload(wait_until="domcontentloaded")
            wait_for_active_service_worker(page)
            _toggle_offline(page)  # removes the local copy
            expect(page.locator('[data-testid="offline-available-badge"]')).to_be_hidden()
            _toggle_offline(page)
            expect(toast).to_contain_text(AVAILABLE)
            expect(toast).not_to_have_class(re.compile(r"border-red"))
        finally:
            context.close()

    def test_a_hanging_retry_is_bounded_and_shared_between_clicks(self, browser, base_url):
        sw_route = _SwScriptRoute("fail")
        context, page = _open_first_client(browser, base_url, sw_route)
        try:
            page.wait_for_function("() => window.offlineWorker && window.offlineWorker.status() === 'failed'")
            sw_route.mode = "hang"
            hits_before = sw_route.hits
            toast = page.locator('[data-testid="offline-toast"]')

            _toggle_offline(page)
            # The wait for the retry is bounded: the notice appears although the attempt never settles.
            expect(toast).to_contain_text(UNAVAILABLE, timeout=15000)
            assert sw_route.hits == hits_before + 1, "exactly one new registration attempt"

            _toggle_offline(page)  # remove
            expect(page.locator('[data-testid="offline-available-badge"]')).to_be_hidden()
            _toggle_offline(page)  # take again while the first attempt is still open
            expect(toast).to_contain_text(UNAVAILABLE, timeout=15000)
            assert sw_route.hits == hits_before + 1, "the open attempt is shared, not repeated"
        finally:
            context.close()

    def test_a_claim_made_while_the_registration_is_pending_is_withdrawn_when_it_fails(self, browser, base_url):
        sw_route = _SwScriptRoute("hang")
        context, page = _open_first_client(browser, base_url, sw_route)
        try:
            toast = page.locator('[data-testid="offline-toast"]')
            assert _worker_status(page) == "pending"
            _toggle_offline(page)
            expect(toast).to_contain_text(AVAILABLE)

            # The attempt fails late: the promise is taken back, visibly and in red.
            sw_route.mode = "fail"
            for parked in sw_route.parked:
                parked.fulfill(status=500, body="boom", content_type="text/plain")
            expect(toast).to_contain_text(UNAVAILABLE, timeout=15000)
            expect(toast).to_have_class(re.compile(r"border-red"))
            expect(page.locator('[data-testid="offline-available-badge"]')).to_be_visible()
        finally:
            context.close()


_FAIL_IDB_READS = """
(() => {
    const original = IDBDatabase.prototype.transaction;
    IDBDatabase.prototype.transaction = function (...args) {
        if (window.__failIdbReads) throw new DOMException('injected read failure', 'UnknownError');
        return original.apply(this, args);
    };
})();
"""


def _tally_counts(page) -> list[str]:
    """Texts of the confirmed counts in the tally panel, in row order."""
    return page.locator('#tally-panel [data-testid^="tally-count-"]').all_inner_texts()


class TestCounterReadErrors:
    def _staff_page(self, browser, base_url):
        context = browser.new_context(locale="de-DE")
        context.add_init_script(_FAIL_IDB_READS)
        page = context.new_page()
        page.set_default_timeout(30000)
        page.goto(f"{base_url}/login/")
        page.fill('input[name="username"]', "miriam")
        page.fill('input[name="password"]', SEED_PASSWORD)
        page.click('#login-form button[type="submit"]')
        page.wait_for_url(lambda url: "/login/" not in url, timeout=10000)
        page.goto(f"{base_url}/start/", wait_until="domcontentloaded")
        return context, page

    def test_unreadable_change_counter_shows_the_unclear_banner_and_recovers_on_retry(self, browser, base_url):
        context, page = self._staff_page(browser, base_url)
        try:
            banner = page.locator('[data-testid="queue-unknown-banner"]')
            expect(banner).to_be_hidden()
            page.wait_for_function("() => !!window.offlineQueue && !!window.offlineQueue.refreshCount")
            page.evaluate("() => { window.__failIdbReads = true; return window.offlineQueue.refreshCount(); }")
            expect(banner).to_be_visible()
            expect(banner).to_contain_text("Stand der wartenden Änderungen unklar.")
            expect(page.locator('[data-testid="offline-status-announcer"]')).to_contain_text("unklar")
            retry = banner.get_by_role("button", name="Erneut versuchen")
            expect(retry).to_have_count(1)

            page.evaluate("() => { window.__failIdbReads = false; }")
            retry.click()
            expect(banner).to_be_hidden()
        finally:
            context.close()

    def test_unreadable_local_contacts_show_an_alert_in_the_tally_panel_and_recover_on_retry(self, browser, base_url):
        context, page = self._staff_page(browser, base_url)
        try:
            panel = page.locator("#tally-panel")
            expect(panel).to_have_count(1)
            notice = panel.locator("[data-offline-tally-unknown]")
            expect(notice).to_have_count(0)
            counts_before = _tally_counts(page)
            assert counts_before, "the seeded tally panel must show at least one count"
            page.evaluate("() => { window.__failIdbReads = true; }")
            page.evaluate("() => window.dispatchEvent(new CustomEvent('zeitstrom:tally-counts-updated'))")
            expect(notice).to_have_count(1)
            expect(notice).to_have_attribute("role", "alert")
            expect(notice).to_contain_text("Lokal wartende Kontakte konnten nicht gelesen werden")
            # The confirmed server counts stay as shown: no zeros, no made-up local numbers.
            assert _tally_counts(page) == counts_before
            # A repeated failure does not stack a second notice.
            page.evaluate("() => window.dispatchEvent(new CustomEvent('zeitstrom:tally-counts-updated'))")
            page.wait_for_timeout(300)
            expect(notice).to_have_count(1)
            assert _tally_counts(page) == counts_before

            page.evaluate("() => { window.__failIdbReads = false; }")
            notice.get_by_role("button", name="Erneut versuchen").click()
            expect(notice).to_have_count(0)
            assert _tally_counts(page) == counts_before
            # The retry button vanished with the notice: the focus stays inside the panel.
            assert page.evaluate("() => !!document.activeElement.closest('#tally-panel')")
        finally:
            context.close()

    def test_unreadable_local_contacts_at_page_load_show_the_alert_and_keep_the_counts(self, browser, base_url):
        context, page = self._staff_page(browser, base_url)
        try:
            counts_before = _tally_counts(page)
            assert counts_before, "the seeded tally panel must show at least one count"
            expect(page.locator("#tally-panel [data-offline-tally-unknown]")).to_have_count(0)

            # Reload with every storage read failing from the first script on: the initial refresh
            # of the panel runs into the error without any trigger from the test. The failure is
            # tied to a storage flag so that it is in place before the first script of the reload.
            context.add_init_script("window.__failIdbReads = localStorage.getItem('failIdbReads') === '1';")
            page.evaluate("() => localStorage.setItem('failIdbReads', '1')")
            page_errors: list[str] = []
            failed_attempts: list[str] = []
            page.on(
                "console",
                lambda message: (
                    failed_attempts.append(message.text)
                    if "[offline-tally] local state unavailable" in message.text
                    else None
                ),
            )
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on(
                "console",
                lambda message: (
                    page_errors.append(message.text)
                    if message.type == "error" and "TypeError" in message.text
                    else None
                ),
            )
            page.reload(wait_until="domcontentloaded")
            notice = page.locator("#tally-panel [data-offline-tally-unknown]")
            expect(notice).to_have_count(1)
            expect(notice).to_have_attribute("role", "alert")
            assert _tally_counts(page) == counts_before

            # Other triggers also refresh the panel in the background (queue counter, sync, htmx
            # swaps) and would recover the notice without the button. Muting these leaves the button
            # as the trigger of the recovery below.
            page.evaluate(
                """() => {
                    for (const type of ['offline-queue-count', 'offline-sync-finished',
                                        'zeitstrom:tally-counts-updated', 'htmx:afterSwap', 'online']) {
                        window.addEventListener(type, (event) => event.stopImmediatePropagation(), true);
                    }
                }"""
            )

            # While the failure persists, the retry keeps the single notice.
            attempts_before = len(failed_attempts)
            notice.get_by_role("button", name="Erneut versuchen").click()
            # A refresh of the panel runs into the failure again and logs it; the notice must come back
            # or stay. Which trigger ran it is not told here, the final step pins the button itself.
            for _ in range(100):
                if len(failed_attempts) > attempts_before:
                    break
                page.wait_for_timeout(100)
            assert len(failed_attempts) > attempts_before, "the retry click did not run a new refresh attempt"
            expect(notice).to_have_count(1)
            assert _tally_counts(page) == counts_before

            # The failure is lifted: the retry button of the notice opens the store again on this
            # very page, without a reload. Lifting the failure and the click happen in one task.
            page.evaluate(
                """() => {
                    window.__failIdbReads = false;
                    document.querySelector('#tally-panel [data-offline-tally-unknown] button').click();
                }"""
            )
            expect(notice).to_have_count(0)
            assert _tally_counts(page) == counts_before
            assert not page_errors, page_errors
        finally:
            context.close()
