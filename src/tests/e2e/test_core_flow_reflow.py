"""E2E: Core flows at zoom-equivalent and small windows (#2563, automatable part).

Playwright has no browser zoom. WCAG reflow is approximated by the CSS viewport
(200 % -> 640 px, 400 % -> 320 px) plus 667x375 landscape and 390 px phones. The
measurements are DOM geometry only: ``scrollWidth <= clientWidth`` of the document and
the bounding box of the focused element inside the horizontal viewport during a Tab
walk. Real browser zoom, large system fonts and screen readers are not covered.
"""

from __future__ import annotations

import uuid

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell

pytestmark = pytest.mark.e2e

VIEWPORTS = [(640, 480), (320, 568), (667, 375), (390, 844)]
TAB_STEPS = 14

_MEASURE = """() => {
    const doc = document.documentElement;
    const active = document.activeElement;
    const box = active && active !== document.body ? active.getBoundingClientRect() : null;
    return {
        scrollWidth: doc.scrollWidth,
        clientWidth: doc.clientWidth,
        innerWidth: window.innerWidth,
        focus: box ? {left: box.left, right: box.right, width: box.width, tag: active.tagName,
                      text: (active.getAttribute('aria-label') || active.textContent || '').trim().slice(0, 40)} : null,
    };
}"""


@pytest.fixture
def flow_scene(e2e_env):
    marker = uuid.uuid4().hex[:6]
    script = (
        "from core.models import Case, Client, Facility, User; "
        "f = Facility.objects.get(name='Hauptstelle'); u = User.objects.get(username='miriam'); "
        f"c = Client.objects.create(facility=f, contact_stage='identified', pseudonym='REFLOW-{marker}', "
        "created_by=u); "
        f"ca = Case.objects.create(facility=f, client=c, title='REFLOW-Fall-{marker}', created_by=u, lead_user=u); "
        "print('client=' + str(c.pk)); print('case=' + str(ca.pk))"
    )
    values = {"marker": marker}
    for line in django_shell(e2e_env, script):
        key, _, value = line.partition("=")
        values[key] = value
    return values


def _walk_and_measure(page, label):
    """Tab through the page; every measurement must stay inside the horizontal viewport."""
    findings = []
    start = page.evaluate(_MEASURE)
    assert start["scrollWidth"] <= start["clientWidth"], f"{label}: horizontal scrolling {start}"
    for step in range(TAB_STEPS):
        page.keyboard.press("Tab")
        measured = page.evaluate(_MEASURE)
        assert measured["scrollWidth"] <= measured["clientWidth"], f"{label}: step {step}: {measured}"
        focus = measured["focus"]
        if focus and focus["width"] > 0:
            if focus["left"] < -1 or focus["right"] > measured["innerWidth"] + 1:
                findings.append((step, focus))
    assert not findings, f"{label}: focused element outside the horizontal viewport: {findings}"


@pytest.mark.parametrize(("width", "height"), VIEWPORTS, ids=[f"{w}x{h}" for w, h in VIEWPORTS])
class TestCoreFlowsReflow:
    def _page(self, browser, state, width, height):
        context = browser.new_context(storage_state=state, locale="de-DE", viewport={"width": width, "height": height})
        page = context.new_page()
        page.set_default_timeout(20000)
        return context, page

    def test_contact_form_and_person_search(self, browser, base_url, _staff_storage_state, flow_scene, width, height):
        context, page = self._page(browser, _staff_storage_state, width, height)
        try:
            page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
            expect(page.locator("#event-submit-btn")).to_be_enabled(timeout=10000)
            _walk_and_measure(page, f"contact form {width}x{height}")
            search = page.locator("input[aria-label='Person suchen']")
            search.fill(f"REFLOW-{flow_scene['marker']}")
            expect(page.locator("[role='listbox'] [role='option']").first).to_be_visible(timeout=10000)
            data = page.evaluate(_MEASURE)
            assert data["scrollWidth"] <= data["clientWidth"], f"open result list {width}x{height}: {data}"
            option_box = page.locator("[role='listbox'] [role='option']").first.bounding_box()
            assert option_box and option_box["x"] >= -1 and option_box["x"] + option_box["width"] <= width + 1, (
                option_box
            )
        finally:
            context.close()

    def test_inline_person_dialog(self, browser, base_url, _staff_storage_state, flow_scene, width, height):
        context, page = self._page(browser, _staff_storage_state, width, height)
        try:
            page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
            search = page.locator("input[aria-label='Person suchen']")
            search.fill(f"NOHIT-{flow_scene['marker']}")
            create = page.locator("[data-testid='client-autocomplete-create']")
            expect(create).to_be_visible(timeout=10000)
            create.click()
            dialog = page.locator("#client-inline-create-modal [role='dialog']")
            expect(dialog).to_be_visible(timeout=10000)
            box = dialog.bounding_box()
            assert box and box["x"] >= -1 and box["x"] + box["width"] <= width + 1, f"dialog outside viewport: {box}"
            # Every action of the dialog stays reachable by scrolling inside it or the page.
            for selector in ("#id_pseudonym", "[data-inline-client-cancel]", "button[type='submit']"):
                target = dialog.locator(selector).first
                target.scroll_into_view_if_needed()
                assert target.is_visible(), f"{selector} not reachable at {width}x{height}"
                target_box = target.bounding_box()
                assert target_box and target_box["x"] >= -1 and target_box["x"] + target_box["width"] <= width + 1
        finally:
            context.close()

    def test_task_inbox_and_goals(self, browser, base_url, _staff_storage_state, flow_scene, width, height):
        context, page = self._page(browser, _staff_storage_state, width, height)
        try:
            page.goto(f"{base_url}/workitems/", wait_until="domcontentloaded")
            expect(page.locator("#main-content")).to_be_visible()
            _walk_and_measure(page, f"task inbox {width}x{height}")
            page.goto(f"{base_url}/cases/{flow_scene['case']}/", wait_until="domcontentloaded")
            expect(page.locator("#main-content")).to_be_visible()
            _walk_and_measure(page, f"case file with goals {width}x{height}")
        finally:
            context.close()

    def test_form_configuration_board(self, browser, base_url, _login_storage_state, e2e_env, width, height):
        pk = next(
            line
            for line in django_shell(
                e2e_env,
                "from core.models import DocumentType; "
                "print('pk=' + str(DocumentType.objects.filter(name='Kontakt').first().pk))",
            )
            if line.startswith("pk=")
        ).removeprefix("pk=")
        context, page = self._page(browser, _login_storage_state, width, height)
        try:
            page.goto(f"{base_url}/config/document-types/{pk}/", wait_until="domcontentloaded")
            expect(page.locator("#field-board")).to_be_visible(timeout=10000)
            data = page.evaluate(_MEASURE)
            assert data["scrollWidth"] <= data["clientWidth"], f"field board {width}x{height}: {data}"
            # Order and group buttons of every row are reachable without drag and drop.
            buttons = page.locator("#field-board [data-board-order='up'], #field-board [data-board-order='down']")
            assert buttons.count() > 0
            first = buttons.first
            first.scroll_into_view_if_needed()
            box = first.bounding_box()
            assert box and box["x"] >= -1 and box["x"] + box["width"] <= width + 1, f"order button outside: {box}"
        finally:
            context.close()
