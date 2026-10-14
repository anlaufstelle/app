"""E2E tests for the contacts list ``/events/``.

Covers the browser behaviour the unit tests cannot see: HTMX filter runs that
push the URL, shareable URLs, browser history after HTMX filter changes,
pagination that keeps the filter state, mobile cards, the link from the
Zeitstrom, the form without JavaScript, and the role-dependent columns.

All data lives in March 2019 and in ``E2E Liste``-prefixed document types, so
the seed data never leaks into a date-bounded list and cleanup is exact.
"""

import json
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell

pytestmark = pytest.mark.e2e

PREFIX = "E2E Liste"
RANGE = "date_from=2019-03-01&date_to=2019-03-31"

# Staff (miriam) sees everything but the trashed contact; the assistant (lena)
# additionally does not see the elevated document type.
STAFF_TOTAL = 32
ASSISTANT_TOTAL = 31
PAGE_SIZE = 25

# ``EventHistory`` carries an append-only trigger; the contacts are removed with
# the trigger switched off for the duration of the delete (same procedure as
# ``core.seed.flush.flush_seed_data``) and switched back on in all cases.
_CLEANUP = f"""
from django.db import connection
from core.models import Case, Client, DocumentType, Event, EventHistory

cursor = connection.cursor()
cursor.execute('ALTER TABLE core_eventhistory DISABLE TRIGGER eventhistory_no_delete')
try:
    EventHistory.objects.filter(event__document_type__name__startswith='{PREFIX}').delete()
    Event.objects.filter(document_type__name__startswith='{PREFIX}').delete()
finally:
    cursor.execute('ALTER TABLE core_eventhistory ENABLE ALWAYS TRIGGER eventhistory_no_delete')
Case.objects.filter(title__startswith='{PREFIX}').delete()
DocumentType.objects.filter(name__startswith='{PREFIX}').delete()
Client.objects.filter(pseudonym__startswith='{PREFIX}').delete()
"""

_SETUP = f"""
import json
from datetime import datetime
from django.utils import timezone
from core.models import Case, Client, DocumentType, Event, Facility, User

facility = Facility.objects.get(name='Hauptstelle')
users = {{u.username: u for u in User.objects.filter(username__in=['emma', 'miriam', 'lena'])}}
EVENT = DocumentType.EntityType.EVENT


def at(day, hour):
    return timezone.make_aware(datetime(2019, 3, day, hour, 0))


def doc_type(suffix, sensitivity):
    return DocumentType.objects.create(
        facility=facility, name='{PREFIX} ' + suffix, entity_type=EVENT, sensitivity=sensitivity
    )


type_a = doc_type('A', DocumentType.Sensitivity.NORMAL)
type_b = doc_type('B', DocumentType.Sensitivity.NORMAL)
type_elevated = doc_type('Erhöht', DocumentType.Sensitivity.ELEVATED)
person_1 = Client.objects.create(
    facility=facility, contact_stage='identified', pseudonym='{PREFIX}-Person-1', created_by=users['emma']
)
person_2 = Client.objects.create(
    facility=facility, contact_stage='identified', pseudonym='{PREFIX}-Person-2', created_by=users['emma']
)
case = Case.objects.create(facility=facility, client=person_2, title='{PREFIX} Fall', created_by=users['emma'])


def make(doc, who, when, client=None, **extra):
    return Event.objects.create(
        facility=facility, document_type=doc, client=client, occurred_at=when,
        data_json={{}}, created_by=users[who], **extra,
    )


for day in range(1, 28):
    make(type_a, 'miriam', at(day, 10), person_1)
for hour in (9, 12, 15):
    make(type_b, 'emma', at(5, hour), person_2, case=case)
make(type_a, 'lena', at(6, 8), None, is_anonymous=True)
make(type_elevated, 'emma', at(7, 11), person_2)
trashed = make(type_a, 'miriam', at(8, 10), person_1, is_deleted=True, deleted_at=timezone.now())

print('DATA ' + json.dumps({{
    'type_a': str(type_a.pk), 'type_b': str(type_b.pk), 'type_elevated': str(type_elevated.pk),
    'case': str(case.pk), 'trashed': str(trashed.pk),
    'emma': users['emma'].pk, 'miriam': users['miriam'].pk, 'lena': users['lena'].pk,
}}))
"""

_SINGLE_EVENT = f"""
from datetime import datetime
from django.utils import timezone
from core.models import Client, DocumentType, Event, Facility, User

facility = Facility.objects.get(name='Hauptstelle')
event = Event.objects.create(
    facility=facility,
    document_type=DocumentType.objects.get(facility=facility, name='{PREFIX} A'),
    client=Client.objects.get(facility=facility, pseudonym='{PREFIX}-Person-1'),
    occurred_at=timezone.make_aware(datetime(2019, 4, 2, 10, 0)),
    data_json={{}},
    created_by=User.objects.get(username='miriam'),
)
print('EVENT ' + str(event.pk))
"""


@pytest.fixture(scope="module")
def dataset(e2e_env):
    """Create the deterministic contact set once per module and remove it afterwards."""
    django_shell(e2e_env, _CLEANUP)
    lines = django_shell(e2e_env, _SETUP)
    payload = next((line[5:] for line in lines if line.startswith("DATA ")), None)
    assert payload, lines
    yield json.loads(payload)
    django_shell(e2e_env, _CLEANUP)


def rows(page):
    """One header button per contact card (the list renders the shared Zeitstrom card, not a table)."""
    return page.locator("#event-table button[aria-controls^='event-detail-']")


def hrefs(page):
    return sorted(
        page.locator("#event-table a[href^='/events/']").evaluate_all("els => els.map(e => e.getAttribute('href'))")
    )


def query_of(url):
    return parse_qs(urlsplit(url).query)


def open_more_filters(page):
    """Open the collapsed „Weitere Filter“ block (case, created by, shift, anonymous) if it is closed."""
    details = page.locator("#event-filters details")
    if details.get_attribute("open") is None:
        details.locator("summary").click()
    expect(details).to_have_attribute("open", "")


def submit_filters(page):
    """Submit the filter form and wait for the HTMX partial response."""
    with page.expect_response(
        lambda response: (
            urlsplit(response.url).path == "/events/" and response.request.resource_type in ("xhr", "fetch")
        )
    ) as info:
        page.locator("#event-filters button[type='submit']").click()
    assert info.value.status == 200


def open_list(page, base_url, query=""):
    page.goto(f"{base_url}/events/{('?' + query) if query else ''}")
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator("#event-filters")).to_be_visible()


def expect_total(page, shown, total):
    expect(page.locator("#event-table")).to_contain_text(re.compile(rf"1–{shown} von {total} Einträgen"))


class TestFilterRun:
    def test_filter_run_narrows_rows_and_pushes_the_url(self, staff_page, base_url, dataset):
        page = staff_page
        open_list(page, base_url)

        page.fill("#event-date_from", "2019-03-01")
        page.fill("#event-date_to", "2019-03-31")
        submit_filters(page)
        expect(rows(page)).to_have_count(PAGE_SIZE)
        expect_total(page, PAGE_SIZE, STAFF_TOTAL)
        expect(page).to_have_url(re.compile(r"date_from=2019-03-01"))
        assert query_of(page.url)["date_to"] == ["2019-03-31"]
        # The form is re-sent with the results; keyboard focus must stay on the button.
        assert page.evaluate("document.activeElement.id") == "event-submit"

        page.select_option("#event-doc_type", dataset["type_b"])
        submit_filters(page)
        expect(rows(page)).to_have_count(3)
        assert query_of(page.url)["doc_type"] == [dataset["type_b"]]

        # Type "Alle" again and filter by the creator: the elevated contact joins.
        page.select_option("#event-doc_type", "")
        open_more_filters(page)
        page.select_option("#event-created_by", str(dataset["emma"]))
        submit_filters(page)
        expect(rows(page)).to_have_count(4)
        assert query_of(page.url)["created_by"] == [str(dataset["emma"])]

        # "Nur meine" for the signed-in staff user: all 27 own, visible contacts.
        open_more_filters(page)
        page.select_option("#event-created_by", "")
        page.check("#event-mine")
        submit_filters(page)
        expect(rows(page)).to_have_count(PAGE_SIZE)
        expect_total(page, PAGE_SIZE, 27)
        assert query_of(page.url)["mine"] == ["1"]

    def test_trashed_and_deleted_contacts_never_appear(self, staff_page, base_url, dataset, e2e_env):
        page = staff_page
        open_list(page, base_url, RANGE)
        expect_total(page, PAGE_SIZE, STAFF_TOTAL)
        assert not [href for href in hrefs(page) if dataset["trashed"] in href]

        lines = django_shell(e2e_env, _SINGLE_EVENT)
        pk = next(line[6:] for line in lines if line.startswith("EVENT "))
        open_list(page, base_url, "date_from=2019-04-01&date_to=2019-04-30")
        expect(rows(page)).to_have_count(1)

        # Delete through the real UI path; the contact must leave the list.
        page.goto(f"{base_url}/events/{pk}/delete/")
        page.locator("[data-testid='event-delete-confirm']").click()
        page.wait_for_load_state("domcontentloaded")
        open_list(page, base_url, "date_from=2019-04-01&date_to=2019-04-30")
        expect(rows(page)).to_have_count(0)
        expect(page.locator("#event-table")).to_contain_text("Keine Kontakte gefunden.")


class TestSharedUrlAndHistory:
    def test_shared_url_restores_the_same_state_in_a_fresh_context(
        self, staff_page, browser, _staff_storage_state, base_url, dataset
    ):
        page = staff_page
        open_list(page, base_url, RANGE)
        page.select_option("#event-doc_type", dataset["type_b"])
        open_more_filters(page)
        page.select_option("#event-created_by", str(dataset["emma"]))
        submit_filters(page)
        expect(rows(page)).to_have_count(3)
        shared_url = page.url
        expected_rows = hrefs(page)

        context = browser.new_context(storage_state=_staff_storage_state, locale="de-DE")
        try:
            other = context.new_page()
            other.goto(shared_url)
            other.wait_for_load_state("domcontentloaded")
            expect(rows(other)).to_have_count(3)
            assert hrefs(other) == expected_rows
            expect(other.locator("#event-date_from")).to_have_value("2019-03-01")
            expect(other.locator("#event-date_to")).to_have_value("2019-03-31")
            expect(other.locator("#event-doc_type")).to_have_value(dataset["type_b"])
            expect(other.locator("#event-created_by")).to_have_value(str(dataset["emma"]))
        finally:
            context.close()

    def test_back_and_forward_restore_each_filter_state(self, staff_page, base_url, dataset):
        page = staff_page
        open_list(page, base_url, RANGE)
        expect_total(page, PAGE_SIZE, STAFF_TOTAL)

        page.select_option("#event-doc_type", dataset["type_b"])
        submit_filters(page)
        expect(rows(page)).to_have_count(3)

        page.select_option("#event-doc_type", dataset["type_a"])
        submit_filters(page)
        expect_total(page, PAGE_SIZE, 28)
        assert query_of(page.url)["doc_type"] == [dataset["type_a"]]

        page.go_back()
        expect(page).to_have_url(re.compile(rf"doc_type={dataset['type_b']}"))
        expect(rows(page)).to_have_count(3)
        expect(page.locator("#event-doc_type")).to_have_value(dataset["type_b"])

        page.go_back()
        expect(page).not_to_have_url(re.compile(r"doc_type="))
        expect_total(page, PAGE_SIZE, STAFF_TOTAL)
        expect(page.locator("#event-doc_type")).to_have_value("")

        page.go_forward()
        expect(rows(page)).to_have_count(3)
        expect(page.locator("#event-doc_type")).to_have_value(dataset["type_b"])

    def test_back_after_a_history_cache_miss_loads_the_full_page(self, staff_page, base_url, dataset):
        page = staff_page
        open_list(page, base_url, RANGE)
        page.select_option("#event-doc_type", dataset["type_b"])
        submit_filters(page)
        expect(rows(page)).to_have_count(3)

        # Without a snapshot HTMX fetches the URL and swaps the response into the body.
        page.evaluate(
            "sessionStorage.removeItem('htmx-history-cache');"
            " window.__historyMiss = false;"
            " document.body.addEventListener('htmx:historyCacheMiss', () => { window.__historyMiss = true; });"
        )
        page.go_back()
        missed = page.evaluate("window.__historyMiss")
        assert missed is True, "the snapshot cache was not missed"
        expect(page).not_to_have_url(re.compile(r"doc_type="))
        expect(page.locator("#event-filters")).to_have_count(1)
        expect(page.locator("h1")).to_have_text("Kontakte")
        expect_total(page, PAGE_SIZE, STAFF_TOTAL)
        expect(page.locator("#event-doc_type")).to_have_value("")


class TestPagination:
    def test_pagination_keeps_the_filter_state(self, staff_page, base_url, dataset):
        page = staff_page
        open_list(page, base_url, f"{RANGE}&mine=1&doc_type={dataset['type_a']}")
        expect(rows(page)).to_have_count(PAGE_SIZE)
        expect_total(page, PAGE_SIZE, 27)

        page.get_by_role("link", name="Nächste Seite").click()
        page.wait_for_load_state("domcontentloaded")
        query = query_of(page.url)
        assert query["page"] == ["2"]
        assert query["mine"] == ["1"]
        assert query["doc_type"] == [dataset["type_a"]]
        assert query["date_from"] == ["2019-03-01"]
        expect(rows(page)).to_have_count(27 - PAGE_SIZE)
        expect(page.locator("#event-mine")).to_be_checked()
        expect(page.locator("#event-doc_type")).to_have_value(dataset["type_a"])


class TestZeitstromLink:
    def test_contacts_link_follows_the_zeitstrom_filter(self, staff_page, base_url, dataset):
        page = staff_page
        page.goto(f"{base_url}/?date=2019-03-05")
        page.wait_for_load_state("domcontentloaded")
        select = page.locator("#filter-doc-type")
        select.wait_for(state="visible")
        link = page.locator("[data-testid='zeitstrom-contacts-list']")
        expect(link).to_be_visible()

        with page.expect_response(
            lambda response: "/partials/zeitstrom/feed/" in response.url and response.status == 200
        ):
            select.select_option(dataset["type_b"])
        # The partial refreshes the link out of band; wait for the new state.
        expect(link).to_have_attribute("href", re.compile(rf"doc_type={dataset['type_b']}"))
        query = query_of(link.get_attribute("href"))
        assert query["date_from"] == ["2019-03-05"]
        assert query["date_to"] == ["2019-03-05"]

        link.click()
        page.wait_for_load_state("domcontentloaded")
        expect(page).to_have_url(re.compile(r"/events/\?"))
        expect(rows(page)).to_have_count(3)
        expect(page.locator("#event-date_from")).to_have_value("2019-03-05")
        expect(page.locator("#event-doc_type")).to_have_value(dataset["type_b"])


class TestWithoutJavaScript:
    def test_form_submits_as_plain_get(self, browser, _staff_storage_state, base_url, dataset):
        context = browser.new_context(storage_state=_staff_storage_state, locale="de-DE", java_script_enabled=False)
        try:
            page = context.new_page()
            open_list(page, base_url)
            page.fill("#event-date_from", "2019-03-01")
            page.fill("#event-date_to", "2019-03-31")
            page.select_option("#event-doc_type", dataset["type_b"])
            page.locator("#event-filters button[type='submit']").click()
            page.wait_for_load_state("domcontentloaded")

            query = query_of(page.url)
            assert query["doc_type"] == [dataset["type_b"]]
            assert query["date_from"] == ["2019-03-01"]
            expect(rows(page)).to_have_count(3)
            expect(page.locator("#event-doc_type")).to_have_value(dataset["type_b"])
        finally:
            context.close()


class TestMobileCards:
    def test_cards_fit_a_narrow_viewport(self, mobile_authenticated_page, base_url, dataset):
        page = mobile_authenticated_page
        open_list(page, base_url, RANGE)
        expect(rows(page)).to_have_count(PAGE_SIZE)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

        page.select_option("#event-doc_type", dataset["type_b"])
        submit_filters(page)
        expect(rows(page)).to_have_count(3)
        expect(rows(page).first).to_be_visible()


class TestRoleColumns:
    def test_assistant_gets_no_case_column_filter_or_elevated_contacts(self, assistant_page, base_url, dataset):
        page = assistant_page
        open_list(page, base_url, RANGE)
        expect_total(page, PAGE_SIZE, ASSISTANT_TOTAL)
        expect(page.locator("#event-case")).to_have_count(0)
        expect(page.locator("#event-table")).not_to_contain_text("Fall:")
        expect(page.locator("#event-doc_type option", has_text=f"{PREFIX} Erhöht")).to_have_count(0)

        # A forged case filter is ignored, not applied: the total stays unfiltered.
        open_list(page, base_url, f"{RANGE}&case={dataset['case']}")
        expect_total(page, PAGE_SIZE, ASSISTANT_TOTAL)

    def test_staff_gets_the_case_column_and_filter(self, staff_page, base_url, dataset):
        page = staff_page
        open_list(page, base_url, f"{RANGE}&case={dataset['case']}")
        expect(page.locator("#event-case")).to_have_value(dataset["case"])
        expect(rows(page)).to_have_count(3)
        expect(rows(page).first).to_contain_text(f"Fall: {PREFIX} Fall")
