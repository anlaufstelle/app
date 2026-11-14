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

from ._selectors import find_first_by_exact_testid

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


# --- Quick-exit button, opened contact cards and tally actions at 200 percent -------------------

ZOOM_VIEWPORT = {"width": 640, "height": 450}
_TARGETS = r"""scope => Array.from(document.querySelectorAll(scope)).filter(el => el.checkVisibility()).map(el => {
    const r = el.getBoundingClientRect();
    return {name: (el.getAttribute('aria-label') || el.textContent || el.name || '').trim()
            .replace(/\s+/g, ' ').slice(0, 60), tag: el.tagName.toLowerCase(), testid: el.getAttribute('data-testid'),
        width: r.width, height: r.height, left: r.left, right: r.right};
})"""
_CLICKABLE = "a[href], button:not(:disabled), summary, input:not([type=hidden]), select, textarea"


def _scoped(prefix):
    return ", ".join(f"{prefix} {part.strip()}" for part in _CLICKABLE.split(","))


def _zoomed_page(browser, storage_state):
    context = browser.new_context(
        storage_state=storage_state, locale="de-DE", viewport=ZOOM_VIEWPORT, device_scale_factor=2
    )
    page = context.new_page()
    page.set_default_timeout(15000)
    return context, page


def _assert_targets(targets, where):
    small = [t for t in targets if t["width"] < 44 or t["height"] < 44]
    outside = [t for t in targets if t["left"] < -0.5 or t["right"] > ZOOM_VIEWPORT["width"] + 0.5]
    assert targets, f"{where}: no target found"
    assert not small, f"{where}: targets below 44 px {small}"
    assert not outside, f"{where}: targets outside the viewport {outside}"


@pytest.mark.parametrize(("path", "key"), PAGES)
def test_quick_exit_button_keeps_its_target_free_at_200_percent(browser, base_url, _login_storage_state, path, key):
    context, page = _zoomed_page(browser, _login_storage_state)
    try:
        page.goto(base_url + path, wait_until="domcontentloaded")
        expect(page.locator("#main-content h1")).to_be_visible()
        button = page.get_by_test_id("quick-exit-button")
        expect(button).to_be_visible()
        values = page.evaluate(r"""() => {
            const el = document.getElementById('quick-exit-button'), r = el.getBoundingClientRect();
            const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
            const css = getComputedStyle(el);
            return {dpr: devicePixelRatio, width: r.width, height: r.height, left: r.left, right: r.right,
                top: r.top, bottom: r.bottom, viewport: document.documentElement.clientWidth,
                scroll: document.documentElement.scrollWidth, clipped: el.scrollWidth > el.clientWidth + 1,
                free: !!hit && (hit === el || el.contains(hit)), label: el.textContent.trim()};
        }""")
        _evidence(page, f"quick-exit-{key}-2x", values)
        assert values["dpr"] == 2, values
        assert values["width"] >= 44 and values["height"] >= 44, values
        assert values["left"] >= 0 and values["right"] <= values["viewport"] and values["top"] >= 0, values
        assert values["scroll"] <= values["viewport"] and not values["clipped"], values
        assert values["free"], f"something covers the quick-exit button on {path}: {values}"
        button.focus()
        focus = page.evaluate("""() => { const css = getComputedStyle(document.activeElement);
            return {id: document.activeElement.id,
                outline: css.outlineStyle !== 'none' && parseFloat(css.outlineWidth) >= 2,
                shadow: css.boxShadow !== 'none' && css.boxShadow.includes('2px')}; }""")
        assert focus["id"] == "quick-exit-button" and (focus["outline"] or focus["shadow"]), focus
    finally:
        context.close()


def test_opened_contact_cards_keep_their_targets_at_200_percent(browser, base_url, _login_storage_state):
    context, page = _zoomed_page(browser, _login_storage_state)
    try:
        page.goto(base_url + "/events/", wait_until="domcontentloaded")
        toggles = page.get_by_test_id("event-card-toggle")
        expect(toggles.first).to_be_visible()
        count = toggles.count()
        assert count > 0
        for index in range(count):
            toggle = toggles.nth(index)
            if toggle.get_attribute("aria-expanded") != "true":
                toggle.click()
                expect(toggle).to_have_attribute("aria-expanded", "true")
        targets = page.evaluate(_TARGETS, _scoped("#event-table"))
        metrics = page.evaluate("""() => ({scroll: document.documentElement.scrollWidth,
            viewport: document.documentElement.clientWidth, dpr: devicePixelRatio,
            clipped: Array.from(document.querySelectorAll('#event-table [id^=event-detail-] *'))
                .filter(el => el.checkVisibility() && el.children.length === 0 && el.clientWidth > 1 &&
                    el.scrollWidth > el.clientWidth + 1).map(el => el.textContent.trim().slice(0, 60))})""")
        _evidence(page, "contacts-opened-2x", {"cards": count, "targets": len(targets), **metrics})
        assert metrics["dpr"] == 2 and metrics["scroll"] <= metrics["viewport"], metrics
        assert not metrics["clipped"], metrics["clipped"]
        _assert_targets(targets, f"{count} opened contact cards")
        assert any(t["name"] == "Zum Eintrag" for t in targets), "the opened cards must offer their entry link"
    finally:
        context.close()


def test_tally_actions_keep_their_targets_at_200_percent(browser, base_url, _staff_storage_state):
    context, page = _zoomed_page(browser, _staff_storage_state)
    try:
        page.goto(base_url + "/", wait_until="domcontentloaded")
        panel = page.get_by_test_id("tally-panel")
        expect(panel).to_be_visible()
        tiles = panel.locator("[data-testid^='tally-row-']")
        assert tiles.count() > 0, "the tally panel must show tiles for this account"
        direct = panel.locator("button[data-testid^='tally-increment-']")
        number = panel.locator("a[data-testid^='tally-increment-']")
        assert direct.count() > 0 and number.count() > 0, "the seed must offer a +1 tile and a number tile"
        actions = page.evaluate(_TARGETS, "#tally-panel [data-testid^='tally-increment-']")
        _assert_targets(actions, "tally tile actions")
        tile_metrics = page.evaluate("""() => ({scroll: document.documentElement.scrollWidth,
            viewport: document.documentElement.clientWidth,
            clipped: Array.from(document.querySelectorAll('#tally-panel [data-testid^=tally-name-], ' +
                '#tally-panel [data-testid^=tally-count-]')).filter(el => el.checkVisibility() &&
                el.scrollWidth > el.clientWidth + 1).map(el => el.textContent.trim())})""")
        _evidence(page, "tally-tiles-2x", {"tiles": tiles.count(), "actions": len(actions), **tile_metrics})
        assert tile_metrics["scroll"] <= tile_metrics["viewport"], tile_metrics
        assert not tile_metrics["clipped"], tile_metrics["clipped"]

        find_first_by_exact_testid(panel, direct).click()
        undo = page.get_by_test_id("tally-undo")
        expect(undo).to_be_visible()
        bar = page.evaluate(_TARGETS, "#tally-undo-slot a[href], #tally-undo-slot button")
        _assert_targets(bar, "tally undo bar")
        undo.click()
        expect(page.get_by_test_id("tally-undo")).to_have_count(0)

        find_first_by_exact_testid(panel, number).click()
        slot = page.get_by_test_id("tally-number-form-slot")
        expect(slot.locator("form")).to_be_visible()
        form = page.evaluate(_TARGETS, _scoped("#tally-number-form-slot"))
        _assert_targets(form, "tally number form")
        _evidence(page, "tally-number-form-2x", {"targets": len(form)})
        slot.locator("[data-testid^='tally-cancel-']").click()
    finally:
        context.close()
