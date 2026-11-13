"""The tab's session storage after "Quick exit" and after logging out, in a real browser.

htmx keeps page snapshots (``htmx-history-cache``) and the current path
(``htmx-current-path-for-history``) in ``sessionStorage``; filter-persistence.js keeps
``filters:<path>``. All three must be gone once the person leaves, also when the browser
does not honour ``Clear-Site-Data`` and also when the old document is still alive and the
Back button is used before the navigation arrives.

The logout response is stripped of ``Clear-Site-Data`` and the contexts block service
workers, otherwise the header (or the worker) hides the JavaScript path under test. The
navigation is held on purpose: that keeps the old document alive, which is the window where
a late write could follow.

While a navigation is held the browser does not answer ``page.evaluate`` reliably, so the old
document reports its own storage: an init script posts a snapshot every 100 ms to a probe URL
that the test answers itself, and the Back button is pressed with the keyboard.
"""

from __future__ import annotations

import re
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from ._helpers import do_real_login as _do_real_login
from .test_event_list import filter_by, open_filter_panel
from .test_quick_exit import EXIT_TARGET_GLOB, STUB_HTML, _set_facility_settings

pytestmark = pytest.mark.e2e

_STORAGE = """() => ({
    cache: sessionStorage.getItem('htmx-history-cache'),
    path: sessionStorage.getItem('htmx-current-path-for-history'),
    filters: Object.keys(sessionStorage).filter(key => key.startsWith('filters:')),
})"""


_PROBE_URL = "/__storage-probe__/"
_PROBE = """() => {
    const read = () => ({
        cache: sessionStorage.getItem('htmx-history-cache') !== null ? '1' : '0',
        path: sessionStorage.getItem('htmx-current-path-for-history') || '',
        filters: String(Object.keys(sessionStorage).filter(key => key.startsWith('filters:')).length),
    });
    setInterval(() => {
        fetch('/__storage-probe__/?' + new URLSearchParams(read()))
            .then(response => response.text())
            .then(command => { if (command === 'back') history.back(); })
            .catch(() => { /* leaving */ });
    }, 100);
}"""


def _storage(page):
    return page.evaluate(_STORAGE)


class _Window:
    """Collects the probe snapshots and the htmx Back restores of one old document.

    The window opens when the navigation of the leave path is held: by then the page has had its
    chance to clear the storage, and everything the old document does afterwards is observed.
    """

    def __init__(self, context, page):
        self.snapshots: list[dict] = []
        self.restores = 0
        self.command = ""
        self.held: list = []
        context.add_init_script(f"({_PROBE})()")
        context.route(re.compile(re.escape(_PROBE_URL)), self._probe)
        page.on("request", self._request)

    def _probe(self, route):
        query = parse_qs(urlsplit(route.request.url).query, keep_blank_values=True)
        self.snapshots.append({key: values[0] for key, values in query.items()})
        route.fulfill(status=200, content_type="text/plain", body=self.command)
        self.command = ""

    def _request(self, request):
        if request.headers.get("hx-history-restore-request"):
            self.restores += 1

    def press_back(self, page):
        """Have the old document call ``history.back()`` with its next probe (no evaluate while held)."""
        self.command = "back"
        end = time.monotonic() + 5
        while self.command and time.monotonic() < end:
            page.wait_for_timeout(50)
        assert not self.command, "the old document did not pick up the Back command"

    def wait_until_held(self, page, seconds=15):
        """Wait until the navigation of the leave path is under way (its request is held)."""
        end = time.monotonic() + seconds
        while not self.held and time.monotonic() < end:
            page.wait_for_timeout(50)
        assert self.held, "the leave path did not start its navigation"

    def observe(self, page, seconds):
        """Keep looking: the storage must stay free of the three kinds of keys the whole time."""
        start = len(self.snapshots)
        page.wait_for_timeout(int(seconds * 1000))
        seen = self.snapshots[start:]
        assert seen, "the old document sent no snapshot while observing"
        for item in seen:
            assert item["cache"] == "0", item
            assert item["filters"] == "0", item
            assert item["path"] == "", item

    def release(self):
        """Abort what is still held so that closing the context does not wait for it."""
        for route in self.held:
            try:
                route.abort()
            except Exception:  # noqa: BLE001 - best-effort clean-up, the result is already decided
                pass


def _new_context(browser):
    context = browser.new_context(locale="de-DE", viewport={"width": 1280, "height": 900}, service_workers="block")
    page = context.new_page()
    page.set_default_timeout(30000)
    return context, page


def _logout_without_header(route):
    """Let the logout through to the server but drop ``Clear-Site-Data`` from its answer."""
    response = route.fetch(max_redirects=0)
    headers = {key: value for key, value in response.headers.items() if key.lower() != "clear-site-data"}
    route.fulfill(response=response, headers=headers)


def _fill_storage_through_two_navigations(page, base_url):
    """Two filter changes, each one a ``hx-push-url`` navigation, so the history cache and a filter key exist."""
    page.goto(f"{base_url}/events/", wait_until="domcontentloaded")
    open_filter_panel(page)
    for name in ("Erfassungsweg", "Typ"):
        select = page.get_by_role("combobox", name=name, exact=True)
        assert select.locator("option").count() > 1, f"the {name} filter needs an option to choose"
        filter_by(page, lambda select=select: select.select_option(index=1))
    state = _storage(page)
    assert state["cache"], "two push navigations must have filled the htmx history cache"
    assert state["filters"], "the filter change must have been remembered under filters:"
    assert state["path"], "htmx remembers the current path while the person is on the page"


def _logout_control(page):
    return page.locator("nav").get_by_role("button", name="Abmelden")


def _only_login_page_keys(page, base_url):
    """After the navigation a same-origin page may remember its own path, nothing of the earlier session."""
    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
    state = _storage(page)
    assert state["cache"] is None, state
    assert state["filters"] == [], state
    assert state["path"] in (None, "/login/"), state


def test_logout_empties_the_tab_storage_before_and_after_going_back(browser, base_url):
    context, page = _new_context(browser)
    window = _Window(context, page)
    try:
        _do_real_login(page, base_url)
        context.route(re.compile(r"/logout/$"), lambda route: window.held.append(route))
        _fill_storage_through_two_navigations(page, base_url)
        _logout_control(page).click(no_wait_after=True)
        window.wait_until_held(page)
        window.observe(page, 1.0)
        # Back inside the window between the click and the navigation: htmx restores the page
        # from the server (cache miss) and must not write the snapshot or the path again.
        window.press_back(page)
        window.observe(page, 2.0)
        assert window.restores >= 1, "Back must have made htmx restore the page, otherwise the test shows nothing"
        assert len(window.held) == 1, "the logout form is submitted once"
        # Now let the logout through, without the header, and look at the tab afterwards.
        # (Back has cancelled the pending navigation; the request itself still reaches the server.)
        _logout_without_header(window.held.pop())
        _only_login_page_keys(page, base_url)
    finally:
        window.release()
        context.close()


def test_logout_navigation_leaves_nothing_behind_without_clear_site_data(browser, base_url):
    context, page = _new_context(browser)
    seen: list = []
    try:
        _do_real_login(page, base_url)

        def handler(route):
            seen.append(route.fetch(max_redirects=0).headers.get("clear-site-data"))
            _logout_without_header(route)

        context.route(re.compile(r"/logout/$"), handler)
        _fill_storage_through_two_navigations(page, base_url)
        _logout_control(page).click()
        page.wait_for_url(re.compile(r"/login/"), timeout=15000)
        assert seen and seen[0], "the server sends Clear-Site-Data, so the test really removes it"
        state = _storage(page)
        assert state["cache"] is None, state
        assert state["filters"] == [], state
        assert state["path"] in (None, "/login/"), state
    finally:
        context.close()


def test_quick_exit_empties_the_tab_storage_before_and_after_going_back(browser, base_url, e2e_env):
    _set_facility_settings(e2e_env, quick_exit_url="https://www.wikipedia.org/", privacy_screen=False)
    context, page = _new_context(browser)
    window = _Window(context, page)
    try:
        _do_real_login(page, base_url)
        context.route(re.compile(r"/logout/$"), _logout_without_header)
        _fill_storage_through_two_navigations(page, base_url)
        # Hold the navigation to the exit target: the old document stays alive.
        page.route(EXIT_TARGET_GLOB, lambda route: window.held.append(route))
        page.get_by_test_id("quick-exit-button").click(no_wait_after=True)
        window.wait_until_held(page)
        window.observe(page, 1.0)
        window.press_back(page)
        window.observe(page, 2.0)
        assert window.restores >= 1, "Back must have made htmx restore the page, otherwise the test shows nothing"
        for route in window.held:
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=STUB_HTML)
        window.held.clear()
        _only_login_page_keys(page, base_url)
    finally:
        window.release()
        context.close()


def test_failed_logout_can_be_started_again_after_the_window(browser, base_url):
    context, page = _new_context(browser)
    window = _Window(context, page)
    posts: list = []

    def never_answered(route):
        window.held.append(route)
        if route.request.method == "POST":
            posts.append(time.monotonic())

    try:
        _do_real_login(page, base_url)
        page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")
        context.route(re.compile(r"/logout/$"), never_answered)
        control = _logout_control(page)
        control.click(no_wait_after=True)
        window.wait_until_held(page)
        # The button keeps the focus, so Enter submits the logout form again without any page script call.
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
        assert len(posts) == 1, f"a second click inside the window must be swallowed: {posts}"
        page.wait_for_timeout(11000)
        page.keyboard.press("Enter")
        end = time.monotonic() + 10
        while len(posts) < 2 and time.monotonic() < end:
            page.wait_for_timeout(200)
        assert len(posts) == 2, f"a second logout attempt must send a second POST after the window: {posts}"
    finally:
        window.release()
        context.close()
