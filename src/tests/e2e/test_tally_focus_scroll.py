"""Playwright checks: a tally stroke keeps the scroll position and the keyboard focus.

A stroke swaps the tile and then asks the feed to reload itself.  That reload
used to pull the focus into the feed and scroll the page down to it, so the
tiles left the screen after every tap.  The scenarios run on a day whose feed
is long enough for the page to scroll, at a desktop and a phone viewport.
"""

import re

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell, tally_tile, wait_until

pytestmark = pytest.mark.e2e

FEED_EVENT_COUNT = 45
VIEWPORTS = {
    "desktop": {"width": 1200, "height": 750},
    "mobile": {"width": 390, "height": 844},
}

_CREATE_FEED_EVENTS = """
from datetime import timedelta
from django.utils import timezone
from core.models import DocumentType, Event, User
user = User.objects.get(username='miriam')
doc_type = (
    DocumentType.objects.filter(
        facility=user.facility, entity_type=DocumentType.EntityType.EVENT, is_active=True, tally_enabled=False
    )
    .order_by('slug')
    .first()
)
now = timezone.now()
events = Event.objects.bulk_create(
    Event(
        facility=user.facility,
        client=None,
        document_type=doc_type,
        occurred_at=now - timedelta(seconds=index + 1),
        data_json={},
        search_text='',
        is_anonymous=True,
        created_by=user,
    )
    for index in range(%(count)d)
)
print('PKS|' + ','.join(str(event.pk) for event in events))
"""

_DELETE_FEED_EVENTS = "from core.models import Event; Event.objects.filter(pk__in=%(pks)r).delete()"


@pytest.fixture
def long_feed(e2e_env):
    """A day with enough feed events that the page can scroll; removed afterwards."""
    lines = django_shell(e2e_env, _CREATE_FEED_EVENTS % {"count": FEED_EVENT_COUNT})
    pks = next(line.removeprefix("PKS|") for line in lines if line.startswith("PKS|")).split(",")
    assert len(pks) == FEED_EVENT_COUNT
    try:
        yield pks
    finally:
        django_shell(e2e_env, _DELETE_FEED_EVENTS % {"pks": pks})


def _scroll_y(page):
    return page.evaluate("() => window.scrollY")


def _active_row_testid(page):
    """The ``data-testid`` of the tally tile that holds the focus, or what holds it instead."""
    return page.evaluate(
        """() => {
            const active = document.activeElement;
            if (!active) return 'none';
            const row = active.closest('[data-testid^="tally-row-"]');
            if (row) return row.dataset.testid;
            return active.id ? '#' + active.id : active.tagName.toLowerCase();
        }"""
    )


def _wait_for_feed_reload(page, action):
    """Run ``action`` and wait until the feed has reloaded itself afterwards."""
    with page.expect_response(
        lambda response: response.request.method == "GET" and "/partials/zeitstrom/feed/" in response.url
    ):
        action()
    # The swap and the focus handler run right after the response arrived.
    page.wait_for_timeout(600)


def _stroke(page, tile):
    tile.locator('[data-testid^="tally-increment-"]').click()


@pytest.mark.parametrize("viewport_name", sorted(VIEWPORTS))
def test_undo_button_shows_a_space_before_the_countdown(staff_page, base_url, viewport_name):
    """The rendered label reads "Rückgängig · 60 s" - the flex button must not swallow the space."""
    page = staff_page
    page.set_viewport_size(VIEWPORTS[viewport_name])
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    tile = tally_tile(page, "Kontakt")
    _stroke(page, tile)

    undo = page.locator('#tally-undo-slot [data-testid="tally-undo"]')
    expect(undo).to_be_visible()
    expect(undo).to_have_text(re.compile(r"^Rückgängig · \d+ s$"), use_inner_text=True)


@pytest.mark.parametrize("viewport_name", sorted(VIEWPORTS))
class TestTallyStrokeKeepsPlace:
    def _open(self, page, base_url, viewport_name):
        page.set_viewport_size(VIEWPORTS[viewport_name])
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        tile = tally_tile(page, "Kontakt")
        expect(tile).to_be_visible()
        page.evaluate("() => window.scrollTo(0, 0)")
        page_height = page.evaluate("() => document.documentElement.scrollHeight")
        assert page_height > VIEWPORTS[viewport_name]["height"] + 200, (
            "the page must be long enough to scroll for this check to mean anything"
        )
        return tile

    def test_stroke_keeps_scroll_and_focus_on_the_tile(self, staff_page, base_url, long_feed, viewport_name):
        page = staff_page
        tile = self._open(page, base_url, viewport_name)
        tile_testid = tile.get_attribute("data-testid")

        _wait_for_feed_reload(page, lambda: _stroke(page, tile))

        assert _scroll_y(page) == 0, "the page scrolled away from the tiles after a stroke"
        assert _active_row_testid(page) == tile_testid, "the focus left the tile after a stroke"
        expect(tile).to_be_in_viewport()

    def test_undo_keeps_scroll_and_focus_on_the_tile(self, staff_page, base_url, long_feed, viewport_name):
        page = staff_page
        tile = self._open(page, base_url, viewport_name)
        tile_testid = tile.get_attribute("data-testid")
        _wait_for_feed_reload(page, lambda: _stroke(page, tile))

        undo = page.locator('#tally-undo-slot [data-testid="tally-undo"]')
        expect(undo).to_be_visible()
        _wait_for_feed_reload(page, undo.click)

        assert _scroll_y(page) == 0, "the page scrolled away from the tiles after the undo"
        assert wait_until(page, lambda: _active_row_testid(page) == tile_testid), (
            f"the focus is on {_active_row_testid(page)} after the undo, not on the tile"
        )
        expect(tile).to_be_in_viewport()

    def test_feed_filter_change_still_moves_the_focus_into_the_feed(
        self, staff_page, base_url, long_feed, viewport_name
    ):
        """The focus guidance for a person's own feed swap stays untouched."""
        page = staff_page
        self._open(page, base_url, viewport_name)
        doc_type_filter = page.locator("#filter-doc-type")
        expect(doc_type_filter).to_be_visible()

        _wait_for_feed_reload(page, lambda: doc_type_filter.select_option(index=1))

        assert page.evaluate("() => document.activeElement && document.activeElement.id") == "feed-list"
