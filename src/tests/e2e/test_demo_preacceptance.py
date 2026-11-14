"""Demo pre-acceptance in the real browser (demo profile, staff and assistant, 1280 px and 390 px).

Walks the pages a visitor meets after the demo entry: timeline, contact list with every
contact card opened, tally tiles with the undo bar and the number form, people list, the
quick-exit button and the logout. Per page and role it asserts what a visitor sees:

* no horizontal overflow,
* axe finds no serious or critical violation (the baseline is empty, so any new one fails),
* touch targets of the main content, the quick-exit button and the logout are at least 44 px
  on the compact layout (the 1280 px figures are recorded, not asserted),
* the demo notice is visible and neither covers the quick-exit button nor the page heading.

Runs only in the demo profile (``make test-e2e-demo``). Set ``E2E_EVIDENCE_DIR`` to keep the
measurements and screenshots outside the source tree.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import pytest
from playwright.sync_api import expect

from ._axe import assert_within_baseline, run_axe
from ._profiles import require_demo
from ._selectors import (
    demo_scenario_start,
    find_first_by_exact_testid,
    wait_for_settled,
    wait_for_transition_end,
)

pytestmark = pytest.mark.e2e

STUB_HTML = "<!doctype html><html lang='de'><head><title>Neutral</title><body><h1>Neutral target</h1>"
WIDTHS = [pytest.param(1280, 900, id="1280"), pytest.param(390, 844, id="390")]
ROLES = [pytest.param("staff", "Fachkraft", id="staff"), pytest.param("assistant", "Assistenz", id="assistant")]

_METRICS = r"""() => {
    const small = Array.from(document.querySelectorAll(
        '#main-content a[href], #main-content button:not(:disabled), #main-content summary, ' +
        '#main-content input:not([type=hidden]), #main-content select, #quick-exit-button'))
        .filter(el => el.checkVisibility()).map(el => {
            const label = (el.type === 'checkbox' || el.type === 'radio') &&
                (el.closest('label') || (el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)));
            const r = (label || el).getBoundingClientRect();
            return {name: (el.getAttribute('aria-label') || (label || el).textContent || el.name || '').trim()
                    .replace(/\s+/g, ' ').slice(0, 60), tag: el.tagName.toLowerCase(),
                testid: el.getAttribute('data-testid'), width: Math.round(r.width), height: Math.round(r.height)};
        }).filter(r => r.width < 44 || r.height < 44);
    const rect = sel => { const el = document.querySelector(sel); if (!el || !el.checkVisibility()) return null;
        const r = el.getBoundingClientRect(); return {left: r.left, right: r.right, top: r.top, bottom: r.bottom}; };
    const textRects = sel => {
        const root = document.querySelector(sel); if (!root || !root.checkVisibility()) return [];
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT), out = [];
        while (walker.nextNode()) { const n = walker.currentNode;
            if (!n.textContent.trim() || !n.parentElement.checkVisibility()) continue;
            const range = document.createRange(); range.selectNodeContents(n);
            for (const r of range.getClientRects())
                out.push({left: r.left, right: r.right, top: r.top, bottom: r.bottom});
        } return out; };
    const topmost = (sel) => { const el = document.querySelector(sel); if (!el || !el.checkVisibility()) return null;
        const r = el.getBoundingClientRect();
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + Math.min(r.height / 2, 12));
        return !!hit && (hit === el || el.contains(hit)); };
    return {scroll: document.documentElement.scrollWidth, viewport: document.documentElement.clientWidth,
        small, bannerText: textRects('[data-testid="demo-banner"]'),
        banner: rect('[data-testid="demo-banner"]'), exit: rect('#quick-exit-button'),
        bannerOnTop: topmost('[data-testid="demo-banner"]'), exitOnTop: topmost('#quick-exit-button'),
        headingOnTop: topmost('#main-content h1')};
}"""


@pytest.fixture
def demo_profile(base_url, browser):
    def probe():
        context = browser.new_context(locale="de-DE")
        try:
            response = context.new_page().goto(f"{base_url}/demo/")
            return response is not None and response.status == 200
        finally:
            context.close()

    require_demo(probe)


def _record(page, name, values):
    print("DEMO-ACCEPTANCE " + name + " " + json.dumps(values, sort_keys=True))
    directory = os.environ.get("E2E_EVIDENCE_DIR")
    if directory:
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target / f"{name}.png"), full_page=True)
        (target / f"{name}.json").write_text(json.dumps(values, indent=2, sort_keys=True) + "\n")


def _start_demo(page, base_url, role):
    page.goto(f"{base_url}/demo/")
    if role == "staff":
        demo_scenario_start(page).click()
    else:
        card = page.locator("[data-testid='demo-scenario-card'][data-scenario-key='kontaktladen']")
        card.locator("[data-testid='demo-scenario-other-roles'] summary").click()
        card.locator("input[name=role][value=assistant]").check()
        card.get_by_role("button", name="Mit gewählter Rolle starten").click()
    page.wait_for_url(lambda url: "/demo/" not in url, timeout=15000)


def _check_page(page, base_url, key, width, role, *, require_banner=True):
    """Measure the current page, assert the visible promises and return the metrics."""
    # Opening cards or a sheet scrolls the page; the notice and the exit button are judged at the top.
    page.evaluate("() => window.scrollTo(0, 0)")
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    metrics = page.evaluate(_METRICS)
    result = run_axe(page, base_url)
    metrics["axe"] = {v["rule"]: len(v["nodes"]) for v in result.violations}
    name = f"demo-{role}-{width}-{key}"
    _record(page, name, metrics)
    assert metrics["scroll"] <= metrics["viewport"], f"{name}: horizontal overflow {metrics}"
    assert_within_baseline(f"demo_{key}_{role}", result, {})
    if width < 768:
        # The folded demo notice is a deliberate one-line bar (at most 36 px high). Its toggle is
        # demo only and meets WCAG 2.5.8 AA (at least 24 px); it is the only target below 44 px
        # that is accepted, the 44 px goal holds for every other target.
        small = [t for t in metrics["small"] if not (t["testid"] == "demo-banner-toggle" and t["height"] >= 24)]
        assert not small, f"{name}: targets below 44 px {small}"
    if require_banner:
        assert metrics["banner"] is not None, f"{name}: the demo notice is not visible"
        assert metrics["bannerOnTop"], f"{name}: something covers the demo notice"
        assert metrics["exitOnTop"], f"{name}: something covers the quick-exit button"
        assert metrics["headingOnTop"], f"{name}: something covers the page heading"
        e = metrics["exit"]
        assert e is not None and metrics["bannerText"], f"{name}: {metrics}"
        for t in metrics["bannerText"]:
            assert t["bottom"] <= e["top"] or t["right"] <= e["left"] or e["right"] <= t["left"], (
                f"{name}: the demo notice text overlaps the quick-exit button {t} {e}"
            )
    return metrics


def _assert_session_ended(page, base_url, how, seconds=15.0):
    """The logout beacon of a quick exit may still be in flight: poll for the redirect instead of looking once."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")
        if "/login/" in page.url or "/demo/" in page.url:
            return
        time.sleep(0.3)
    raise AssertionError(f"the session survived the {how}: {page.url}")


def _open_all_contacts(page):
    """Open every contact card and return how many there are.

    Each panel fades in over 150 ms. axe reads the colour through the running opacity (the
    muted text of the last panel measured 4.31:1 at an effective opacity of 0.69, 4.57:1 at rest),
    so the helper returns only when every opened panel rests at full opacity.
    """
    toggles = page.get_by_test_id("event-card-toggle")
    expect(toggles.first).to_be_visible(timeout=15000)
    for index in range(toggles.count()):
        toggle = toggles.nth(index)
        if toggle.get_attribute("aria-expanded") != "true":
            toggle.click()
            expect(toggle).to_have_attribute("aria-expanded", "true")
            panel = page.locator("#" + toggle.get_attribute("aria-controls"))
            expect(panel).to_be_visible()
            wait_for_transition_end(panel)
    return toggles.count()


def _tally_walk(page, base_url, width, role):
    """Direct tile → undo bar → undo; number tile → inline form → cancel. Returns what exists."""
    panel = page.get_by_test_id("tally-panel")
    if panel.count() == 0:
        return {"tiles": 0}
    direct = panel.locator("button[data-testid^='tally-increment-']")
    number = panel.locator("a[data-testid^='tally-increment-']")
    found = {
        "tiles": panel.locator("[data-testid^='tally-row-']").count(),
        "direct": direct.count(),
        "number": number.count(),
    }
    if found["direct"]:
        find_first_by_exact_testid(panel, direct).click()
        undo = page.get_by_test_id("tally-undo")
        expect(undo).to_be_visible(timeout=10000)
        _check_page(page, base_url, "tally-undo", width, role)
        undo.click()
        expect(page.get_by_test_id("tally-undo")).to_have_count(0, timeout=10000)
    if found["number"]:
        find_first_by_exact_testid(panel, number).click()
        slot = page.get_by_test_id("tally-number-form-slot")
        expect(slot.locator("form")).to_be_visible(timeout=10000)
        # A modal dialog covers the notice and the exit button by design (see the z-index note in
        # base.html); the shortcut stays available. Everything else is still measured.
        _check_page(page, base_url, "tally-number", width, role, require_banner=False)
        slot.locator("[data-testid^='tally-cancel-']").click()
        expect(slot.locator("form")).to_have_count(0, timeout=10000)
    return found


@pytest.mark.usefixtures("demo_profile")
@pytest.mark.parametrize(("width", "height"), WIDTHS)
@pytest.mark.parametrize(("role", "label"), ROLES)
def test_demo_pages_hold_up_for_each_role_and_width(browser, base_url, role, label, width, height):
    context = browser.new_context(locale="de-DE", viewport={"width": width, "height": height})
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        _start_demo(page, base_url, role)
        page.goto(f"{base_url}/account/", wait_until="domcontentloaded")
        expect(page.get_by_test_id("profile-role")).to_have_text(re.compile(rf"\s*{label}\s*"))

        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        expect(page.locator("#main-content h1")).to_be_visible()
        page.wait_for_load_state("networkidle")
        _check_page(page, base_url, "timeline", width, role)
        _record(page, f"demo-{role}-{width}-tally-found", _tally_walk(page, base_url, width, role))

        page.goto(f"{base_url}/events/", wait_until="domcontentloaded")
        expect(page.locator("#main-content h1")).to_be_visible()
        opened = _open_all_contacts(page)
        assert opened > 0, "the contact list must show at least one contact in the demo"
        _check_page(page, base_url, "contacts-open", width, role)

        page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")
        expect(page.locator("#main-content h1")).to_be_visible()
        _check_page(page, base_url, "people", width, role)

        # Log out through the visible control (desktop sidebar, compact "Mehr" menu).
        if width < 768:
            page.get_by_test_id("mobile-nav-more").click()
            menu = page.get_by_test_id("mobile-more-items")
            expect(menu).to_be_visible()
            wait_for_settled(menu)
            logout = menu.get_by_role("button", name="Abmelden")
        else:
            logout = page.locator("nav").get_by_role("button", name="Abmelden")
        expect(logout).to_be_visible()
        # offsetWidth/offsetHeight ignore the scale of the menu's enter transition (bounding_box does not).
        size = logout.evaluate("el => ({width: el.offsetWidth, height: el.offsetHeight})")
        _record(page, f"demo-{role}-{width}-logout-target", size)
        if width < 768:
            assert size["height"] >= 44, f"logout target is {size}"
        logout.click()
        page.wait_for_url(lambda url: "/login/" in url or "/demo/" in url, timeout=15000)
        _assert_session_ended(page, base_url, "logout")
    finally:
        context.close()


@pytest.mark.usefixtures("demo_profile")
@pytest.mark.parametrize(("role", "label"), ROLES)
def test_demo_quick_exit_leaves_and_ends_the_session(browser, base_url, role, label):
    context = browser.new_context(locale="de-DE", viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        _start_demo(page, base_url, role)
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        button = page.get_by_test_id("quick-exit-button")
        expect(button).to_be_visible()
        box = button.bounding_box()
        assert box["width"] >= 44 and box["height"] >= 44, f"quick-exit target is {box}"
        page.route(
            lambda url: not url.startswith(base_url),
            lambda route: route.fulfill(status=200, content_type="text/html; charset=utf-8", body=STUB_HTML),
        )
        with page.expect_navigation(url=lambda url: not url.startswith(base_url), timeout=15000):
            button.click()
        _assert_session_ended(page, base_url, "quick exit")
    finally:
        context.close()
