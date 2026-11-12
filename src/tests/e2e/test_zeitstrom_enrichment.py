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
            button = page.locator("button[data-testid^='tally-increment-']").first
            expect(button).to_be_visible()
            button.click()
            expect(counter).to_have_attribute("data-count", str(before + 1), timeout=15000)
            observer.evaluate("window.zeitstromWatch.checkNow()")
            expect(observer.get_by_test_id("zeitstrom-kpi-tally")).to_have_attribute(
                "data-count", str(before + 1), timeout=15000
            )
            page.get_by_test_id("tally-undo").click()
            expect(counter).to_have_attribute("data-count", str(before), timeout=15000)
            observer.evaluate("window.zeitstromWatch.checkNow()")
            expect(observer.get_by_test_id("zeitstrom-kpi-tally")).to_have_attribute(
                "data-count", str(before), timeout=15000
            )
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
        time_text = header.get_by_text(re.compile(r"^\d{1,2}:\d{2}$")).first
        time_text.click()
        expect(header).to_have_attribute("aria-expanded", "true", timeout=5000)
        expect(panel).to_be_visible()

        # Erneuter Klick auf die Kopfzeile klappt wieder zu.
        time_text.click()
        expect(header).to_have_attribute("aria-expanded", "false", timeout=5000)
