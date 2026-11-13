"""List reflow, field boundaries and keyboard focus at the 200 percent zoom equivalent.

1280 CSS pixels at DPR 1 become 640 CSS pixels at DPR 2. This exercises the
responsive breakpoints as browser zoom would; CSS zoom would keep them unchanged.
Optional evidence goes to E2E_EVIDENCE_DIR, never to the source tree by default.
"""

import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

PAGES = [
    pytest.param("/", "timeline", id="timeline"),
    pytest.param("/events/", "contacts", id="contacts"),
    pytest.param("/clients/", "people", id="people"),
    pytest.param("/workitems/?assigned_to=", "inbox", id="inbox"),
]
FIELDS = (
    "#main-content input:not([type=hidden]):not([type=checkbox]):not([type=radio]), "
    "#main-content select, #main-content textarea"
)


def _fields(page):
    return page.locator(FIELDS).evaluate_all("""els => els.filter(el => el.checkVisibility()).map(el => {
        const css = getComputedStyle(el), rect = el.getBoundingClientRect();
        return {key: el.id || el.name, colour: css.borderTopColor,
            border: parseFloat(css.borderTopWidth), style: css.borderTopStyle,
            width: rect.width, height: rect.height};
    })""")


def _open_filters(page, key, zoomed):
    if key == "contacts":
        page.locator("[data-filter-panel-toggle]").click()
        expect(page.locator("[data-filter-panel]")).to_have_attribute("open", "")
    elif key == "timeline" and zoomed:
        page.get_by_test_id("zeitstrom-selection-trigger").click()
        expect(page.get_by_test_id("zeitstrom-selection")).to_be_visible()


def _evidence(page, name, values):
    print(name + " " + json.dumps(values, sort_keys=True))
    directory = os.environ.get("E2E_EVIDENCE_DIR")
    if directory:
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target / f"{name}.png"), full_page=True)
        (target / f"{name}.json").write_text(json.dumps(values, indent=2) + "\n")


@pytest.mark.parametrize(("path", "key"), PAGES)
def test_list_fields_reflow_and_keep_boundaries_and_keyboard_focus(browser, base_url, _login_storage_state, path, key):
    snapshots = []
    for width, scale in [(1280, 1), (640, 2)]:
        context = browser.new_context(
            storage_state=_login_storage_state,
            locale="de-DE",
            viewport={"width": width, "height": 900 if scale == 1 else 450},
            device_scale_factor=scale,
        )
        try:
            page = context.new_page()
            page.set_default_timeout(15000)
            page.goto(base_url + path, wait_until="domcontentloaded")
            expect(page.locator("#main-content h1")).to_be_visible()
            if key == "inbox":
                expect(page.locator('#inbox-content [id^="workitem-"]').first).to_be_visible()
            _open_filters(page, key, scale == 2)
            fields = _fields(page)
            metrics = page.evaluate(r"""() => ({
                scroll: document.documentElement.scrollWidth, viewport: document.documentElement.clientWidth,
                dpr: window.devicePixelRatio,
                clippedLabels: Array.from(document.querySelectorAll('#main-content h1, #main-content label'))
                    .filter(el => el.checkVisibility() && el.clientWidth > 1 && (el.scrollWidth > el.clientWidth + 1 ||
                        el.scrollHeight > el.clientHeight + 1)).map(el => el.textContent.trim()),
                clippedTitles: Array.from(document.querySelectorAll(
                    '#main-content [id^="workitem-"] a[href^="/workitems/"]'))
                    .filter(el => el.checkVisibility()).filter(el => {
                        const text = el.querySelector('span') || el;
                        return text.scrollWidth > text.clientWidth + 1 || text.scrollHeight > text.clientHeight + 1;
                    }).map(el => el.textContent.trim()),
                smallTargets: Array.from(document.querySelectorAll('#main-content a[href], ' +
                    '#main-content button:not(:disabled), #main-content summary, ' +
                    '#main-content input[type=checkbox]'))
                    .filter(el => el.checkVisibility()).map(el => {
                        const label = el.type === 'checkbox' && (el.closest('label') ||
                            (el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)));
                        const r = (label || el).getBoundingClientRect();
                        return {name: (el.getAttribute('aria-label') || (label || el).textContent).trim()
                                .replace(/\s+/g, ' '),
                            width: r.width, height: r.height};
                    }).filter(r => r.width < 44 || r.height < 44),
            })""")
            metrics["fields"] = fields
            snapshots.append(fields)
            _evidence(page, f"{key}-{scale}x", metrics)
            assert metrics["dpr"] == scale, metrics
            assert metrics["scroll"] <= metrics["viewport"], metrics
            assert not metrics["clippedLabels"], metrics
            assert fields, metrics
            assert all(field["border"] >= 1 and field["style"] == "solid" for field in fields), fields
            if scale == 2:
                assert all(field["width"] >= 44 and field["height"] >= 44 for field in fields), fields
                assert not metrics["smallTargets"], metrics["smallTargets"]
                assert not metrics["clippedTitles"], metrics["clippedTitles"]
            # Reach each field with Tab, recording the indicator at the actual stop.
            focus_selector = FIELDS
            if key == "contacts" or (key == "timeline" and scale == 2):
                scope = "[data-filter-panel-body]" if key == "contacts" else "[data-testid='zeitstrom-selection']"
                focus_selector = f"{scope} input:not([type=hidden]), {scope} select, {scope} textarea"
            focus_keys = {
                field["key"]
                for field in page.locator(focus_selector).evaluate_all(
                    "els => els.filter(el => el.checkVisibility()).map(el => ({key: el.id || el.name}))"
                )
            }
            assert focus_keys, fields
            reached = set()
            for _ in range(120):
                page.keyboard.press("Tab")
                focus = page.evaluate(
                    """selector => {
                    const el = document.activeElement;
                    if (!el || !el.matches(selector)) return null;
                    const css = getComputedStyle(el), r = el.getBoundingClientRect();
                    return {key: el.id || el.name, left: r.left, right: r.right,
                        top: r.top, bottom: r.bottom, height: innerHeight,
                        outline: css.outlineStyle !== 'none' && parseFloat(css.outlineWidth) >= 2,
                        shadow: css.boxShadow !== 'none' && css.boxShadow.includes('2px')};
                }""",
                    focus_selector,
                )
                if focus:
                    assert focus["outline"] or focus["shadow"], focus
                    assert focus["left"] >= 0 and focus["right"] <= width, focus
                    assert focus["top"] >= 0 and focus["bottom"] <= focus["height"], focus
                    reached.add(focus["key"])
                if focus_keys <= reached:
                    break
            assert focus_keys <= reached, (focus_keys, reached)
            _evidence(page, f"{key}-{scale}x-focused", {"reached": sorted(reached)})
        finally:
            context.close()
    before = {field["key"]: (field["colour"], field["border"], field["style"]) for field in snapshots[0]}
    after = {field["key"]: (field["colour"], field["border"], field["style"]) for field in snapshots[1]}
    assert before == after, (before, after)
