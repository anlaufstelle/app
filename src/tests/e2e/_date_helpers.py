"""Interact with visible application-language date controls (Refs #2603)."""

import re

from playwright.sync_api import expect


def date_field(page, selector):
    """Wait for widget initialization and resolve either selector to its clone."""
    field = page.locator(selector)
    expect(field).to_have_attribute("data-app-date-kind", re.compile(r"^(date|time|datetime-local)$"))
    if field.get_attribute("data-app-date-visible") != "true":
        field = field.locator("xpath=preceding-sibling::input[1]")
    expect(field).to_be_visible()
    expect(field).to_have_attribute("data-app-date-visible", "true")
    return field


def _display_value(field, iso):
    if not iso or field.get_attribute("data-app-date-kind") == "time":
        return iso
    date, separator, time = iso.partition("T")
    if field.get_attribute("data-date-language") == "de":
        date = ".".join(reversed(date.split("-")))
    return date + (" " + time if separator else "")


def expect_date_value(page, selector, iso):
    """Assert both the language-specific display and the ISO transport."""
    field = date_field(page, selector)
    expect(field).to_have_value(_display_value(field, iso))
    expect(field.locator("xpath=following-sibling::input[1]")).to_have_value(iso)


def fill_date(page, selector, iso, *, native=False):
    """Type through the visible control and verify the widget's sync path."""
    if native:
        field = page.locator(selector)
        expect(field).to_be_visible()
        expect(field).to_have_attribute("type", re.compile(r"^(date|time|datetime-local)$"))
        field.fill(iso)
        expect(field).to_have_value(iso)
        return
    field = date_field(page, selector)
    field.fill(_display_value(field, iso))
    expect_date_value(page, selector, iso)
