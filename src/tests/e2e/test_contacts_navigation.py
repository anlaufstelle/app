"""E2E: the contact list has its own navigation entry (Refs #2521).

Desktop sidebar row directly under "Zeitstrom", entry in the mobile "Mehr" menu,
highlight only on the contact list itself, and the entry is offered to the
assistant role. The registry/render rules are pinned by the unit tests
(``test_nav_registry.py``, ``test_navigation_mobile_more.py``); this spec drives
the real pages in a browser, including the active-state markup.
"""

import re

import pytest
from playwright.sync_api import expect

from tests.e2e._selectors import event_card_toggle, open_mobile_more, sidebar_item, sidebar_items

pytestmark = pytest.mark.e2e

EVENT_DETAIL_URL = re.compile(r"/events/[0-9a-f-]{36}/$")
MOBILE_VIEWPORT = {"width": 375, "height": 812}


def _active_sidebar_hrefs(page):
    return page.locator("[data-testid='nav-items'] > a[aria-current='page']").evaluate_all(
        "els => els.map(a => a.getAttribute('href'))"
    )


class TestDesktopSidebarContacts:
    def test_entry_sits_directly_below_zeitstrom(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")

        items = sidebar_items(page)
        items.first.wait_for(state="visible", timeout=10000)
        hrefs = items.evaluate_all("els => els.map(a => a.getAttribute('href'))")

        assert hrefs.index("/events/") == hrefs.index("/") + 1
        assert hrefs.index("/events/") < hrefs.index("/workitems/")
        entry = sidebar_item(page, "contacts")
        expect(entry).to_be_visible()
        expect(entry).to_contain_text("Kontakte")

    def test_click_opens_the_contact_list(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")

        sidebar_item(page, "contacts").click()
        page.wait_for_url(re.compile(r"/events/$"), timeout=10000)

        assert page.url.startswith(f"{base_url}/events/")

    def test_highlight_follows_the_contact_list_only(self, authenticated_page, base_url):
        page = authenticated_page

        page.goto(f"{base_url}/events/", wait_until="domcontentloaded")
        sidebar_item(page, "contacts").wait_for(state="visible", timeout=10000)
        assert _active_sidebar_hrefs(page) == ["/events/"]

        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        sidebar_item(page, "contacts").wait_for(state="visible", timeout=10000)
        assert _active_sidebar_hrefs(page) == ["/"]

        # An event record page keeps the Zeitstrom as its home, not the contact list.
        page.goto(f"{base_url}/events/", wait_until="domcontentloaded")
        # The list renders the shared card: open the first one, its entry link is revealed.
        event_card_toggle(page).click()
        detail_link = page.locator("#event-table a[href^='/events/']").filter(has_text="Zum Eintrag").first
        detail_link.wait_for(state="visible", timeout=10000)
        detail_link.click()
        page.wait_for_url(EVENT_DETAIL_URL, timeout=10000)
        sidebar_item(page, "contacts").wait_for(state="visible", timeout=10000)
        assert _active_sidebar_hrefs(page) == ["/"]

    def test_assistant_sees_the_entry(self, assistant_page, base_url):
        page = assistant_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")

        entry = sidebar_item(page, "contacts")
        expect(entry).to_be_visible(timeout=10000)
        assert entry.get_attribute("href") == "/events/"

        entry.click()
        page.wait_for_url(re.compile(r"/events/$"), timeout=10000)
        assert _active_sidebar_hrefs(page) == ["/events/"]

    def test_system_role_has_no_contacts_entry(self, super_admin_page, base_url):
        page = super_admin_page
        page.goto(f"{base_url}/system/", wait_until="domcontentloaded")
        sidebar_items(page).first.wait_for(state="visible", timeout=10000)

        assert sidebar_item(page, "contacts").count() == 0


class TestMobileMoreContacts:
    def test_more_menu_offers_contacts_and_marks_more_as_current(self, mobile_authenticated_page, base_url):
        page = mobile_authenticated_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")

        # On the Zeitstrom the "Mehr" slot is not the current place.
        more = page.locator("[data-testid='mobile-nav-more']")
        more.wait_for(state="visible", timeout=10000)
        assert more.get_attribute("aria-current") is None

        menu = open_mobile_more(page)
        menu.wait_for(state="visible", timeout=10000)
        entry = menu.locator("[data-testid='mobile-nav-contacts']")
        expect(entry).to_be_visible()
        assert entry.get_attribute("href") == "/events/"

        entry.click()
        page.wait_for_url(re.compile(r"/events/$"), timeout=10000)

        # On the contact list the menu slot carries the highlight, no bottom slot does.
        more = page.locator("[data-testid='mobile-nav-more']")
        more.wait_for(state="visible", timeout=10000)
        assert more.get_attribute("aria-current") == "true"
        current_slots = page.locator("[aria-label='Mobile Navigation'] a[aria-current='page']")
        assert current_slots.count() == 0

    def test_assistant_sees_the_mobile_entry(self, assistant_page, base_url):
        page = assistant_page
        page.set_viewport_size(MOBILE_VIEWPORT)
        page.goto(f"{base_url}/", wait_until="domcontentloaded")

        menu = open_mobile_more(page)
        menu.wait_for(state="visible", timeout=10000)
        entry = menu.locator("[data-testid='mobile-nav-contacts']")
        expect(entry).to_be_visible()
        assert entry.get_attribute("href") == "/events/"
