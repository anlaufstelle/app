"""E2E: Contact list ``/events/`` — cards, filters and role view (KL), German and English.

Complements ``test_event_list.py`` (filter run, URL, history, pagination) with what
is missing there: expanding the shared Zeitstrom card by click, Enter and space bar,
the link "Zum Eintrag", open "Weitere Filter" with visible active filters, the value
comparison per role against the Zeitstrom (also in the closed DOM), no horizontal
scrolling at 390 px and the English labels.

All data belongs to a dedicated document type (``KL-<marker>``) in May 2018.
"""

from __future__ import annotations

import json
import re
import subprocess
import uuid

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell
from ._selectors import event_card_toggle

pytestmark = pytest.mark.e2e

RANGE = "date_from=2018-05-01&date_to=2018-05-31"

#: role fixture -> which field markers the role may see (open / elevated / high)
ROLE_VISIBILITY = {
    "assistant_page": {"open": True, "elevated": False, "high": False},
    "staff_page": {"open": True, "elevated": True, "high": False},
    "lead_page": {"open": True, "elevated": True, "high": True},
    "authenticated_page": {"open": True, "elevated": True, "high": True},
}

#: role fixture -> whether the high-sensitivity document type (as a whole) is delivered
HIGH_TYPE_VISIBLE = {
    "assistant_page": False,
    "staff_page": False,
    "lead_page": True,
    "authenticated_page": True,
}


@pytest.fixture(scope="module")
def kl_data(e2e_env):
    marker = uuid.uuid4().hex[:8]
    script = f"""
import json
from datetime import datetime
from django.utils import timezone
from core.models import Case, Client, DocumentType, DocumentTypeField, Event, FieldTemplate, Facility, TimeFilter, User

f = Facility.objects.get(name='Hauptstelle')
users = {{u.username: u for u in User.objects.filter(username__in=['emma', 'miriam', 'lena', 'admin'])}}
dt = DocumentType.objects.create(facility=f, name='KL-{marker}', slug='kl-{marker}', entity_type='event',
                                 sensitivity='normal')
slugs = {{}}
for order, (key, sens) in enumerate((('open', ''), ('elevated', 'elevated'), ('high', 'high'))):
    ft = FieldTemplate.objects.create(facility=f, name='KL-' + key + '-{marker}', slug='kl-' + key + '-{marker}',
                                      field_type='text', sensitivity=sens)
    DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=order)
    slugs[key] = ft.slug
person = Client.objects.create(facility=f, contact_stage='identified', pseudonym='KL-Person-{marker}',
                               created_by=users['emma'])
case = Case.objects.create(facility=f, client=person, title='KL-Fall-{marker}', created_by=users['emma'])
tf = TimeFilter.objects.filter(facility=f).first()


def at(day, hour):
    return timezone.make_aware(datetime(2018, 5, day, hour, 0))


def make(who, when, data, client=None, **extra):
    return Event.objects.create(facility=f, document_type=dt, client=client, occurred_at=when, data_json=data,
                                created_by=users[who], **extra)


values = {{k: k.upper() + '-WERT-{marker}' for k in ('open', 'elevated', 'high')}}
e1 = make('miriam', at(10, 10), {{slugs[k]: values[k] for k in slugs}}, person, case=case)
e2 = make('emma', at(11, 10), {{slugs['open']: 'OFFEN-ZWEI-{marker}'}}, person)
e3 = make('lena', at(12, 9), {{slugs['open']: 'OFFEN-ANONYM-{marker}'}}, None, is_anonymous=True)
# A second, high-sensitivity document type with an ordinary field: the whole entry is withheld by type.
dt_high = DocumentType.objects.create(facility=f, name='KLH-{marker}', slug='klh-{marker}', entity_type='event',
                                      sensitivity='high')
ft_high = FieldTemplate.objects.create(facility=f, name='KLH-feld-{marker}', slug='klh-feld-{marker}',
                                       field_type='text', sensitivity='high')
DocumentTypeField.objects.create(document_type=dt_high, field_template=ft_high, sort_order=0)
e4 = Event.objects.create(facility=f, document_type=dt_high, client=person, occurred_at=at(13, 10),
                          data_json={{ft_high.slug: 'HOCHTYP-WERT-{marker}'}}, created_by=users['miriam'])
print('DATA ' + json.dumps({{'values': values, 'case': str(case.pk), 'e1': str(e1.pk), 'e2': str(e2.pk),
    'e3': str(e3.pk), 'e4': str(e4.pk), 'miriam': users['miriam'].pk, 'emma': users['emma'].pk,
    'time_filter': str(tf.pk) if tf else '', 'type': str(dt.pk)}}))
"""
    cleanup = f"""
from django.db import connection
from core.models import Case, Client, DocumentType, DocumentTypeField, Event, EventHistory, FieldTemplate
cursor = connection.cursor()
cursor.execute('ALTER TABLE core_eventhistory DISABLE TRIGGER eventhistory_no_delete')
try:
    EventHistory.objects.filter(event__document_type__name__in=['KL-{marker}', 'KLH-{marker}']).delete()
    Event.objects.filter(document_type__name__in=['KL-{marker}', 'KLH-{marker}']).delete()
finally:
    cursor.execute('ALTER TABLE core_eventhistory ENABLE ALWAYS TRIGGER eventhistory_no_delete')
Case.objects.filter(title='KL-Fall-{marker}').delete()
DocumentTypeField.objects.filter(document_type__name__in=['KL-{marker}', 'KLH-{marker}']).delete()
DocumentType.objects.filter(name__in=['KL-{marker}', 'KLH-{marker}']).delete()
FieldTemplate.objects.filter(name__endswith='-{marker}', name__startswith='KL-').delete()
FieldTemplate.objects.filter(name='KLH-feld-{marker}').delete()
Client.objects.filter(pseudonym='KL-Person-{marker}').delete()
"""
    try:
        lines = django_shell(e2e_env, script)
    except subprocess.CalledProcessError as exc:
        raise AssertionError(f"seed failed: {exc.stderr[-1500:]}") from exc
    payload = next((ln[5:] for ln in lines if ln.startswith("DATA ")), None)
    assert payload, lines
    data = json.loads(payload)
    data["marker"] = marker
    yield data
    django_shell(e2e_env, cleanup)


def _open_list(page, base_url, query=""):
    page.goto(f"{base_url}/events/?{RANGE}{('&' + query) if query else ''}", wait_until="domcontentloaded")
    expect(page.locator("#event-filters")).to_be_visible()


def _no_horizontal_scroll(page):
    return page.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth")


class TestRoleValueComparison:
    """A value invisible to the role must not appear anywhere in the DOM — list and Zeitstrom agree."""

    @pytest.mark.parametrize("role_fixture", list(ROLE_VISIBILITY))
    def test_list_and_zeitstrom_show_the_same_values(self, request, base_url, kl_data, role_fixture):
        page = request.getfixturevalue(role_fixture)
        visible = ROLE_VISIBILITY[role_fixture]
        _open_list(page, base_url)
        expect(page.locator("#event-table")).to_contain_text(f"KL-{kl_data['marker']}", timeout=10000)
        list_html = page.content()
        page.goto(f"{base_url}/?date=2018-05-10", wait_until="domcontentloaded")
        expect(page.locator("main")).to_contain_text(f"KL-{kl_data['marker']}", timeout=10000)
        feed_html = page.content()

        for key, shown in visible.items():
            value = kl_data["values"][key]
            assert (value in list_html) is shown, f"{role_fixture}: {key} on /events/ (expected visible={shown})"
            assert (value in feed_html) is shown, f"{role_fixture}: {key} on the Zeitstrom (expected visible={shown})"


class TestHighSensitivityDocumentType:
    """A high-sensitivity document type is withheld as a whole: list, Zeitstrom and detail agree per role."""

    @pytest.mark.parametrize("role_fixture", list(HIGH_TYPE_VISIBLE))
    def test_type_value_and_actions_follow_the_role(self, request, base_url, kl_data, role_fixture):
        page = request.getfixturevalue(role_fixture)
        shown = HIGH_TYPE_VISIBLE[role_fixture]
        marker = kl_data["marker"]
        value = f"HOCHTYP-WERT-{marker}"
        type_name = f"KLH-{marker}"
        entry_link = f"/events/{kl_data['e4']}/"

        _open_list(page, base_url)
        expect(page.locator("#event-table")).to_contain_text(f"KL-{marker}", timeout=10000)
        list_html = page.content()
        # The feed is rendered into the page document; wait for that response and a distinct rendered state.
        with page.expect_response(
            lambda r: r.request.resource_type == "document" and "date=2018-05-13" in r.url
        ) as feed_response:
            page.goto(f"{base_url}/?date=2018-05-13", wait_until="domcontentloaded")
        assert feed_response.value.status == 200, f"{role_fixture}: Zeitstrom answered {feed_response.value.status}"
        feed = page.locator("#feed-list")
        if shown:
            expect(feed).to_contain_text(type_name, timeout=10000)
        else:
            expect(feed).to_contain_text("Keine Einträge für diesen Zeitraum", timeout=10000)
        feed_html = page.content()

        for where, html in (("/events/", list_html), ("Zeitstrom", feed_html)):
            assert (value in html) is shown, f"{role_fixture}: value on {where} (expected visible={shown})"
            assert (type_name in html) is shown, f"{role_fixture}: type name on {where} (expected visible={shown})"
            assert (entry_link in html) is shown, f"{role_fixture}: entry link on {where} (expected={shown})"
        # Contract of get_visible_event_or_404: a hidden event answers 404, never 403 and never a masked 200.
        status = page.request.get(f"{base_url}{entry_link}").status
        expected_status = 200 if shown else 404
        assert status == expected_status, f"{role_fixture}: detail answered {status}, expected {expected_status}"


class TestCardExpansion:
    @pytest.mark.parametrize(
        "viewport", [{"width": 1280, "height": 800}, {"width": 390, "height": 844}], ids=["desktop", "390px"]
    )
    def test_click_enter_and_space_toggle_and_entry_link_works(self, staff_page, base_url, kl_data, viewport):
        page = staff_page
        page.set_viewport_size(viewport)
        _open_list(page, base_url, f"case={kl_data['case']}")
        header = page.locator("#event-table button[aria-controls^='event-detail-']").first
        expect(header).to_be_visible(timeout=10000)
        detail = page.locator(f"#{header.get_attribute('aria-controls')}")

        expect(header).to_have_attribute("aria-expanded", "false")
        header.click()
        expect(header).to_have_attribute("aria-expanded", "true")
        expect(detail).to_be_visible()
        assert _no_horizontal_scroll(page)

        header.focus()
        page.keyboard.press("Enter")
        expect(header).to_have_attribute("aria-expanded", "false")
        expect(detail).to_be_hidden()
        page.keyboard.press("Space")
        expect(header).to_have_attribute("aria-expanded", "true")
        expect(detail).to_be_visible()
        expect(detail).to_contain_text(kl_data["values"]["elevated"])

        link = detail.get_by_role("link", name=re.compile("Zum Eintrag"))
        expect(link).to_have_attribute("href", re.compile(rf"/events/{kl_data['e1']}/$"))
        link.click()
        page.wait_for_url(re.compile(rf"/events/{kl_data['e1']}/$"), timeout=10000)


class TestFiltersByKeyboardAndOpenState:
    def test_more_filters_open_with_active_filters_and_survive_history(self, staff_page, base_url, kl_data):
        page = staff_page
        query = f"case={kl_data['case']}&created_by={kl_data['miriam']}&anonymous=no"
        if kl_data["time_filter"]:
            query += f"&time_filter={kl_data['time_filter']}"
        _open_list(page, base_url, query)
        details = page.locator("#event-filters details")
        expect(details).to_have_attribute("open", "")
        for name in ("case", "created_by", "anonymous") + (("time_filter",) if kl_data["time_filter"] else ()):
            expect(page.locator(f"summary [data-active-filter='{name}']")).to_be_visible()
        expect(page.locator("#event-case")).to_have_value(kl_data["case"])
        expect(page.locator("#event-created_by")).to_have_value(str(kl_data["miriam"]))
        expect(page.locator("#event-anonymous")).to_have_value("no")
        assert _no_horizontal_scroll(page)

        # Leave and come back: the same form values are restored.
        page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")
        page.go_back(wait_until="domcontentloaded")
        expect(page.locator("#event-case")).to_have_value(kl_data["case"])
        expect(page.locator("#event-anonymous")).to_have_value("no")

    def test_search_field_and_filters_work_with_keyboard_only(self, staff_page, base_url, kl_data):
        page = staff_page
        _open_list(page, base_url)
        search = page.locator("#event-q")
        search.focus()
        page.keyboard.type(f"KL-Person-{kl_data['marker']}")
        with page.expect_response(
            lambda r: r.url.split("?")[0].endswith("/events/") and r.request.resource_type in ("xhr", "fetch")
        ):
            page.keyboard.press("Enter")
        expect(page.locator("#event-table button[aria-controls^='event-detail-']")).to_have_count(2, timeout=10000)
        page.wait_for_url(re.compile(rf"[?&]q=KL-Person-{kl_data['marker']}"), timeout=5000)
        # The "Nur meine" checkbox toggles with the space bar; the submit button runs the filter with Enter.
        page.locator("#event-mine").focus()
        page.keyboard.press("Space")
        expect(page.locator("#event-mine")).to_be_checked()
        page.locator("#event-submit").focus()
        page.keyboard.press("Enter")
        try:
            page.wait_for_url(re.compile(r"[?&]mine=1"), timeout=10000)
        except Exception as exc:  # noqa: BLE001 - report what the page did instead
            raise AssertionError(
                f"mine=1 not applied; url={page.url}; checked={page.locator('#event-mine').is_checked()}; "
                f"cards={page.locator('#event-table button[aria-controls^=event-detail-]').count()}"
            ) from exc
        # Only the contact created by this person (miriam) remains.
        cards = page.locator("#event-table button[aria-controls^='event-detail-']")
        expect(cards).to_have_count(1, timeout=10000)
        expect(page.locator("#event-mine")).to_be_checked()
        expect(page.locator("#event-q")).to_have_value(f"KL-Person-{kl_data['marker']}")


class TestNarrowViewportAndEnglish:
    @pytest.mark.parametrize("role_fixture", ["assistant_page", "staff_page", "lead_page", "authenticated_page"])
    def test_390px_has_no_horizontal_scroll_for_every_role(self, request, base_url, kl_data, role_fixture):
        page = request.getfixturevalue(role_fixture)
        page.set_viewport_size({"width": 390, "height": 844})
        _open_list(page, base_url, f"case={kl_data['case']}")
        assert _no_horizontal_scroll(page)
        event_card_toggle(page).click()
        assert _no_horizontal_scroll(page)

    def test_english_labels_and_empty_options(self, browser, base_url, kl_data, e2e_env, _staff_storage_state):
        script = (
            "from core.models import User; u = User.objects.get(username='miriam'); "
            "print('prev=' + u.preferred_language); "
            "User.objects.filter(pk=u.pk).update(preferred_language='en')"
        )
        previous = next(ln for ln in django_shell(e2e_env, script) if ln.startswith("prev=")).removeprefix("prev=")
        context = browser.new_context(
            storage_state=_staff_storage_state, locale="en-US", viewport={"width": 390, "height": 844}
        )
        try:
            page = context.new_page()
            page.set_default_timeout(30000)
            _open_list(page, base_url, f"case={kl_data['case']}")
            form = page.locator("#event-filters")
            text = form.inner_text()
            for english in ("More filters", "All types", "Only mine"):
                assert english in text or english in form.inner_html(), (english, text)
            options = page.locator("#event-filters select option[value='']").all_inner_texts()
            assert options and all(o.strip() for o in options)
            for german in ("Weitere Filter", "Alle Typen", "Alle Personen", "Alle Fälle", "Nur meine"):
                assert german not in form.inner_html(), german
            assert page.locator("#event-q").get_attribute("placeholder") == "Search contacts …"
            # Date fields are not part of this check (format belongs to the locale work).
            assert _no_horizontal_scroll(page)
        finally:
            context.close()
            django_shell(
                e2e_env,
                "from core.models import User; "
                f"User.objects.filter(username='miriam').update(preferred_language={previous!r})",
            )
