"""E2E: the quick-exit button covers no control and leaves no tap trap (Refs #2660, #2663).

The button is a corner button that reaches into the page (about y = 4..48 px at
the right edge) inside a sticky wrapper that also holds the offline, sync and
conflict bars. This spec measures the real boxes in the states a person can end
up in:

* **Resting state** (scroll position 0): for the widths 390, 640, 767, 768, 830,
  1024 and 1440 px, no visible ``a``, ``button``, ``input``, ``select``,
  ``textarea``, ``summary``, ``label`` or ``[role=button]`` may overlap the
  button, and the button must be the topmost element at its own centre. Pages:
  the start page, a list with a "new" action, the configuration lists whose
  title row carries action buttons, the forced-password-change page and the
  session-less offline fallback (both standalone pages with their own copy of
  the button), and the static offline shell with its navigation.
* **Scrolled** (page scrolled away from the top): the button stays in its corner
  and covers only its own surface. Sampling the top band with
  ``elementFromPoint`` finds no point outside the button where the sticky
  wrapper or the status container itself is the topmost element, i.e. no
  transparent strip that swallows taps while content scrolls underneath.
* **Status bars** (online page and offline shell): the offline bar with its
  counter link, the sync, conflict and "unclear" bars, and the static notices
  (authentication pending, offline key expired, revocation pending) are raised;
  neither their controls nor their text may run under the button, the bars keep
  their full width, and the scrolled state is trap-free.
* **Two-factor reminder** (second server with role enforcement, a staff account
  inside the grace period): the notice leaves the button free on every width.
* **Demo notice** (demo profile only, ``E2E_DEMO_MODE=1 E2E_SEED_SCALE=demo``):
  the folded notice stays on one line (at most 36 px) at 390 px, and notice and
  button never overlap on any width. The desktop line count is deliberately not
  asserted.

The default classes skip in the demo profile (the demo notice has its own case),
the demo class skips outside it. Run the demo class with
``E2E_DEMO_MODE=1 E2E_SEED_SCALE=demo pytest src/tests/e2e/test_quick_exit_geometry.py::TestDemoNotice``.

IMPORTANT: run E2E serially (container RAM limit).
"""

from __future__ import annotations

import os

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell
from ._selectors import demo_scenario_start

pytestmark = pytest.mark.e2e

# 640-767 px is the compact layout, 768-830 px the narrow desktop layout where the
# notices wrap differently; 767/768 sit on both sides of the breakpoint.
WIDTHS = [390, 640, 767, 768, 830, 1024, 1440]
HEIGHT = 900

# Online pages (admin session) and the signed-out offline shell.
ONLINE_PATHS = [
    "/",
    "/clients/",
    "/config/document-types/",
    "/config/field-templates/",
    "/import/",
    "/verwaltung/zugaenge/",
    # Standalone page (no layout) with its own copy of the button, see the module docstring.
    "/password-change/",
]
OFFLINE_SHELL_PATH = "/offline/client-shell/"
# Session-less offline fallback: standalone page with inline CSS instead of Tailwind.
OFFLINE_FALLBACK_PATH = "/offline/"

_CONTROLS = "a, button, input, select, textarea, summary, label, [role='button']"

_OVERLAPS = """
(controls) => {
    const button = document.getElementById("quick-exit-button");
    const b = button.getBoundingClientRect();
    const hits = [];
    for (const el of document.querySelectorAll(controls)) {
        if (button.contains(el)) continue;
        if (!el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) continue;
        const r = el.getBoundingClientRect();
        if (r.width <= 1 || r.height <= 1) continue;  // sr-only helpers
        const overlap = r.left < b.right && r.right > b.left && r.top < b.bottom && r.bottom > b.top;
        if (overlap) {
            hits.push({
                tag: el.tagName.toLowerCase(),
                testid: el.getAttribute("data-testid"),
                text: (el.textContent || el.getAttribute("aria-label") || "").trim().slice(0, 40),
                rect: [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)],
            });
        }
    }
    // Text of the full-width notices must not run under the button either: they
    // start at y = 0 and are not moved below it.
    const textHits = [];
    const notices = document.querySelectorAll(
        "#offline-status > :not(.sr-only), [data-testid='demo-banner'], [data-testid='mfa-setup-reminder'], "
        + "[data-testid='offline-nav']"
    );
    for (const notice of notices) {
        if (!notice.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) continue;
        const walker = document.createTreeWalker(notice, NodeFilter.SHOW_TEXT);
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
            if (!node.textContent.trim()) continue;
            const parent = node.parentElement;
            if (!parent || !parent.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) continue;
            const range = document.createRange();
            range.selectNodeContents(node);
            for (const r of range.getClientRects()) {
                if (r.width <= 1 || r.height <= 1) continue;
                if (r.left < b.right && r.right > b.left && r.top < b.bottom && r.bottom > b.top) {
                    textHits.push({
                        text: node.textContent.trim().slice(0, 40),
                        rect: [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)],
                    });
                    break;
                }
            }
        }
    }
    const cx = (b.left + b.right) / 2, cy = (b.top + b.bottom) / 2;
    const top = document.elementFromPoint(cx, cy);
    return {
        button: [Math.round(b.left), Math.round(b.top), Math.round(b.right), Math.round(b.bottom)],
        buttonOnTop: !!top && (top === button || button.contains(top)),
        hits,
        textHits,
    };
}
"""

# Tap traps: points of the band stuck to the top, outside the button, where the
# transparent wrapper (or the status container itself) is the topmost element.
_TAP_TRAPS = """
() => {
    const button = document.getElementById("quick-exit-button");
    const b = button.getBoundingClientRect();
    // Tailwind pages wrap the button in a ".sticky"/".fixed" element; the session-less
    // offline fallback spells the same fixed bar with inline CSS as ".quick-exit-bar".
    const wrapper = button.closest(".sticky, .fixed, .quick-exit-bar");
    if (!wrapper) throw new Error("the quick-exit button has no sticky or fixed wrapper");
    const w = wrapper.getBoundingClientRect();
    const bottom = Math.min(Math.max(w.bottom, b.bottom) + 2, innerHeight - 1);
    const traps = [];
    for (let y = 1; y < bottom; y += 3) {
        for (let x = 1; x < innerWidth; x += 6) {
            if (x >= b.left && x <= b.right && y >= b.top && y <= b.bottom) continue;
            const el = document.elementFromPoint(x, y);
            if (el && wrapper.contains(el)
                && (el === wrapper || el.id === "quick-exit-bar" || el.id === "offline-status"
                    || el.classList.contains("quick-exit-bar"))) {
                traps.push([x, y, el.id || "wrapper"]);
            }
        }
    }
    return {
        scrollY: Math.round(window.scrollY),
        wrapperTop: Math.round(w.top),
        wrapperHeight: Math.round(w.height),
        button: [Math.round(b.left), Math.round(b.top), Math.round(b.right), Math.round(b.bottom)],
        trapCount: traps.length,
        traps: traps.slice(0, 5),
    };
}
"""

# A tall spacer (set through the CSSOM, which the CSP allows) makes every page
# scrollable, so the sticky behaviour is measured on short pages as well.
_SCROLL_AWAY = """
() => {
    const spacer = document.createElement("div");
    spacer.setAttribute("data-testid", "geometry-spacer");
    spacer.style.height = "3000px";
    document.body.appendChild(spacer);
    window.scrollTo(0, 600);
    return Math.round(window.scrollY);
}
"""

# Raise the bars the way the page components do (their events), twice and with a
# pause in between so a late counter event of the page itself cannot reset them.
_RAISE_BARS = """
async (state) => {
    const fire = (name, detail) => window.dispatchEvent(new CustomEvent(name, {detail}));
    const raise = () => {
        if (state === "offline") {
            window.dispatchEvent(new Event("offline"));
            fire("offline-clients-count", {count: 3});
            fire("offline-unsynced-count", {count: 2});
        } else if (state === "sync") {
            fire("offline-queue-count", {count: 3, pending: 3, blocked: 0});
            fire("offline-conflict-count", {count: 2});
            fire("offline-count-unknown", {source: "unsynced"});
        } else if (state === "notices") {
            for (const selector of ["[data-offline-auth-pending]", "[data-offline-key-expired]",
                                    "[data-offline-revocation-pending]"]) {
                const el = document.querySelector(selector);
                if (!el) continue;
                const text = el.querySelector("[data-offline-auth-pending-text]");
                if (text) text.textContent = el.dataset.authPendingPasswordText.replace("{count}", "3");
                el.hidden = false;
            }
        }
    };
    raise();
    await new Promise((resolve) => setTimeout(resolve, 600));
    raise();
    await new Promise((resolve) => setTimeout(resolve, 200));
}
"""

# Which bars each state must show (test ids / data attributes).
BAR_SELECTORS = {
    "offline": ["[data-testid='offline-banner']"],
    "sync": ["[data-testid='sync-banner']", "[data-testid='conflict-banner']", "[data-testid='queue-unknown-banner']"],
    "notices": [
        "[data-testid='offline-auth-pending']",
        "[data-testid='offline-key-expired']",
        "[data-testid='offline-revocation-pending']",
    ],
}


def _measure(page, label):
    page.locator("#quick-exit-button").wait_for(state="visible")
    result = page.evaluate(_OVERLAPS, _CONTROLS)
    left, top, right, bottom = result["button"]
    assert right <= page.viewport_size["width"], f"{label}: the button leaves the viewport {result['button']}"
    assert 0 <= top < 16 and bottom <= 56, f"{label}: the button is not in the top corner {result['button']}"
    assert result["buttonOnTop"], f"{label}: another element sits on top of the button centre"
    assert not result["hits"], f"{label}: the button {result['button']} covers {result['hits']}"
    assert not result["textHits"], (
        f"{label}: notice text runs under the button {result['button']}: {result['textHits']}"
    )


def _scroll_away(page):
    scrolled = page.evaluate(_SCROLL_AWAY)
    assert scrolled > 0, "the page did not scroll"


def _measure_scrolled(page, label):
    """Scrolled state: the button keeps its corner and the wrapper leaves no tap trap."""
    page.locator("#quick-exit-button").wait_for(state="visible")
    result = page.evaluate(_TAP_TRAPS)
    assert result["scrollY"] > 0, f"{label}: not scrolled"
    assert result["wrapperTop"] == 0, f"{label}: the sticky wrapper left the top edge ({result['wrapperTop']})"
    left, top, right, bottom = result["button"]
    assert right <= page.viewport_size["width"] and 0 <= top < 16 and bottom <= 56, (
        f"{label}: the button left its corner {result['button']}"
    )
    assert result["trapCount"] == 0, (
        f"{label}: {result['trapCount']} points beside the button are caught by the sticky wrapper, "
        f"e.g. {result['traps']}"
    )
    on_top = page.evaluate(
        """() => {
            const b = document.getElementById("quick-exit-button").getBoundingClientRect();
            const el = document.elementFromPoint((b.left + b.right) / 2, (b.top + b.bottom) / 2);
            return !!el && !!el.closest("#quick-exit-button");
        }"""
    )
    assert on_top, f"{label}: the button is not the topmost element at its centre while scrolled"


def _raise_bars(page, state, label):
    page.wait_for_function(
        "() => { const el = document.getElementById('offline-status'); return !!(el && el._x_dataStack); }"
    )
    page.evaluate(_RAISE_BARS, state)
    for selector in BAR_SELECTORS[state]:
        expect(page.locator(selector), f"{label}: {selector} did not show").to_be_visible()


def _context(browser, width, **extra):
    return browser.new_context(
        locale="de-DE", viewport={"width": width, "height": HEIGHT}, service_workers="block", **extra
    )


@pytest.fixture
def _without_demo_mode():
    if os.environ.get("E2E_DEMO_MODE") == "1":
        pytest.skip("The geometry is measured without the demo notice; the demo class covers it")


def _open_online(browser, base_url, storage_state, path, width):
    context = _context(browser, width, storage_state=storage_state)
    page = context.new_page()
    page.set_default_timeout(30000)
    response = page.goto(f"{base_url}{path}", wait_until="load")
    assert response is not None and response.ok, f"{path}: HTTP {response and response.status}"
    assert page.locator("[data-testid='demo-banner']").count() == 0, "run without demo mode"
    return context, page


def _open_shell(browser, base_url, width, path=OFFLINE_SHELL_PATH):
    context = _context(browser, width)
    page = context.new_page()
    page.set_default_timeout(30000)
    response = page.goto(f"{base_url}{path}", wait_until="load")
    assert response is not None and response.ok
    if path == OFFLINE_SHELL_PATH:
        page.locator("[data-testid='offline-nav']").wait_for(state="visible")
    return context, page


@pytest.mark.usefixtures("_without_demo_mode")
class TestQuickExitGeometry:
    @pytest.mark.parametrize("width", WIDTHS)
    @pytest.mark.parametrize("path", ONLINE_PATHS)
    def test_button_covers_no_control_on_online_pages(self, browser, base_url, _login_storage_state, path, width):
        context, page = _open_online(browser, base_url, _login_storage_state, path, width)
        try:
            _measure(page, f"{path} @ {width}px")
        finally:
            context.close()

    @pytest.mark.parametrize("width", WIDTHS)
    @pytest.mark.parametrize("path", [OFFLINE_SHELL_PATH, OFFLINE_FALLBACK_PATH])
    def test_button_covers_no_control_in_the_offline_pages(self, browser, base_url, path, width):
        context, page = _open_shell(browser, base_url, width, path)
        try:
            _measure(page, f"{path} @ {width}px")
        finally:
            context.close()

    @pytest.mark.parametrize("width", WIDTHS)
    @pytest.mark.parametrize("path", ONLINE_PATHS)
    def test_scrolled_online_page_has_only_the_button_surface(
        self, browser, base_url, _login_storage_state, path, width
    ):
        context, page = _open_online(browser, base_url, _login_storage_state, path, width)
        try:
            _scroll_away(page)
            _measure_scrolled(page, f"{path} scrolled @ {width}px")
        finally:
            context.close()

    @pytest.mark.parametrize("width", WIDTHS)
    @pytest.mark.parametrize("path", [OFFLINE_SHELL_PATH, OFFLINE_FALLBACK_PATH])
    def test_scrolled_offline_pages_have_only_the_button_surface(self, browser, base_url, path, width):
        context, page = _open_shell(browser, base_url, width, path)
        try:
            _scroll_away(page)
            _measure_scrolled(page, f"{path} scrolled @ {width}px")
        finally:
            context.close()


@pytest.mark.usefixtures("_without_demo_mode")
class TestStatusBars:
    """Offline, sync, conflict and "unclear" bars plus the static notices, resting and scrolled."""

    @pytest.mark.parametrize("width", WIDTHS)
    @pytest.mark.parametrize("state", sorted(BAR_SELECTORS))
    @pytest.mark.parametrize("kind", ["online page", "offline shell"])
    def test_bars_leave_the_button_free_and_trap_nothing(
        self, browser, base_url, _login_storage_state, kind, state, width
    ):
        if kind == "online page":
            context, page = _open_online(browser, base_url, _login_storage_state, "/", width)
        else:
            context, page = _open_shell(browser, base_url, width)
        label = f"{kind} / {state} @ {width}px"
        try:
            _raise_bars(page, state, label)
            _measure(page, label)
            # The bars keep the full width: only their padding makes room for the button.
            bar_widths = page.evaluate(
                """(selectors) => selectors.map((s) => {
                    const el = document.querySelector(s);
                    return [Math.round(el.getBoundingClientRect().width), el.getBoundingClientRect().right];
                })""",
                BAR_SELECTORS[state],
            )
            wrapper_right = page.evaluate("() => Math.round(document.documentElement.clientWidth)")
            for width_px, right in bar_widths:
                assert round(right) == wrapper_right, f"{label}: a bar ends at {right}, not at the page edge"
            _scroll_away(page)
            _measure_scrolled(page, label + " scrolled")
        finally:
            context.close()


@pytest.mark.usefixtures("_without_demo_mode")
class TestTwoFactorReminder:
    """The 2FA reminder of a staff account inside the grace period (second server, enforced roles)."""

    _SET = (
        "import datetime; from django.utils import timezone; from core.models import User; "
        "deadline = timezone.now() + datetime.timedelta(days=5); "
        "User.objects.filter(username='miriam').update(mfa_setup_deadline=deadline); "
        "print('SET|ok')"
    )
    _RESET = (
        "from core.models import User; User.objects.filter(username='miriam').update(mfa_setup_deadline=None); "
        "print('RESET|ok')"
    )

    @pytest.fixture
    def grace_period(self, e2e_env):
        assert django_shell(e2e_env, self._SET)[-1] == "SET|ok"
        yield
        assert django_shell(e2e_env, self._RESET)[-1] == "RESET|ok"

    def test_reminder_leaves_the_button_free_on_every_width(
        self, browser, enforced_base_url, _staff_storage_state, grace_period
    ):
        context = browser.new_context(
            locale="de-DE",
            viewport={"width": WIDTHS[0], "height": HEIGHT},
            service_workers="block",
            storage_state=_staff_storage_state,
        )
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            for width in WIDTHS:
                page.set_viewport_size({"width": width, "height": HEIGHT})
                response = page.goto(f"{enforced_base_url}/", wait_until="load")
                assert response is not None and response.ok
                expect(page.locator("[data-testid='mfa-setup-reminder']"), f"reminder @ {width}px").to_be_visible()
                _measure(page, f"2FA reminder @ {width}px")
        finally:
            context.close()


class TestDemoNotice:
    """The folded demo notice next to the button (demo profile only)."""

    @pytest.fixture(autouse=True)
    def _demo_profile(self, base_url, browser):
        # Not ``require_demo``: that helper registers the call site in the demo gate
        # (``make test-e2e-demo``), which is a separate target of its own. This class is
        # started explicitly in the demo profile, see the module docstring.
        if os.environ.get("E2E_DEMO_MODE") != "1":
            pytest.skip("Requires the demo profile (E2E_DEMO_MODE=1, E2E_SEED_SCALE=demo)")
        assert os.environ.get("E2E_SEED_SCALE") == "demo", "The demo profile requires E2E_SEED_SCALE=demo"
        context = browser.new_context(locale="de-DE")
        try:
            response = context.new_page().goto(f"{base_url}/demo/")
            assert response is not None and response.status == 200, "the demo server must answer /demo/"
        finally:
            context.close()

    @pytest.mark.parametrize("path", ["/", "/clients/"])
    def test_notice_stays_visible_and_clear_of_the_button_on_every_width(self, browser, base_url, path):
        context = browser.new_context(
            locale="de-DE", viewport={"width": WIDTHS[0], "height": HEIGHT}, service_workers="block"
        )
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            page.goto(f"{base_url}/demo/")
            demo_scenario_start(page).click()
            page.wait_for_url(lambda url: "/demo/" not in url, timeout=15000)
            for width in WIDTHS:
                page.set_viewport_size({"width": width, "height": HEIGHT})
                response = page.goto(f"{base_url}{path}", wait_until="load")
                assert response is not None and response.ok
                banner = page.locator("[data-testid='demo-banner']")
                expect(banner, f"demo notice @ {width}px").to_be_visible()
                if width < 768:
                    # Folded on the compact layout: the summary line is one line.
                    height = banner.evaluate("el => el.getBoundingClientRect().height")
                    assert height <= 36, f"{path} @ {width}px: the folded demo notice is {height}px high"
                    assert banner.evaluate("el => el.hasAttribute('data-collapsed')")
                _measure(page, f"demo {path} @ {width}px")
                _scroll_away(page)
                _measure_scrolled(page, f"demo {path} scrolled @ {width}px")
        finally:
            context.close()
