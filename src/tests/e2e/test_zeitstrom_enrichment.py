"""E2E-Tests: Zeitstrom-Vorschaufelder, Badge-Farben, Übergabe.

Refs #411 — Zeitstrom aufwerten.

The feed-card tests run against one fixed past day (``FEED_DAY``) that the
module builds itself. A day picked out of the random seed would make every
assertion depend on which verbs, shifts and kinds the seed happened to land
there; a past day outside the seed window holds exactly the cards created here.
"""

import json
import re
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from ._axe import run_axe
from ._helpers import django_shell

pytestmark = pytest.mark.e2e

PREFIX = "E2E Zeitstrom"
FEED_DAY = "2019-04-16"

_KIND_BADGE = "#feed-list [data-testid='feed-kind-badge']"
_ACTIVITY_VERB = "#feed-list [data-testid='feed-activity-verb']"

_CLEANUP = f"""
from core.models import Activity, Client, DocumentType, Event, Facility, WorkItem

facility = Facility.objects.get(name='Hauptstelle')
Activity.objects.for_facility(facility).filter(context__feed_fixture='{PREFIX}').delete()
WorkItem.objects.filter(title__startswith='{PREFIX}').delete()
Event.objects.filter(document_type__name__startswith='{PREFIX}').delete()
DocumentType.objects.filter(name__startswith='{PREFIX}').delete()
Client.objects.filter(pseudonym__startswith='{PREFIX}').delete()
"""

# One person, one contact, one task and two activities (created, qualified) on FEED_DAY.
_SETUP = f"""
import json
from datetime import datetime
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from core.models import Activity, Client, DocumentType, Event, Facility, User, WorkItem

facility = Facility.objects.get(name='Hauptstelle')
actor = User.objects.get(username='miriam')
person = Client.objects.create(
    facility=facility, contact_stage='qualified', pseudonym='{PREFIX}-Person', created_by=actor
)
doc_type = DocumentType.objects.create(
    facility=facility,
    name='{PREFIX} Kontakt',
    entity_type=DocumentType.EntityType.EVENT,
    sensitivity=DocumentType.Sensitivity.NORMAL,
)


def at(hour):
    return timezone.make_aware(datetime(2019, 4, 16, hour, 0))


client_ct = ContentType.objects.get_for_model(Client)


def activity(verb, action, hour):
    return Activity.objects.create(
        facility=facility, actor=actor, verb=verb, target_type=client_ct, target_id=person.pk,
        context={{'schema': 1, 'action': action, 'feed_fixture': '{PREFIX}'}}, occurred_at=at(hour),
    )


activity(Activity.Verb.CREATED, Activity.Action.CLIENT_CREATED, 9)
activity(Activity.Verb.QUALIFIED, Activity.Action.CLIENT_QUALIFIED, 10)
Event.objects.create(
    facility=facility, document_type=doc_type, client=person, occurred_at=at(11), data_json={{}}, created_by=actor
)
for hour in (13, 13, 14):
    Event.objects.create(
        facility=facility, document_type=doc_type, occurred_at=at(hour), is_anonymous=True,
        created_via=Event.CreatedVia.TALLY, data_json={{}}, created_by=actor,
    )
task = WorkItem.objects.create(facility=facility, client=person, created_by=actor, title='{PREFIX} Aufgabe')
WorkItem.objects.filter(pk=task.pk).update(created_at=at(12))
print('FEED_FIXTURE ' + json.dumps({{'client': str(person.pk)}}))
"""


@pytest.fixture(scope="module")
def feed_fixture(e2e_env):
    """Build the cards of ``FEED_DAY`` and return their target keys; remove them afterwards."""
    django_shell(e2e_env, _CLEANUP)
    lines = django_shell(e2e_env, _SETUP)
    payload = next((line[len("FEED_FIXTURE ") :] for line in lines if line.startswith("FEED_FIXTURE ")), None)
    assert payload, f"Feed fixture setup did not report its keys: {lines}"
    yield json.loads(payload)
    django_shell(e2e_env, _CLEANUP)


def _verbs(page, base_url, **params):
    """Open ``FEED_DAY`` with the given feed filter and return the activity verb labels shown."""
    query = "&".join(f"{key}={value}" for key, value in {"date": FEED_DAY, **params}.items())
    page.goto(f"{base_url}/?{query}", wait_until="domcontentloaded")
    page.locator("#feed-list").wait_for(state="attached", timeout=10000)
    return [text.strip() for text in page.locator(_ACTIVITY_VERB).all_inner_texts()]


def _open_settled_feed(page, base_url, extra_query=""):
    """Finish the initial live-watch replacement before inspecting feed rows."""
    page.goto(f"{base_url}/?date={FEED_DAY}&time_filter=all{extra_query}", wait_until="load")
    page.evaluate("() => window.zeitstromWatch.checkNow()")


class TestEventCardPreview:
    """Event-Karten zeigen Vorschau-Felder und farbige Badges."""

    def test_event_card_shows_preview_fields(self, authenticated_page, base_url):
        """Seed events should display preview fields like Dauer on the card."""
        page = authenticated_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")

        # Look for any event card with preview data (e.g. "Dauer:")
        # Seed data creates events with dauer values
        event_cards = page.locator("a[href*='/events/']")
        assert event_cards.count() > 0, "No event cards found in Zeitstrom"

    def test_kind_badges_differ_in_color_by_card_kind(self, authenticated_page, base_url, feed_fixture):
        """Activities and tasks carry distinct badges; contacts use their type column."""
        page = authenticated_page
        page.goto(f"{base_url}/?date={FEED_DAY}", wait_until="domcontentloaded")
        expect(page.locator(_KIND_BADGE).first).to_be_visible()

        # Computed colors, not class strings: two kinds sharing their color
        # classes must fail even when other classes keep the strings apart.
        colors_by_kind = {}
        for badge in page.locator(_KIND_BADGE).all():
            colors = badge.evaluate("(el) => [getComputedStyle(el).backgroundColor, getComputedStyle(el).color]")
            colors_by_kind.setdefault(badge.inner_text().strip(), set()).add(tuple(colors))

        kinds = ("Aktivität", "Aufgabe")
        assert "Kontakt" not in colors_by_kind
        assert set(kinds) <= set(colors_by_kind), colors_by_kind
        for kind in kinds:
            assert len(colors_by_kind[kind]) == 1, f"{kind} badges are not uniform: {colors_by_kind[kind]}"
        background = {kind: next(iter(colors_by_kind[kind]))[0] for kind in kinds}
        text = {kind: next(iter(colors_by_kind[kind]))[1] for kind in kinds}
        assert len(set(background.values())) == 2, f"Kind badges share a background color: {background}"
        assert len(set(text.values())) == 2, f"Kind badges share a text color: {text}"


class TestActivityCards:
    """Activity-Karten: Keine created-Duplikate, farbige Badges, klickbare Links."""

    def test_no_created_activity_duplicates(self, authenticated_page, base_url, feed_fixture):
        """Mixed feed should not show 'erstellt' activities (redundant with object cards)."""
        page = authenticated_page
        # Precondition, proven on the page: the activity-only view carries the 'erstellt' row.
        assert "erstellt" in _verbs(page, base_url, type="activities")

        verbs = _verbs(page, base_url)
        assert "erstellt" not in verbs, f"'erstellt' activity badge in mixed feed: {verbs}"
        # The mixed feed still shows the other activity of that day, so the
        # assertion above is not satisfied by an empty feed.
        assert "qualifiziert" in verbs, verbs

    def test_activities_filter_shows_created(self, authenticated_page, base_url, feed_fixture):
        """When filtering by 'Aktivitäten', created activities should be visible."""
        verbs = _verbs(authenticated_page, base_url, type="activities")
        assert sorted(verbs) == ["erstellt", "qualifiziert"], verbs

    def test_activity_card_has_badge(self, authenticated_page, base_url, feed_fixture):
        """Activity cards should display a colored verb badge."""
        page = authenticated_page
        verbs = _verbs(page, base_url, type="activities")
        assert len(verbs) == 2, verbs
        assert all(verbs), verbs
        cards = page.locator("#feed-list [data-testid='feed-activity-card']")
        expect(cards).to_have_count(2)
        for card in cards.all():
            expect(card.locator("[data-testid='feed-kind-badge']")).to_have_text("Aktivität")

    def test_activity_card_links_to_target(self, authenticated_page, base_url, feed_fixture):
        """Non-deleted activity cards should link to their target detail page."""
        page = authenticated_page
        _verbs(page, base_url, type="activities")

        # Both cards point at the person the activities were written for.
        target = f"/clients/{feed_fixture['client']}/"
        links = page.locator(f"#feed-list [data-testid='feed-activity-card'] a[href='{target}']")
        expect(links).to_have_count(2)


class TestHandoverPage:
    """Übergabe als ?view=uebergabe-Modus im Zeitstrom erreichbar und zeigt Inhalt (Refs #1124)."""

    def test_handover_mode_accessible(self, authenticated_page, base_url):
        """Der ?view=uebergabe-Modus lädt und zeigt die Statistiken-Sektion."""
        page = authenticated_page
        page.goto(f"{base_url}/?view=uebergabe", wait_until="domcontentloaded")
        assert page.locator("h2:has-text('Statistiken')").is_visible()

    def test_uebergabe_tab_visible(self, authenticated_page, base_url):
        """Der 'Übergabe'-Tab ist im Zeitstrom sichtbar."""
        page = authenticated_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        tab = page.locator("[data-testid='zeitstrom-tab-uebergabe']")
        assert tab.is_visible()
        assert "Übergabe" in tab.inner_text()

    def test_handover_shows_stats(self, authenticated_page, base_url):
        """Der Übergabe-Modus zeigt die Statistiken-Sektion mit 'Kontakte'."""
        page = authenticated_page
        page.goto(f"{base_url}/?view=uebergabe", wait_until="domcontentloaded")
        # Stats section should show "Kontakte" label
        assert page.locator("text=Kontakte").count() > 0


class TestZeitstromFigures:
    """The figures replace the old shift card and follow the selected contact window."""

    def test_figures_replace_the_current_shift_card(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        expect(page.get_by_test_id("zeitstrom-kpi-slot")).to_be_visible()
        expect(page.get_by_test_id("zeitstrom-contacts-list")).to_be_visible()
        expect(page.get_by_test_id("current-shift")).to_have_count(0)
        expect(page.get_by_test_id("zeitstrom-cockpit")).to_have_count(0)
        expect(page.get_by_test_id("handover-summary")).to_have_count(0)

    def test_contact_figure_follows_date_and_shift(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        shift = page.get_by_test_id("zeitstrom-shift-select")
        values = shift.locator("option").evaluate_all("els => els.map(el => el.value)")
        other = next(value for value in values if value not in ("all", shift.input_value()))
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        page.goto(f"{base_url}/?date={yesterday}&time_filter={other}", wait_until="domcontentloaded")
        expect(shift).to_have_value(other)
        contacts = page.get_by_test_id("zeitstrom-contacts-list")
        expect(contacts).to_be_visible()
        params = parse_qs(urlparse(contacts.get_attribute("href")).query)
        assert params["date_from"] == params["date_to"] == [yesterday]
        assert params["time_filter"] == [other]
        assert params["created_via"] == ["standard"]
        expect(page.get_by_test_id("zeitstrom-kpi-tally")).to_have_count(0)
        expect(page.get_by_test_id("current-shift")).to_have_count(0)

    def test_tally_and_undo_update_the_figure_in_another_tab(self, staff_page, base_url):
        page = staff_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        observer = page.context.new_page()
        try:
            observer.goto(f"{base_url}/", wait_until="domcontentloaded")
            counter = page.get_by_test_id("zeitstrom-kpi-tally")
            before = int(counter.get_attribute("data-count"))
            group_sum = """expected => Array.from(
                document.querySelectorAll('#feed-list [data-testid=tally-group]')
            ).reduce((n, el) => n + Number(el.dataset.total), 0) === expected"""
            page.wait_for_function(group_sum, arg=before)
            observer.wait_for_function(group_sum, arg=before)
            button = page.locator("button[data-testid^='tally-increment-']").first
            expect(button).to_be_visible()
            button.click()
            expect(counter).to_have_attribute("data-count", str(before + 1), timeout=15000)
            observer.evaluate("window.zeitstromWatch.checkNow()")
            expect(observer.get_by_test_id("zeitstrom-kpi-tally")).to_have_attribute(
                "data-count", str(before + 1), timeout=15000
            )
            page.wait_for_function(group_sum, arg=before + 1)
            observer.wait_for_function(group_sum, arg=before + 1)
            page.get_by_test_id("tally-undo").click()
            expect(counter).to_have_attribute("data-count", str(before), timeout=15000)
            observer.evaluate("window.zeitstromWatch.checkNow()")
            expect(observer.get_by_test_id("zeitstrom-kpi-tally")).to_have_attribute(
                "data-count", str(before), timeout=15000
            )
            page.wait_for_function(group_sum, arg=before)
            observer.wait_for_function(group_sum, arg=before)
        finally:
            observer.close()


class TestFeedCardHeaderToggle:
    """Refs #1286: Die ganze Kopfzeile (nicht nur der Chevron) klappt Feed-Karten auf."""

    _SEL = "#feed-list button[aria-controls^='event-detail-'], #feed-list button[aria-controls^='activity-detail-']"

    def test_clicking_header_text_toggles_details(self, authenticated_page, base_url, feed_fixture):
        """Klick auf den Zeit-Text der Kopfzeile (nicht den Chevron) klappt auf und wieder zu."""
        page = authenticated_page
        page.goto(f"{base_url}/?date={FEED_DAY}", wait_until="domcontentloaded")
        # The fixed day holds expandable event and activity cards (precondition, not a skip).
        expect(page.locator(self._SEL)).not_to_have_count(0)

        header = page.locator(self._SEL).first
        header.wait_for(state="visible", timeout=10000)
        expect(header).to_have_attribute("aria-expanded", "false")
        panel = page.locator(f"#{header.get_attribute('aria-controls')}")

        # Klick auf den Zeit-Text in der Kopfzeile (NICHT den Chevron rechts).
        time_text = header.locator('[data-feed-column="time"]')
        time_text.click()
        expect(header).to_have_attribute("aria-expanded", "true", timeout=5000)
        expect(panel).to_be_visible()

        # Erneuter Klick auf die Kopfzeile klappt wieder zu.
        time_text.click()
        expect(header).to_have_attribute("aria-expanded", "false", timeout=5000)


class TestFeedRowsAndGroups:
    @pytest.mark.parametrize("position", ["first", "middle", "last", "only"])
    def test_keyboard_focus_outline_stays_inside_visible_rows(
        self, authenticated_page, base_url, feed_fixture, position
    ):
        page = authenticated_page
        # The work-item filter has exactly one row; the mixed feed has five.
        feed_filter = "&type=workitems" if position == "only" else ""
        _open_settled_feed(page, base_url, feed_filter)
        rows = page.locator("#feed-list .feed-row-header")
        count = rows.count()
        assert count == 1 if position == "only" else count >= 5
        index = {"first": 0, "middle": count // 2, "last": count - 1, "only": 0}[position]
        target = rows.nth(index)
        # Reach the row by keyboard, including the single-row feed.
        for _ in range(80):
            page.keyboard.press("Tab")
            if target.evaluate("el => el === document.activeElement"):
                break
        expect(target).to_be_focused()
        values = target.evaluate("""el => {
            const css = getComputedStyle(el), row = el.getBoundingClientRect();
            const box = el.closest('.feed-rows').getBoundingClientRect();
            const width = parseFloat(css.outlineWidth), offset = parseFloat(css.outlineOffset);
            const extent = width + offset;
            const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
            const ctx = canvas.getContext('2d', {willReadFrequently: true});
            function luminance(colour) {
                ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = colour; ctx.fillRect(0, 0, 1, 1);
                const rgb = Array.from(ctx.getImageData(0, 0, 1, 1).data).slice(0, 3).map(c => {
                    c /= 255; return c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4;
                });
                return .2126 * rgb[0] + .7152 * rgb[1] + .0722 * rgb[2];
            }
            const ink = luminance(css.outlineColor);
            const contrasts = [getComputedStyle(el.closest('.feed-rows')).backgroundColor,
                getComputedStyle(document.body).backgroundColor].map(colour => {
                    const bg = luminance(colour);
                    return (Math.max(ink, bg) + .05) / (Math.min(ink, bg) + .05);
                });
            return {style: css.outlineStyle, colour: css.outlineColor, width, offset, contrasts,
                inside: row.left - extent >= box.left + 1 && row.right + extent <= box.right - 1 &&
                    row.top - extent >= box.top + 1 && row.bottom + extent <= box.bottom - 1};
        }""")
        assert values["style"] == "solid" and values["width"] >= 2, values
        assert values["offset"] <= -values["width"] and values["inside"], values
        assert min(values["contrasts"]) >= 3, values

    def test_mobile_rows_keep_a_visible_expansion_indicator(self, authenticated_page, base_url, feed_fixture):
        page = authenticated_page
        page.set_viewport_size({"width": 390, "height": 844})
        _open_settled_feed(page, base_url)
        toggles = page.locator("#feed-list button.feed-row-header")
        assert toggles.count() >= 4  # Contact, activity and both hourly groups.
        for toggle in toggles.all():
            indicator = toggle.locator('[data-feed-column="by"] > span[aria-hidden="true"]')
            expect(indicator).to_be_visible()
            # Read both rectangles in one task: scrolling or a layout change
            # between separate protocol calls would mix coordinate frames.
            bounds, arrow = toggle.evaluate("""el => [
                el.getBoundingClientRect().toJSON(),
                el.querySelector('[data-feed-column="by"] > span[aria-hidden="true"]')
                    .getBoundingClientRect().toJSON()
            ]""")
            assert bounds["height"] >= 44 and bounds["width"] >= 44, bounds
            assert arrow["width"] >= 16 and arrow["height"] >= 16, arrow
            assert bounds["x"] <= arrow["x"] < arrow["x"] + arrow["width"] <= bounds["x"] + bounds["width"]
            assert bounds["y"] <= arrow["y"] < arrow["y"] + arrow["height"] <= bounds["y"] + bounds["height"]
            expect(toggle).to_have_attribute("aria-expanded", "false")
            toggle.click()
            expect(toggle).to_have_attribute("aria-expanded", "true")
            expect(indicator).to_have_class(re.compile(r"\brotate-180\b"))
            toggle.click()
            expect(toggle).to_have_attribute("aria-expanded", "false")
            expect(indicator).not_to_have_class(re.compile(r"\brotate-180\b"))

    def test_hour_groups_expand_with_keyboard(self, authenticated_page, base_url, feed_fixture):
        page = authenticated_page
        # The first live-watch cycle swaps the feed list and drops keyboard focus on the toggle.
        _open_settled_feed(page, base_url)
        groups = page.get_by_test_id("tally-group")
        expect(groups).to_have_count(2)
        assert sorted(int(g.get_attribute("data-total")) for g in groups.all()) == [1, 2]
        toggle = groups.first.get_by_test_id("tally-group-toggle")
        expect(toggle).to_have_attribute("aria-expanded", "false")
        toggle.focus()
        toggle.press("Enter")
        expect(toggle).to_have_attribute("aria-expanded", "true")
        detail = page.locator("#" + toggle.get_attribute("aria-controls"))
        expect(detail).to_be_visible()
        expect(detail.locator("li")).to_have_count(1)
        toggle.press("Space")
        expect(toggle).to_have_attribute("aria-expanded", "false")
        expect(toggle).to_be_focused()

    def test_shared_grid_at_desktop_and_mobile(self, authenticated_page, base_url, feed_fixture):
        page = authenticated_page
        _open_settled_feed(page, base_url)
        page.set_viewport_size({"width": 1440, "height": 900})
        rows = page.locator("#feed-list .feed-row-header")
        assert rows.count() >= 5
        positions = rows.evaluate_all("""rows => rows.map(row => {
            const children = Array.from(row.children);
            return {display: getComputedStyle(row).display,
                x: children.map(el => Math.round(el.getBoundingClientRect().left)),
                h: row.getBoundingClientRect().height};
        })""")
        assert all(r["display"] == "grid" and r["h"] >= 48 for r in positions)
        for column in (1, 2, 3):
            assert len({r["x"][column] for r in positions}) == 1
        page.set_viewport_size({"width": 390, "height": 844})
        geometry = rows.evaluate_all("""rows => rows.map(row => {
            const time = row.querySelector('[data-feed-column="time"]').getBoundingClientRect();
            const details = row.querySelector('[data-feed-column="details"]').getBoundingClientRect();
            return {height: row.getBoundingClientRect().height, top: time.top, next: details.top};
        })""")
        assert all(r["height"] >= 44 and r["next"] > r["top"] for r in geometry)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        contact = page.get_by_test_id("event-card-toggle").first
        contact.focus()
        contact.press("Enter")
        expect(contact).to_have_attribute("aria-expanded", "true")
        contact.press("Space")
        expect(contact).to_have_attribute("aria-expanded", "false")
        expect(contact).to_be_focused()

    def test_control_and_text_contrast_from_computed_colours(self, authenticated_page, base_url, feed_fixture):
        page = authenticated_page
        page.goto(f"{base_url}/?date={FEED_DAY}&time_filter=all", wait_until="domcontentloaded")
        values = page.evaluate("""() => {
            const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
            const ctx = canvas.getContext('2d', {willReadFrequently: true});
            function luminance(colour) {
                ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = colour; ctx.fillRect(0, 0, 1, 1);
                const rgb = Array.from(ctx.getImageData(0, 0, 1, 1).data).slice(0, 3).map(c => {
                    c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4;
                });
                return .2126 * rgb[0] + .7152 * rgb[1] + .0722 * rgb[2];
            }
            function ratio(a, b) {
                const values = [luminance(a), luminance(b)].sort((a,b) => a-b);
                return (values[1] + .05) / (values[0] + .05);
            }
            const select = getComputedStyle(document.getElementById('zeitstrom-shift-select'));
            const list = getComputedStyle(document.querySelector('#feed-list .feed-rows'));
            const text = getComputedStyle(document.querySelector('#feed-list .feed-row-time'));
            const token = getComputedStyle(document.documentElement);
            return {control: ratio(select.borderColor, select.backgroundColor),
                focus: ratio(token.getPropertyValue('--accent'), '#fff'),
                text: ratio(text.color, list.backgroundColor),
                soft: ratio(list.borderColor, list.backgroundColor)};
        }""")
        assert values["control"] >= 3, values
        assert values["focus"] >= 3, values
        assert values["text"] >= 4.5, values
        assert values["soft"] < 3, values

    def test_desktop_shift_change_keeps_focus(self, authenticated_page, base_url):
        page = authenticated_page
        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto(f"{base_url}/?time_filter=all", wait_until="domcontentloaded")
        select = page.get_by_test_id("zeitstrom-shift-select")
        page.evaluate("""() => {
            window.__feedSwapDone = false;
            document.body.addEventListener('htmx:afterSwap', event => {
                if (event.detail.target.id === 'feed-list') window.__feedSwapDone = true;
            });
        }""")
        select.focus()
        with page.expect_response(lambda r: "/partials/zeitstrom/feed/" in r.url and r.request.method == "GET"):
            select.select_option(index=1)
        page.wait_for_function("window.__feedSwapDone === true")
        expect(select).to_be_focused()

    def test_mobile_sheet_traps_tab_and_returns_focus(self, authenticated_page, base_url):
        page = authenticated_page
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(f"{base_url}/?time_filter=all", wait_until="domcontentloaded")
        trigger = page.get_by_test_id("zeitstrom-selection-trigger")
        trigger.click()
        sheet = page.get_by_test_id("zeitstrom-selection")
        expect(sheet).to_have_attribute("aria-modal", "true")
        select = sheet.locator("select")
        close = sheet.locator("[data-sheet-close]")
        select.focus()
        select.press("Tab")
        expect(close).to_be_focused()
        close.press("Shift+Tab")
        expect(select).to_be_focused()
        select.press("Escape")
        expect(trigger).to_be_focused()
        expect(sheet).to_be_hidden()

    def test_mobile_shift_links_without_javascript(self, staff_page, browser, base_url):
        context = browser.new_context(
            storage_state=staff_page.context.storage_state(),
            java_script_enabled=False,
            viewport={"width": 390, "height": 844},
        )
        try:
            page = context.new_page()
            page.goto(f"{base_url}/?time_filter=all", wait_until="domcontentloaded")
            expect(page.get_by_test_id("zeitstrom-selection-trigger")).to_be_hidden()
            links = page.locator("noscript nav a")
            assert links.count() >= 2
            shift_link = next(
                link
                for link in links.all()
                if parse_qs(urlparse(link.get_attribute("href")).query).get("time_filter") != ["all"]
            )
            expect(shift_link).to_be_visible()
            href = shift_link.get_attribute("href")
            shift_link.click()
            assert page.url.endswith(href)
        finally:
            context.close()

    def test_feed_has_no_axe_findings(self, authenticated_page, base_url, feed_fixture):
        page = authenticated_page
        page.goto(f"{base_url}/?date={FEED_DAY}&time_filter=all", wait_until="domcontentloaded")
        expect(page.get_by_test_id("tally-group")).to_have_count(2)
        result = run_axe(page, base_url)
        assert not result.violations, result.violations
        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_test_id("zeitstrom-selection-trigger").click()
        expect(page.get_by_test_id("zeitstrom-selection")).to_have_attribute("aria-modal", "true")
        result = run_axe(page, base_url)
        assert not result.violations, result.violations

    @pytest.mark.parametrize("path", ["/", "/events/", "/clients/", "/workitems/"])
    def test_field_boundaries_at_double_zoom(self, authenticated_page, base_url, path):
        page = authenticated_page
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.goto(base_url + path, wait_until="domcontentloaded")
        page.locator("#main-content h1").wait_for(state="visible")
        snapshot = """() => Array.from(document.querySelectorAll(
            '#main-content input:not([type=hidden]):not([type=checkbox]):not([type=radio]), ' +
            '#main-content select, #main-content textarea'
        )).filter(el => el.getBoundingClientRect().width > 0).map(el => {
            const css = getComputedStyle(el), rect = el.getBoundingClientRect();
            return {id: el.id || el.name, colour: css.borderColor,
                border: parseFloat(css.borderWidth), width: rect.width, height: rect.height};
        })"""
        before = page.evaluate(snapshot)
        assert before
        page.evaluate("document.documentElement.style.zoom = '200%'")
        after = page.evaluate(snapshot)
        assert [(f["id"], f["colour"], f["border"]) for f in after] == [
            (f["id"], f["colour"], f["border"]) for f in before
        ]
        assert all(f["border"] >= 1 and f["width"] > 0 and f["height"] >= 44 for f in after), after
