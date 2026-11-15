"""E2E-Tests: Doku-Typ-Filter bleibt bei Zeitfilter-Wechsel erhalten (Refs #298, Refs #303)."""

import re

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import expect

from ._helpers import wait_until_htmx_bound

pytestmark = pytest.mark.e2e

FEED_PARTIAL = "/partials/zeitstrom/feed/"


def _record_feed_responses(page):
    """Collect status, URL and transition headers of every feed partial answer.

    A shift change that never gets a 200 answer is ambiguous without this: the server may have
    refused the transition (409), the answer may have been late, or the request may not have left.
    """
    seen = []

    def note(response):
        if FEED_PARTIAL not in response.url:
            return
        sent = sorted(name for name in response.request.headers if name.startswith("x-zeitstrom"))
        seen.append(f"{response.status} {response.request.resource_type} {response.url} sent={sent}")

    page.on("response", note)
    return seen


class TestDocTypeFilterPreservedOnTimeFilterSwitch:
    """Bug #298: Zeitfilter-Tabs verlieren den doc_type-Parameter."""

    def test_doc_type_preserved_when_switching_time_filter(self, staff_page, base_url):
        """Changing the shift preserves the document type without a page navigation."""
        page = staff_page
        feed_responses = _record_feed_responses(page)

        # Timeline öffnen (Schicht-Tabs + Doc-Type-Filter)
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        # Sicherstellen, dass das Doc-Type-Dropdown vorhanden ist
        doc_type_select = page.locator("#filter-doc-type")
        doc_type_select.wait_for(state="visible", timeout=5000)

        # Einen spezifischen Dokumentationstyp auswählen (nicht "Alle")
        # "Kontakt" ist im Seed immer vorhanden
        with page.expect_response(
            lambda response: (
                response.request.resource_type == "xhr"
                and response.status == 200
                and "/partials/zeitstrom/feed/" in response.url
                and "feed_only=1" in response.url
            ),
            timeout=5000,
        ):
            doc_type_select.select_option(label="Kontakt")
        # The response is seen before htmx has swapped it in. ``hx-indicator`` puts the
        # ``htmx-request`` class on the spinner, not on the select, so wait for the spinner and
        # for the swap and settle classes of the target; only then is the swap complete.
        page.wait_for_function(
            "() => !document.querySelector('#zeitstrom-doc-type-spinner.htmx-request')"
            " && !document.querySelector("
            "'#feed-list.htmx-swapping, #feed-list.htmx-settling, #feed-list .htmx-added')",
            timeout=5000,
        )

        # Aktuellen Wert des Dropdowns prüfen
        selected_value = doc_type_select.input_value()
        assert selected_value != "", "Doc-Type-Filter sollte einen Wert haben nach Auswahl"

        # The single native shift control replaces the old row of buttons.
        shift = page.get_by_test_id("zeitstrom-shift-select")
        expect(shift).to_be_visible()
        options = shift.locator("option").evaluate_all("els => els.map(el => el.value)")
        assert len(options) >= 3
        target_filter = next(value for value in options if value != shift.input_value() and value != "all")
        wait_until_htmx_bound(page, shift)

        # Erst ab dem abgeschlossenen Dokumenttyp-Swap darf eine Navigation
        # dem Zeitfilterwechsel zugerechnet werden.
        page.evaluate("window.__zeitstromFilterNavigationMarker = 'alive'")

        # Der Tab-Wechsel ist ein HTMX-Partial, keine vollständige Navigation.
        # Die Response explizit abwarten, damit der anschließende Live-Watch-Check
        # nicht gegen einen noch laufenden Feed-Swap konkurriert (Refs #1928).
        try:
            with page.expect_response(
                lambda response: (
                    response.request.resource_type == "xhr"
                    and response.status == 200
                    and FEED_PARTIAL in response.url
                    and f"time_filter={target_filter}" in response.url
                ),
                timeout=5000,
            ):
                shift.select_option(target_filter)
        except PlaywrightTimeoutError as error:
            raise AssertionError(
                f"No 200 answer for the shift change to {target_filter!r}; feed answers seen: {feed_responses}"
            ) from error

        page.evaluate("window.zeitstromWatch.checkNow()")
        assert page.evaluate("window.__zeitstromFilterNavigationMarker") == "alive"
        page.wait_for_function(
            f"() => document.querySelector('#filter-doc-type')?.value === '{selected_value}'",
            timeout=5000,
        )

        expect(shift).to_have_value(target_filter)
        expect(doc_type_select).to_have_value(selected_value)
        expect(page.get_by_test_id("zeitstrom-contacts-list")).to_have_attribute(
            "href",
            re.compile(
                rf"doc_type={selected_value}.*time_filter={target_filter}|time_filter={target_filter}.*doc_type={selected_value}"
            ),
        )
