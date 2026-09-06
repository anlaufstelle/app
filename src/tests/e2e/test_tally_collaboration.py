"""Playwright checks for collaborative and progressively enhanced tallies.

The scenarios in this file were derived from the manual browser check for
#1911/#1913/#1916.  They deliberately use two independent staff contexts for
the live-update path and a JavaScript-disabled context for the POST/Redirect
fallbacks.
"""

import re
from urllib.parse import urljoin

import pytest
from playwright.sync_api import expect

from ._helpers import wait_until_htmx_bound

pytestmark = pytest.mark.e2e


def _row(page, document_type):
    """Return the live tally row for a seeded document type."""
    return page.locator('[data-testid^="tally-row-"]').filter(has_text=re.compile(rf"^\s*{re.escape(document_type)}\b"))


def _count(row):
    return row.locator('[data-testid^="tally-count-"]')


def _feed_event_ids(page):
    """Return the visible feed event-card IDs, independent of FEED_MAX."""
    return set(
        page.locator('#feed-list button[aria-controls^="event-detail-"]').evaluate_all(
            "els => els.map(el => el.getAttribute('aria-controls'))"
        )
    )


def _feed_cards_outside(page, baseline_ids):
    """Build a live locator for event cards not present in the baseline."""
    exclusions = "".join(f':not([aria-controls="{event_id}"])' for event_id in baseline_ids)
    return page.locator(f'#feed-list button[aria-controls^="event-detail-"]{exclusions}')


class TestCollaborativeTally:
    def test_second_staff_page_updates_without_losing_number_form(self, staff_page, browser, base_url):
        """A's tally changes reach B while B's open 17/4 form stays intact."""
        page_a = staff_page
        page_a.goto(f"{base_url}/", wait_until="domcontentloaded")

        # Use a different seeded staff account and a real login so the test
        # exercises two independent authenticated sessions, not two pages
        # sharing miriam's storage state.
        context_b = browser.new_context(locale="de-DE")
        page_b = context_b.new_page()
        page_b.set_default_timeout(30000)
        page_b.set_default_navigation_timeout(30000)
        try:
            page_b.goto(f"{base_url}/login/", wait_until="domcontentloaded")
            page_b.locator('input[name="username"]').fill("markus")
            page_b.locator('input[name="password"]').fill("anlaufstelle2026")
            page_b.locator('button[type="submit"]').click()
            page_b.wait_for_url(lambda url: "/login/" not in url, timeout=10000)
            page_b.goto(f"{base_url}/", wait_until="domcontentloaded")

            syringe_row_b = _row(page_b, "Spritzentausch")
            syringe_button_b = syringe_row_b.locator('[data-testid^="tally-increment-"]')
            with page_b.expect_response(
                lambda response: response.request.method == "GET" and "/partials/tally/increment/" in response.url
            ):
                syringe_button_b.click()

            syringe_form_b = page_b.locator('[data-testid^="tally-inline-form-"]')
            expect(syringe_form_b).to_be_visible()
            wait_until_htmx_bound(page_b, syringe_form_b)
            syringe_form_b.locator('input[name="ausgabe"]').fill("17")
            syringe_form_b.locator('input[name="rueckgabe"]').fill("4")

            contact_row_a = _row(page_a, "Kontakt")
            contact_button_a = contact_row_a.locator('[data-testid^="tally-increment-"]')
            contact_count_a = _count(contact_row_a)
            count_before = int(contact_count_a.inner_text().strip())
            feed_before_ids = _feed_event_ids(page_b)

            with page_a.expect_response(
                lambda response: response.request.method == "POST" and "/partials/tally/increment/" in response.url
            ):
                contact_button_a.click()

            expect(contact_count_a).to_have_text(str(count_before + 1), timeout=5000)

            contact_row_b = _row(page_b, "Kontakt")
            expect(_count(contact_row_b)).to_have_text(str(count_before + 1), timeout=22000)
            new_feed_cards = _feed_cards_outside(page_b, feed_before_ids)
            expect(new_feed_cards).to_have_count(1, timeout=22000)
            new_event_id = new_feed_cards.get_attribute("aria-controls")
            assert new_event_id and new_event_id not in feed_before_ids
            expect(syringe_form_b.locator('input[name="ausgabe"]')).to_have_value("17")
            expect(syringe_form_b.locator('input[name="rueckgabe"]')).to_have_value("4")

            # Undo exactly the first +1 after B observed it.  B must naturally
            # converge to baseline; the newly observed card must disappear.
            undo_a = contact_row_a.locator('[data-testid="tally-undo"]')
            expect(undo_a).to_be_visible()
            with page_a.expect_response(
                lambda response: response.request.method == "POST" and "/partials/tally/undo/" in response.url
            ):
                undo_a.click()
            expect(contact_count_a).to_have_text(str(count_before), timeout=5000)

            expect(_count(_row(page_b, "Kontakt"))).to_have_text(str(count_before), timeout=22000)
            expect(_feed_cards_outside(page_b, feed_before_ids)).to_have_count(0, timeout=22000)
            assert new_event_id not in _feed_event_ids(page_b)
            expect(syringe_form_b.locator('input[name="ausgabe"]')).to_have_value("17")
            expect(syringe_form_b.locator('input[name="rueckgabe"]')).to_have_value("4")
        finally:
            context_b.close()


class TestTallyWithoutJavaScript:
    def test_contact_increment_is_real_post_redirect(self, browser, base_url, _staff_storage_state):
        """The direct +1 remains usable when the browser executes no JavaScript."""
        context = browser.new_context(storage_state=_staff_storage_state, locale="de-DE", java_script_enabled=False)
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            page.goto(f"{base_url}/", wait_until="domcontentloaded")
            contact_row = _row(page, "Kontakt")
            contact_button = contact_row.locator('[data-testid^="tally-increment-"]')
            before = int(_count(contact_row).inner_text().strip())
            tally_form = contact_button.locator("xpath=ancestor::form")
            expected_return_url = urljoin(base_url, tally_form.locator('input[name="next"]').input_value())

            with (
                page.expect_request(
                    lambda request: request.method == "POST" and "/partials/tally/increment/" in request.url
                ) as request_info,
                page.expect_response(
                    lambda response: response.request.method == "POST" and "/partials/tally/increment/" in response.url
                ) as response_info,
            ):
                contact_button.click()

            assert request_info.value.method == "POST"
            assert 300 <= response_info.value.status < 400
            expect(page).to_have_url(expected_return_url)
            expect(_count(_row(page, "Kontakt"))).to_have_text(str(before + 1))
        finally:
            context.close()

    def test_number_tally_is_full_page_post_redirect(self, browser, base_url, _staff_storage_state):
        """The Spritzentausch number form works as a normal HTML form without JS."""
        context = browser.new_context(storage_state=_staff_storage_state, locale="de-DE", java_script_enabled=False)
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            page.goto(f"{base_url}/", wait_until="domcontentloaded")
            syringe_row = _row(page, "Spritzentausch")
            before = int(_count(syringe_row).inner_text().strip())

            syringe_row.locator('[data-testid^="tally-increment-"]').click()
            form = page.locator('[data-testid^="tally-inline-form-"]')
            expect(form).to_be_visible()
            form.locator('input[name="ausgabe"]').fill("17")
            form.locator('input[name="rueckgabe"]').fill("4")
            expected_return_url = urljoin(base_url, form.locator('input[name="next"]').input_value())

            with (
                page.expect_request(
                    lambda request: request.method == "POST" and "/partials/tally/increment/" in request.url
                ) as request_info,
                page.expect_response(
                    lambda response: response.request.method == "POST" and "/partials/tally/increment/" in response.url
                ) as response_info,
            ):
                form.locator('[data-testid^="tally-submit-"]').click()

            assert request_info.value.method == "POST"
            assert 300 <= response_info.value.status < 400
            expect(page).to_have_url(expected_return_url)
            expect(_count(_row(page, "Spritzentausch"))).to_have_text(str(before + 1))
        finally:
            context.close()
