"""E2E-Tests: Doku-Typ-Filter bleibt bei Zeitfilter-Wechsel erhalten (Refs #298, Refs #303)."""

import re
import time

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import expect

from ._helpers import wait_until_htmx_bound

pytestmark = pytest.mark.e2e

FEED_PARTIAL = "/partials/zeitstrom/feed/"


class _FeedTraffic:
    """Timestamped log of everything the page does with the feed partial."""

    def __init__(self):
        self._start = time.monotonic()
        self._events = []

    def _elapsed_ms(self):
        return round((time.monotonic() - self._start) * 1000)

    def add(self, kind, detail):
        self._events.append(f"+{self._elapsed_ms()}ms {kind} {detail}")

    def report(self):
        """Return the log as one text, ending with the time of the report itself."""
        events = self._events or ["no feed request seen"]
        return "; ".join([*events, f"reported at +{self._elapsed_ms()}ms"])


def _record_feed_responses(page):
    """Log request, answer and end of every feed partial exchange with a timestamp.

    A shift change that never gets a 200 answer is ambiguous without this: the server may have
    refused the transition (409), the answer may have been late, or the request may not have left.
    Never sent: no ``request`` line. Held or late: ``request`` without ``finished``/``response``,
    or an answer stamped after the wait ran out. Cancelled: ``failed`` with the browser's reason.
    Only the names of the ``x-zeitstrom*`` headers are logged, never their values.
    """
    traffic = _FeedTraffic()

    def describe(request):
        sent = sorted(name for name in request.headers if name.startswith("x-zeitstrom"))
        return f"{request.method} {request.resource_type} {request.url} sent={sent}"

    def on_request(request):
        if FEED_PARTIAL in request.url:
            traffic.add("request", describe(request))

    def on_response(response):
        if FEED_PARTIAL in response.url:
            traffic.add("response", f"{response.status} {describe(response.request)}")

    def on_finished(request):
        if FEED_PARTIAL in request.url:
            traffic.add("finished", request.url)

    def on_failed(request):
        if FEED_PARTIAL in request.url:
            traffic.add("failed", f"{request.failure} {request.url}")

    page.on("request", on_request)
    page.on("response", on_response)
    page.on("requestfinished", on_finished)
    page.on("requestfailed", on_failed)
    return traffic


class TestDocTypeFilterPreservedOnTimeFilterSwitch:
    """Bug #298: Zeitfilter-Tabs verlieren den doc_type-Parameter."""

    def test_doc_type_preserved_when_switching_time_filter(self, staff_page, base_url):
        """Changing the shift preserves the document type without a page navigation."""
        page = staff_page
        feed_traffic = _record_feed_responses(page)

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
                f"No 200 answer for the shift change to {target_filter!r}; feed traffic: {feed_traffic.report()}"
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
