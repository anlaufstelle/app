"""E2E: the contact list has its own navigation entry (Refs #2521).

Desktop sidebar row directly under "Zeitstrom", entry in the mobile "Mehr" menu,
highlight only on the contact list itself, and the entry is offered to the
assistant role. The registry/render rules are pinned by the unit tests
(``test_nav_registry.py``, ``test_navigation_mobile_more.py``); this spec drives
the real pages in a browser, including the active-state markup.
"""

import re
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from tests.e2e._date_helpers import expect_date_value
from tests.e2e._helpers import django_shell
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


@pytest.fixture
def contact_figure_entries(e2e_env):
    """Keep both origin lists nonempty inside the running window."""
    name = f"KPI list {uuid.uuid4().hex[:8]}"
    django_shell(
        e2e_env,
        f"""
from django.utils import timezone
from core.models import DocumentType, Event, Facility, User
facility = Facility.objects.get(name='Hauptstelle')
actor = User.objects.get(username='miriam')
document_type = DocumentType.objects.create(facility=facility, name='{name}', tally_enabled=True)
for origin in ('standard', 'tally'):
    Event.objects.create(
        facility=facility, document_type=document_type, occurred_at=timezone.now(), created_by=actor,
        is_anonymous=True, created_via=origin, data_json={{}},
    )
""",
    )
    yield
    django_shell(
        e2e_env,
        f"""
from core.models import DocumentType, Event
Event.objects.filter(document_type__name='{name}').delete()
DocumentType.objects.filter(name='{name}').delete()
""",
    )


class TestZeitstromContactFigures:
    @pytest.mark.parametrize(
        "testid,origin", [("zeitstrom-contacts-list", "standard"), ("zeitstrom-kpi-tally", "tally")]
    )
    def test_number_matches_the_filtered_list(self, staff_page, base_url, contact_figure_entries, testid, origin):
        page = staff_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        chip = page.get_by_test_id(testid)
        expect(chip).to_be_visible()
        count = int(chip.get_attribute("data-count"))
        assert count > 0
        href = chip.get_attribute("href")
        params = parse_qs(urlparse(href).query)
        assert params["created_via"] == [origin]
        assert params["date_from"] == params["date_to"]
        chip.click()
        page.wait_for_url(re.compile(r"/events/\?"))
        page.locator("[data-filter-panel-toggle]").click()
        expect(page.locator("#event-created_via")).to_have_value(origin)
        expect_date_value(page, "#event-date_from", params["date_from"][0])
        expect_date_value(page, "#event-date_to", params["date_to"][0])
        if "time_filter" in params:
            expect(page.locator("#event-time_filter")).to_have_value(params["time_filter"][0])
        table = page.locator("#event-table")
        pagination = table.get_by_text(re.compile(r"\d+–\d+ von \d+ Einträgen"))
        if pagination.count():
            shown_total = int(re.search(r"von (\d+)", pagination.inner_text())[1])
            assert shown_total == count
        else:
            expect(table.get_by_test_id("event-card-toggle")).to_have_count(count)
