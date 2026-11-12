"""E2E-Tests: Doku-Typ-Filter bleibt bei Zeitfilter-Wechsel erhalten (Refs #298, Refs #303)."""

import re

import pytest
from playwright.sync_api import expect

from ._helpers import wait_until_htmx_bound

pytestmark = pytest.mark.e2e


class TestDocTypeFilterPreservedOnTimeFilterSwitch:
    """Bug #298: Zeitfilter-Tabs verlieren den doc_type-Parameter."""

    def test_doc_type_preserved_when_switching_time_filter(self, staff_page, base_url):
        """Changing the shift preserves the document type without a page navigation."""
        page = staff_page

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
        page.wait_for_function(
            "() => !document.querySelector('#filter-doc-type')?.classList.contains('htmx-request')",
            timeout=5000,
        )
        # Dropdown-Wert wird serverseitig gerendert — auf einen nicht-leeren
        # Wert warten (signalisiert abgeschlossenen HTMX-Swap).
        page.wait_for_function(
            "() => document.querySelector('#filter-doc-type')?.value !== ''",
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
        with page.expect_response(
            lambda response: (
                response.request.resource_type == "xhr"
                and response.status == 200
                and "/partials/zeitstrom/feed/" in response.url
                and f"time_filter={target_filter}" in response.url
            ),
            timeout=5000,
        ):
            shift.select_option(target_filter)

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
