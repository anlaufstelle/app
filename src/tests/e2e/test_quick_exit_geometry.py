"""E2E: the quick-exit button covers no control on any width (Refs #2660).

The button is a corner button that reaches into the page (about y = 4..48 px at
the right edge). This spec measures the real boxes: for five widths and for the
pages that matter, no visible ``a``, ``button``, ``input`` or ``select`` may
overlap the button's bounding box, and the button itself must be the topmost
element at its own centre.

Pages: the start page, a list with a "new" action, the configuration lists
whose title row carries action buttons, and the static offline shell with its
navigation. Run without demo mode (the demo notice has its own reserve and is
measured by ``test_demo_banner``-style specs); with ``E2E_DEMO_MODE=1`` the
spec skips.

WICHTIG: E2E seriell ausfuehren (RAM-Limit der Container).
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.e2e

WIDTHS = [390, 640, 768, 1024, 1440]
HEIGHT = 900

# Online pages (admin session) and the signed-out offline shell.
ONLINE_PATHS = [
    "/",
    "/clients/",
    "/config/document-types/",
    "/config/field-templates/",
    "/import/",
    "/verwaltung/zugaenge/",
]
OFFLINE_SHELL_PATH = "/offline/client-shell/"

_OVERLAPS = """
() => {
    const button = document.getElementById("quick-exit-button");
    const b = button.getBoundingClientRect();
    const hits = [];
    for (const el of document.querySelectorAll("a, button, input, select")) {
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
    const cx = (b.left + b.right) / 2, cy = (b.top + b.bottom) / 2;
    const top = document.elementFromPoint(cx, cy);
    return {
        button: [Math.round(b.left), Math.round(b.top), Math.round(b.right), Math.round(b.bottom)],
        buttonOnTop: !!top && (top === button || button.contains(top)),
        hits,
    };
}
"""


def _measure(page, label):
    page.locator("#quick-exit-button").wait_for(state="visible")
    result = page.evaluate(_OVERLAPS)
    left, top, right, bottom = result["button"]
    assert right <= page.viewport_size["width"], f"{label}: the button leaves the viewport {result['button']}"
    assert 0 <= top < 16 and bottom <= 56, f"{label}: the button is not in the top corner {result['button']}"
    assert result["buttonOnTop"], f"{label}: another element sits on top of the button centre"
    assert not result["hits"], f"{label}: the button {result['button']} covers {result['hits']}"


@pytest.fixture
def _without_demo_mode():
    if os.environ.get("E2E_DEMO_MODE") == "1":
        pytest.skip("The geometry is measured without the demo notice")


@pytest.mark.usefixtures("_without_demo_mode")
class TestQuickExitGeometry:
    @pytest.mark.parametrize("width", WIDTHS)
    @pytest.mark.parametrize("path", ONLINE_PATHS)
    def test_button_covers_no_control_on_online_pages(self, browser, base_url, _login_storage_state, path, width):
        context = browser.new_context(
            storage_state=_login_storage_state,
            locale="de-DE",
            viewport={"width": width, "height": HEIGHT},
            service_workers="block",
        )
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            response = page.goto(f"{base_url}{path}", wait_until="load")
            assert response is not None and response.ok, f"{path}: HTTP {response and response.status}"
            assert page.locator("[data-testid='demo-banner']").count() == 0, "run without demo mode"
            _measure(page, f"{path} @ {width}px")
        finally:
            context.close()

    @pytest.mark.parametrize("width", WIDTHS)
    def test_button_covers_no_control_in_the_offline_shell(self, browser, base_url, width):
        context = browser.new_context(
            locale="de-DE",
            viewport={"width": width, "height": HEIGHT},
            service_workers="block",
        )
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            response = page.goto(f"{base_url}{OFFLINE_SHELL_PATH}", wait_until="load")
            assert response is not None and response.ok
            page.locator("[data-testid='offline-nav']").wait_for(state="visible")
            _measure(page, f"offline shell @ {width}px")
        finally:
            context.close()
