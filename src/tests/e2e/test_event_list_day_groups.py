"""Browser acceptance of day groups, result counts and grouped contact rows."""

import json
import os
import re
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from ._axe import run_axe
from ._helpers import django_shell
from ._selectors import wait_for_transition_end
from .test_a11y_reach_and_contrast import _set_user_field
from .test_event_list import filter_by, open_filter_panel

pytestmark = pytest.mark.e2e

RANGE = "date_from=2020-10-22&date_to=2020-10-24"
ROLES = [
    pytest.param("assistant_page", 26, False, id="assistant"),
    pytest.param("staff_page", 27, True, id="staff"),
    pytest.param("lead_page", 28, True, id="lead"),
    pytest.param("authenticated_page", 28, True, id="admin"),
]


@pytest.fixture(scope="module")
def grouped_contacts(e2e_env):
    """Keep the middle day split at the real 25-row pagination boundary."""
    marker = "Day-groups-" + uuid.uuid4().hex[:8]
    lines = django_shell(
        e2e_env,
        f"""
import json
from datetime import datetime
from django.utils import timezone
from core.models import Case, Client, DocumentType, Event, Facility, User

facility = Facility.objects.get(name='Hauptstelle')
actor = User.objects.get(username='miriam')
person = Client.objects.create(facility=facility, pseudonym='{marker}', contact_stage='identified', created_by=actor)
case = Case.objects.create(facility=facility, client=person, title='{marker}', created_by=actor)
types = {{}}
for sensitivity in ('normal', 'elevated', 'high'):
    types[sensitivity] = DocumentType.objects.create(
        facility=facility, name='{marker}-' + sensitivity, entity_type='event', sensitivity=sensitivity,
    )
for day, count in ((24, 1), (23, 26), (22, 1)):
    for minute in range(count):
        Event.objects.create(
            facility=facility, document_type=types['normal'], client=person, case=case, created_by=actor,
            occurred_at=timezone.make_aware(datetime(2020, 10, day, 12, minute)), data_json={{}},
        )
for sensitivity in ('elevated', 'high'):
    Event.objects.create(
        facility=facility, document_type=types[sensitivity], client=person, case=case, created_by=actor,
        occurred_at=timezone.make_aware(datetime(2020, 10, 23, 13)), data_json={{}},
    )
print('DATA ' + json.dumps({{key: str(value.pk) for key, value in types.items()}}))
""",
    )
    data = json.loads(next(line.removeprefix("DATA ") for line in lines if line.startswith("DATA ")))
    data["marker"] = marker
    try:
        yield data
    finally:
        django_shell(
            e2e_env,
            f"""
from django.db import connection
from core.models import Case, Client, DocumentType, Event, EventHistory
with connection.cursor() as cursor:
    cursor.execute('ALTER TABLE core_eventhistory DISABLE TRIGGER eventhistory_no_delete')
    try:
        EventHistory.objects.filter(event__document_type__name__startswith='{marker}').delete()
        Event.objects.filter(document_type__name__startswith='{marker}').delete()
    finally:
        cursor.execute('ALTER TABLE core_eventhistory ENABLE ALWAYS TRIGGER eventhistory_no_delete')
Case.objects.filter(title='{marker}').delete()
DocumentType.objects.filter(name__startswith='{marker}').delete()
Client.objects.filter(pseudonym='{marker}').delete()
""",
        )


def _open(page, base_url):
    page.goto(f"{base_url}/events/?{RANGE}", wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="Kontakte", exact=True)).to_be_visible()
    page.wait_for_load_state("load")


def _count(page, count):
    counter = page.locator("#event-result-count")
    expect(counter).to_have_count(1)
    expect(counter).to_have_attribute("role", "status")
    expect(counter).to_have_text(f"{count} Treffer")


@pytest.mark.parametrize(("role_fixture", "middle_count", "has_case"), ROLES)
def test_split_middle_day_keeps_complete_role_visible_counts(
    request, base_url, grouped_contacts, role_fixture, middle_count, has_case
):
    page = request.getfixturevalue(role_fixture)
    _open(page, base_url)
    _count(page, middle_count + 2)
    headings = page.get_by_role("heading", level=2)
    expect(headings).to_have_text(["Sa, 24. Okt — 1 Kontakt", f"Fr, 23. Okt — {middle_count} Kontakte"])
    toggles = page.get_by_test_id("event-card-toggle")
    expect(toggles).to_have_count(25)
    expect(toggles.first).to_have_accessible_name(
        re.compile(rf"Zeit: 12:00 · Typ: {grouped_contacts['marker']}-normal · Person: {grouped_contacts['marker']}")
    )
    label = toggles.first.get_attribute("aria-label")
    assert (f"Fall: {grouped_contacts['marker']}" in label) is has_case
    assert "Angaben:" in label and "Erfasst von:" in label
    first_ids = toggles.evaluate_all("els => els.map(el => el.getAttribute('aria-controls'))")

    page.get_by_role("link", name="Nächste Seite").click()
    expect(headings).to_have_text([f"Fr, 23. Okt — {middle_count} Kontakte", "Do, 22. Okt — 1 Kontakt"])
    _count(page, middle_count + 2)
    expect(toggles).to_have_count(middle_count + 2 - 25)
    second_ids = toggles.evaluate_all("els => els.map(el => el.getAttribute('aria-controls'))")
    assert len(set(first_ids + second_ids)) == middle_count + 2
    page.get_by_role("link", name="Vorherige Seite").click()
    expect(headings).to_have_text(["Sa, 24. Okt — 1 Kontakt", f"Fr, 23. Okt — {middle_count} Kontakte"])


def test_live_counter_including_zero_and_history(staff_page, base_url, grouped_contacts):
    page = staff_page
    _open(page, base_url)
    _count(page, 29)
    open_filter_panel(page)
    filter_by(page, lambda: page.locator("#event-doc_type").select_option(grouped_contacts["elevated"]))
    _count(page, 1)
    expect(page.get_by_role("heading", level=2)).to_have_text("Fr, 23. Okt — 1 Kontakt")
    filter_by(page, lambda: page.get_by_role("searchbox", name="Suchbegriff").press_sequentially("no-such-contact"))
    _count(page, 0)
    expect(page.get_by_test_id("event-card-toggle")).to_have_count(0)
    expect(page.locator("#event-table")).to_have_text("Keine Kontakte gefunden.")

    page.go_back()
    _count(page, 1)
    expect(page.get_by_test_id("event-card-toggle")).to_have_count(1)
    page.go_back()
    _count(page, 29)
    expect(page.get_by_test_id("event-card-toggle")).to_have_count(25)
    page.go_forward()
    _count(page, 1)
    page.go_forward()
    _count(page, 0)
    expect(page.locator("#event-table")).to_have_text("Keine Kontakte gefunden.")


def test_second_filter_immediately_after_oob_updates_counter(staff_page, base_url, grouped_contacts):
    page = staff_page
    _open(page, base_url)
    open_filter_panel(page)
    page.evaluate(
        """nextType => {
            const followUp = event => {
                if (event.target.id !== 'event-filters') return;
                document.removeEventListener('htmx:oobAfterSwap', followUp);
                setTimeout(() => {
                    const select = document.querySelector('#event-doc_type');
                    select.value = nextType;
                    select.dispatchEvent(new Event('change', {bubbles: true}));
                }, 0);
            };
            document.addEventListener('htmx:oobAfterSwap', followUp);
        }""",
        grouped_contacts["normal"],
    )
    filter_by(page, lambda: page.locator("#event-doc_type").select_option(grouped_contacts["elevated"]))
    expect(page).to_have_url(re.compile(rf"doc_type={grouped_contacts['normal']}"))
    _count(page, 28)
    expect(page.get_by_role("heading", level=2)).to_have_text(["Sa, 24. Okt — 1 Kontakt", "Fr, 23. Okt — 26 Kontakte"])
    expect(page.get_by_test_id("event-card-toggle")).to_have_count(25)


def _assert_pagination_targets(page):
    """Walk to the next page and back, checking the 44 px touch target of each pagination link."""
    next_page_box = page.get_by_role("link", name="Nächste Seite").bounding_box()
    assert next_page_box and next_page_box["width"] >= 44 and next_page_box["height"] >= 44, next_page_box
    page.get_by_role("link", name="Nächste Seite").click()
    # The link is visible as soon as it is parsed; measure only once the document and its
    # stylesheet have loaded.
    page.wait_for_load_state("load")
    previous_page = page.get_by_role("link", name="Vorherige Seite")
    expect(previous_page).to_be_visible()
    previous_page_box = previous_page.bounding_box()
    assert previous_page_box and previous_page_box["width"] >= 44 and previous_page_box["height"] >= 44, (
        previous_page_box
    )
    previous_page.click()
    page.wait_for_load_state("load")
    expect(page.get_by_role("link", name="Nächste Seite")).to_be_visible()


def test_pagination_targets_are_measured_on_the_loaded_page(staff_page, base_url, grouped_contacts):
    """A slow stylesheet must not make a pagination link look smaller than its touch target.

    The pagination links are plain links, so each click loads a new document. The link is
    visible as soon as it is parsed; the stylesheet may still be on its way and the link is
    then measured unstyled. A cold cache and a slow network widen that window.
    """
    page = staff_page
    page.set_viewport_size({"width": 390, "height": 900})
    _open(page, base_url)
    cdp = page.context.new_cdp_session(page)
    cdp.send("Network.enable")
    cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
    cdp.send(
        "Network.emulateNetworkConditions",
        {"offline": False, "latency": 1500, "downloadThroughput": -1, "uploadThroughput": -1},
    )
    try:
        _assert_pagination_targets(page)
    finally:
        cdp.send(
            "Network.emulateNetworkConditions",
            {"offline": False, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1},
        )
        cdp.send("Network.setCacheDisabled", {"cacheDisabled": False})


@pytest.mark.parametrize("width", [1280, 390], ids=["desktop", "mobile"])
def test_grouped_row_layout_keyboard_expansion_and_detail_link(staff_page, base_url, grouped_contacts, width):
    page = staff_page
    page.set_viewport_size({"width": width, "height": 900})
    _open(page, base_url)
    column_heading = page.locator("[data-event-columns]")
    if width == 1280:
        expect(column_heading).to_be_visible()
        expect(column_heading).to_contain_text("Zeit Typ Person Angaben Erfasst von")
    else:
        expect(column_heading).to_be_hidden()
        _assert_pagination_targets(page)
    toggle = page.get_by_test_id("event-card-toggle").first
    geometry = toggle.evaluate("""el => {
        const cell = name => el.querySelector(`[data-feed-column='${name}']`).getBoundingClientRect();
        const time = cell('time'), details = cell('details'), row = el.getBoundingClientRect();
        return {timeTop: time.top, timeBottom: time.bottom, detailsTop: details.top,
            width: row.width, height: row.height,
            timeText: el.querySelector('[data-feed-column=time]').textContent.trim(),
            scroll: document.documentElement.scrollWidth, viewport: document.documentElement.clientWidth};
    }""")
    assert geometry["scroll"] <= geometry["viewport"], geometry
    assert geometry["width"] >= 44 and geometry["height"] >= 44, geometry
    assert geometry["timeText"] == "12:00"
    if width == 390:
        assert geometry["detailsTop"] >= geometry["timeBottom"], geometry
    else:
        assert abs(geometry["detailsTop"] - geometry["timeTop"]) <= 2, geometry
    for _ in range(80):
        page.keyboard.press("Tab")
        if toggle.evaluate("el => el === document.activeElement"):
            break
    expect(toggle).to_be_focused()
    scan = run_axe(page, base_url)
    assert not scan.violations, scan.violations
    detail_id = toggle.get_attribute("aria-controls")
    details = page.locator(f"#{detail_id}")
    expect(details).to_be_hidden()
    page.keyboard.press("Enter")
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(details).to_be_visible()
    page.keyboard.press("Space")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(details).to_be_hidden()
    page.keyboard.press("Enter")
    link = details.get_by_role("link", name="Zum Eintrag")
    expect(link).to_be_visible()
    wait_for_transition_end(details)
    if width == 390:
        link_box = link.bounding_box()
        assert link_box and link_box["width"] >= 44 and link_box["height"] >= 44, link_box
    directory = os.environ.get("E2E_EVIDENCE_DIR")
    if directory:
        (Path(directory) / f"contact-detail-opacity-{width}.txt").write_text(
            details.evaluate("el => getComputedStyle(el).opacity") + "\n"
        )
    expect(details).to_have_css("opacity", "1")
    scan = run_axe(page, base_url)
    assert not scan.violations, scan.violations
    page.keyboard.press("Tab")
    expect(link).to_be_focused()
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(rf"/events/{detail_id.removeprefix('event-detail-')}/$"))
    expect(page.locator("#main-content h1")).to_be_visible()


# Stretches the 150 ms enter transition of the detail panel so that the in-flight state is
# observable for a whole test step. Only the ``.transition`` class is slowed: Alpine removes it
# when the transition ends, so no trailing transition runs on the resting panel.
SLOW_DETAIL_TRANSITION = "[id^='event-detail-'].transition { transition-duration: 4s !important; }"


def _expand_first_card_with_slow_transition(page, base_url):
    """Open the first card with the detail transition stretched and return panel and link."""
    page.set_viewport_size({"width": 390, "height": 900})
    _open(page, base_url)
    # Constructed stylesheets are CSSOM, so the page's inline-style CSP does not block them.
    page.evaluate(
        """css => {
            const sheet = new CSSStyleSheet();
            sheet.replaceSync(css);
            document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
        }""",
        SLOW_DETAIL_TRANSITION,
    )
    toggle = page.get_by_test_id("event-card-toggle").first
    details = page.locator(f"#{toggle.get_attribute('aria-controls')}")
    toggle.focus()
    page.keyboard.press("Enter")
    link = details.get_by_role("link", name="Zum Eintrag")
    expect(link).to_be_visible()
    # The slowed transition is still running here, which is the state the old measurement hit.
    # Alpine swaps the enter-start class for enter-end only on the next animation frame, so the
    # transition and its animation object start one frame after the click; a one-off read can
    # come back 0 although the link is already visible: wait for it. The
    # 4 s stretch keeps the window open far longer than the default timeout needs to see it.
    page.wait_for_function("el => el.getAnimations().length > 0", arg=details.element_handle(), timeout=5000)
    return details, link


def test_detail_link_target_is_measured_after_the_expand_transition(staff_page, base_url, grouped_contacts):
    """A 44 px target must measure 44 px once the transition ended, not 43.99997 on the way."""
    page = staff_page
    details, link = _expand_first_card_with_slow_transition(page, base_url)
    wait_for_transition_end(details)
    assert details.evaluate("el => el.getAnimations().length") == 0
    expect(details).to_have_css("opacity", "1")
    link_box = link.bounding_box()
    assert link_box and link_box["width"] >= 44 and link_box["height"] >= 44, link_box


def test_resting_detail_link_below_44px_is_still_rejected(staff_page, base_url, grouped_contacts):
    """Waiting for the end state adds no tolerance: a real 43 px target keeps failing."""
    page = staff_page
    details, link = _expand_first_card_with_slow_transition(page, base_url)
    link.evaluate("el => el.style.setProperty('min-height', '43px', 'important')")
    wait_for_transition_end(details)
    link_box = link.bounding_box()
    assert link_box and link_box["height"] == 43, link_box
    with pytest.raises(AssertionError):
        assert link_box["width"] >= 44 and link_box["height"] >= 44, link_box


def test_english_day_heading_uses_locale_date_order(browser, base_url, _login_storage_state, e2e_env, grouped_contacts):
    """Use English month-day order and preserve the full filtered day count."""
    previous = _set_user_field(e2e_env, "admin", "preferred_language", "en")
    context = browser.new_context(
        storage_state=_login_storage_state,
        locale="en-US",
        extra_http_headers={"Accept-Language": "en"},
        viewport={"width": 1280, "height": 900},
    )
    try:
        page = context.new_page()
        page.goto(f"{base_url}/events/?{RANGE}", wait_until="domcontentloaded")
        expect(page.locator("html")).to_have_attribute("lang", "en")
        heading = page.get_by_role("heading", level=2).nth(1)
        expect(heading).to_be_visible()
        expect(heading).to_have_text("Fri, Oct 23 — 28 contacts")
        directory = os.environ.get("E2E_EVIDENCE_DIR")
        if directory:
            target = Path(directory)
            target.mkdir(parents=True, exist_ok=True)
            (target / "english-day-heading.txt").write_text(heading.inner_text() + "\n")
            page.screenshot(path=str(target / "english-day-heading.png"))
    finally:
        context.close()
        _set_user_field(e2e_env, "admin", "preferred_language", previous)
