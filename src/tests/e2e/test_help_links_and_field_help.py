"""E2E: context-help links land on their section; doctype form deep help (Refs #2459, #2460).

Read-only: no object is created or changed. Everything is observed on the seed
document types and on the new-doctype form (never submitted).

Preconditions: default E2E seed, login as the facility admin (``authenticated_page``).
"""

import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

from ._axe import assert_within_baseline, load_baseline, run_axe

pytestmark = [pytest.mark.e2e, pytest.mark.a11y]

BASELINE_PATH = Path(__file__).with_name("a11y_baseline.json")
DOCTYPE_LIST = "/config/document-types/"
DEEP_HELP_FIELDS = (
    "entity_type",
    "sensitivity",
    "system_type",
    "report_key",
    "min_contact_stage",
    "retention_days",
    "tally_enabled",
)


def _open_contact_doctype_detail(page, base_url):
    # Search instead of relying on page one: other specs add document types to the paginated list.
    page.goto(f"{base_url}{DOCTYPE_LIST}?q=Kontakt", wait_until="domcontentloaded")
    page.locator('[data-testid="doctype-detail-link"]').filter(has_text=re.compile(r"^\s*Kontakt\s*$")).click()
    page.wait_for_url("**/config/document-types/*/")


def _open_new_form(page, base_url):
    page.goto(f"{base_url}{DOCTYPE_LIST}new/", wait_until="domcontentloaded")
    expect(page.locator("select#id_entity_type")).to_be_visible(timeout=15000)


def _scan(page, base_url, page_key):
    result = run_axe(page, base_url)
    assert_within_baseline(page_key, result, load_baseline(BASELINE_PATH))


class TestFieldConditionsHelpLink:
    def test_more_link_scrolls_to_the_field_conditions_section(self, authenticated_page, base_url):
        page = authenticated_page
        _open_contact_doctype_detail(page, base_url)

        help_block = page.locator('[data-context-help="document-conditions"]')
        expect(help_block).to_be_visible(timeout=15000)
        help_block.locator("summary").click()
        help_block.get_by_role("link", name="Mehr erfahren").click()

        page.wait_for_url("**/hilfe/verwaltung/#feldbedingungen")
        section = page.locator("#feldbedingungen")
        expect(section).to_be_visible()
        expect(section).to_be_in_viewport()
        expect(section.get_by_role("heading", name="Feldbedingungen (bedingte Sichtbarkeit)")).to_be_visible()
        expect(section).to_contain_text("UND innerhalb einer Gruppe")
        expect(section).to_contain_text("ODER über Gruppen hinweg")
        expect(section).to_contain_text("Pflichtfeld")

    @pytest.mark.parametrize("slug", ("verwaltung", "datenschutz"))
    def test_help_pages_with_sections_pass_axe(self, authenticated_page, base_url, slug):
        page = authenticated_page
        page.goto(f"{base_url}/hilfe/{slug}/", wait_until="domcontentloaded")
        expect(page.locator(f'[data-help-slug="{slug}"]')).to_be_visible(timeout=15000)

        _scan(page, base_url, f"help-{slug}")


class TestDocumentTypeFormDeepHelp:
    def test_deep_help_opens_with_the_keyboard_and_stays_collapsed_by_default(self, authenticated_page, base_url):
        page = authenticated_page
        _open_new_form(page, base_url)

        for field in DEEP_HELP_FIELDS:
            details = page.locator(f'details[data-field-help="{field}"]')
            expect(details).to_be_visible()
            assert details.get_attribute("open") is None, field

        details = page.locator('details[data-field-help="system_type"]')
        details.locator("summary").focus()
        page.keyboard.press("Enter")

        expect(details).to_have_attribute("open", "")
        expect(details).to_contain_text("Folgen:")
        expect(details).to_contain_text("Änderbar:")
        expect(details).to_contain_text("Beispiel:")
        expect(details).to_contain_text("Nach dem Anlegen unveränderbar")

        page.keyboard.press("Enter")
        assert details.get_attribute("open") is None

    def test_deep_help_of_event_only_fields_hides_with_the_field(self, authenticated_page, base_url):
        page = authenticated_page
        _open_new_form(page, base_url)

        page.select_option("select#id_entity_type", "client")

        for field in ("system_type", "report_key", "min_contact_stage", "retention_days", "tally_enabled"):
            expect(page.locator(f'details[data-field-help="{field}"]')).to_be_hidden()
        for field in ("entity_type", "sensitivity"):
            expect(page.locator(f'details[data-field-help="{field}"]')).to_be_visible()

        page.select_option("select#id_entity_type", "event")

        expect(page.locator('details[data-field-help="system_type"]')).to_be_visible()

    def test_form_with_every_deep_help_open_passes_axe(self, authenticated_page, base_url):
        page = authenticated_page
        _open_new_form(page, base_url)
        for field in DEEP_HELP_FIELDS:
            page.locator(f'details[data-field-help="{field}"] summary').click()
            expect(page.locator(f'details[data-field-help="{field}"]')).to_have_attribute("open", "")

        _scan(page, base_url, "config-doctype-new-deep-help")
