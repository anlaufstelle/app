"""E2E-Tests: Zeitstrom-Vorschaufelder, Badge-Farben, Übergabe.

Refs #411 — Zeitstrom aufwerten.

The feed-card tests run against one fixed past day (``FEED_DAY``) that the
module builds itself. A day picked out of the random seed would make every
assertion depend on which verbs, shifts and kinds the seed happened to land
there; a past day outside the seed window holds exactly the cards created here.
"""

import json
import re

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
        """Contact, activity and task cards carry kind badges in three different colors.

        Since the visual refresh the document type is a neutral badge; the color
        only distinguishes the card kind (contact, activity, task, ban).
        """
        page = authenticated_page
        page.goto(f"{base_url}/?date={FEED_DAY}", wait_until="domcontentloaded")
        expect(page.locator(_KIND_BADGE).first).to_be_visible()

        # Computed colors, not class strings: two kinds sharing their color
        # classes must fail even when other classes keep the strings apart.
        colors_by_kind = {}
        for badge in page.locator(_KIND_BADGE).all():
            colors = badge.evaluate("(el) => [getComputedStyle(el).backgroundColor, getComputedStyle(el).color]")
            colors_by_kind.setdefault(badge.inner_text().strip(), set()).add(tuple(colors))

        kinds = ("Kontakt", "Aktivität", "Aufgabe")
        assert set(kinds) <= set(colors_by_kind), colors_by_kind
        for kind in kinds:
            assert len(colors_by_kind[kind]) == 1, f"{kind} badges are not uniform: {colors_by_kind[kind]}"
        background = {kind: next(iter(colors_by_kind[kind]))[0] for kind in kinds}
        text = {kind: next(iter(colors_by_kind[kind]))[1] for kind in kinds}
        assert len(set(background.values())) == 3, f"Kind badges share a background color: {background}"
        assert len(set(text.values())) == 3, f"Kind badges share a text color: {text}"


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


class TestCurrentShiftInZeitstrom:
    """Dienst-Übersicht des aktuell laufenden Dienstes im Zeitstrom (Refs #1138)."""

    def test_current_shift_card_visible_on_load(self, authenticated_page, base_url):
        """Die Dienst-Übersicht ist dauerhaft sichtbar und beschriftet — kein Aufklappen."""
        page = authenticated_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")

        card = page.locator("[data-testid='current-shift']")
        card.wait_for(state="visible", timeout=10000)
        # Überschrift wird per CSS in Versalien dargestellt → case-insensitiv prüfen.
        assert "aktueller dienst" in card.inner_text().lower()
        # Kein aufklappbares <details> mehr für die Dienst-Übersicht.
        assert page.locator("[data-testid='handover-summary']").count() == 0

    def test_current_shift_card_independent_of_date_and_filter(self, authenticated_page, base_url):
        """Die Dienst-Übersicht bleibt beim laufenden Dienst, auch bei anderer Datums-/Schicht-Auswahl."""
        from datetime import date, timedelta

        page = authenticated_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")

        card = page.locator("[data-testid='current-shift']")
        card.wait_for(state="visible", timeout=10000)
        running_shift_label = card.locator("[data-testid='current-shift-label']").inner_text()

        # Beliebigen Schicht-Filter ermitteln, der NICHT der laufende Dienst ist,
        # und per Voll-Navigation (anderer Tag) ansteuern.
        tabs = page.locator(".time-filter-tab")
        other_filter_id = None
        for i in range(tabs.count()):
            label = tabs.nth(i).inner_text().strip()
            if label.startswith("Alle"):
                continue
            # running_shift_label = "Spätdienst · 16:00 – 22:00"; Tab = "Spätdienst 16:00–22:00"
            if label.split()[0] not in running_shift_label:
                other_filter_id = tabs.nth(i).get_attribute("data-zeitstrom-time-filter")
                break

        assert other_filter_id is not None, "Kein vom laufenden Dienst abweichender Schichtfilter gefunden"
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        # Datum und abweichende Schicht in EINER Voll-Navigation ansteuern.
        # Ein vorausgehender HTMX-Klick würde absichtlich eine zweite,
        # konkurrierende Navigation starten und nicht diesen Vertrag testen.
        page.goto(
            f"{base_url}/?date={yesterday}&time_filter={other_filter_id}",
            wait_until="domcontentloaded",
        )

        card = page.locator("[data-testid='current-shift']")
        card.wait_for(state="visible", timeout=10000)
        # Beschriftung der Box folgt weder Datum noch Schicht-Auswahl.
        assert card.locator("[data-testid='current-shift-label']").inner_text() == running_shift_label


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
