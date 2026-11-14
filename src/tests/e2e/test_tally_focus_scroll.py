"""Playwright checks: a tally stroke keeps the scroll position and the keyboard focus.

A stroke swaps the tile and then asks the feed to reload itself.  That reload
used to pull the focus into the feed and scroll the page down to it, so the
tiles left the screen after every tap.  The scenarios run on a day whose feed
is long enough for the page to scroll, at a desktop and a phone viewport.
"""

import re

import pytest
from playwright.sync_api import expect

from ._helpers import (
    django_shell,
    do_real_login,
    tally_tile,
    wait_for_active_service_worker,
    wait_until,
    wait_until_htmx_bound,
)
from .test_offline_tally import _cleanup_offline_state, _reconnect_via_online_event

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


def _settled_scroll_y(page):
    """The scroll position once a smooth scroll has come to rest."""
    previous = [None]

    def at_rest():
        current = _scroll_y(page)
        settled = current == previous[0]
        previous[0] = current
        return settled

    assert wait_until(page, at_rest, timeout_ms=5000, interval_ms=200), "the page keeps scrolling"
    return previous[0]


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

    def test_feed_reload_without_a_focused_element_neither_focuses_nor_scrolls(
        self, staff_page, base_url, long_feed, viewport_name
    ):
        """A background reload (offline replay, fresh page) must not pull the page to the feed."""
        page = staff_page
        self._open(page, base_url, viewport_name)
        page.evaluate("() => document.activeElement && document.activeElement.blur()")
        assert page.evaluate("() => document.activeElement === document.body")

        # The same trigger the offline replay fires after it synced a stroke.
        _wait_for_feed_reload(
            page, lambda: page.evaluate("() => document.body.dispatchEvent(new CustomEvent('zeitstrom-changed'))")
        )

        assert _scroll_y(page) == 0, "the page scrolled to the feed after a background reload"
        assert page.evaluate("() => document.activeElement === document.body"), "the feed took the focus"

    def test_feed_reload_replacing_the_focused_card_keeps_the_focus_in_the_feed(
        self, staff_page, base_url, long_feed, viewport_name
    ):
        """The focus on a feed card survives a background reload that replaces the card."""
        page = staff_page
        self._open(page, base_url, viewport_name)
        controls = f"event-detail-{long_feed[0]}"
        selector = f'#feed-list button[aria-controls="{controls}"]'
        # The live watch refreshes the feed once right after the page load; focus the card after that.
        page.wait_for_load_state("networkidle")
        page.locator(selector).scroll_into_view_if_needed()
        scroll_before = _settled_scroll_y(page)
        assert scroll_before > 0
        page.evaluate(
            "(selector) => document.querySelector(selector).focus({ preventScroll: true })",
            selector,
        )
        assert page.evaluate("(selector) => document.activeElement === document.querySelector(selector)", selector)

        _wait_for_feed_reload(
            page, lambda: page.evaluate("() => document.body.dispatchEvent(new CustomEvent('zeitstrom-changed'))")
        )

        assert _scroll_y(page) == scroll_before, "the page moved after the background reload"
        focused = page.evaluate(
            """() => {
                const active = document.activeElement;
                return {
                    inFeed: !!active.closest('#feed-list'),
                    controls: active.getAttribute('aria-controls'),
                };
            }"""
        )
        assert focused == {"inFeed": True, "controls": f"event-detail-{long_feed[0]}"}, focused

    def test_number_tile_save_keeps_scroll_and_focus_on_the_tile(self, staff_page, base_url, long_feed, viewport_name):
        page = staff_page
        self._open(page, base_url, viewport_name)
        tile = tally_tile(page, "Spritzentausch")
        tile_testid = tile.get_attribute("data-testid")
        _stroke(page, tile)

        form = page.locator('#tally-number-form-slot [data-testid^="tally-inline-form-"]')
        expect(form).to_be_visible()
        wait_until_htmx_bound(page, form)
        form.locator('input[name="ausgabe"]').fill("5")
        form.locator('input[name="rueckgabe"]').fill("2")
        _wait_for_feed_reload(page, form.locator('[data-testid^="tally-submit-"]').click)

        expect(form).to_have_count(0)
        assert _scroll_y(page) == 0, "the page scrolled away from the tiles after saving a number tile"
        assert wait_until(page, lambda: _active_row_testid(page) == tile_testid), (
            f"the focus is on {_active_row_testid(page)} after saving, not on the tile"
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


def test_offline_replay_reload_keeps_scroll_and_leaves_the_focus_alone(browser, base_url, long_feed):
    """The reload after the offline replay (real queue, real reconnect) neither scrolls nor focuses."""
    context = browser.new_context(locale="de-DE", viewport=VIEWPORTS["desktop"])
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        do_real_login(page, base_url)
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        page.wait_for_function(
            "() => !!(window.crypto_session && window.offlineStore"
            " && window.syncOrchestrator && window.offlineTallyUi)",
            timeout=10000,
        )
        page.evaluate("async () => { await window.crypto_session.ready(); }")
        wait_for_active_service_worker(page)
        page.evaluate("async () => { await window.crypto_session.ready(); }")
        page.evaluate("async () => window.offlineStore.revalidateCachedFacility()")
        page.evaluate("async () => window.offlineTallyUi.refreshOnlinePanel({useSnapshot: true})")

        tile = tally_tile(page, "Kontakt")
        expect(tile).to_be_visible()
        page.context.set_offline(True)
        page.evaluate("window.dispatchEvent(new Event('offline'))")
        _stroke(page, tile)
        expect(tile.locator("[data-offline-tally-status]")).to_be_visible(timeout=15000)

        page.evaluate("() => window.scrollTo(0, 0)")
        page.evaluate("() => document.activeElement && document.activeElement.blur()")
        assert page.evaluate("() => document.activeElement === document.body")

        _wait_for_feed_reload(page, lambda: _reconnect_via_online_event(page))
        page.wait_for_function("async () => (await window.offlineStore.count('queue')) === 0", timeout=30000)

        assert _scroll_y(page) == 0, "the page scrolled to the feed after the offline replay"
        assert page.evaluate("() => document.activeElement === document.body"), "the replay moved the focus"
    finally:
        _cleanup_offline_state(page)
        context.close()
