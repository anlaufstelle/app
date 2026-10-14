"""Public rendering contracts for contacts navigation and metadata filters."""

from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlsplit

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Event, TimeFilter
from tests.test_event_list import list_response

pytestmark = pytest.mark.django_db


class Elements(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.tags = []
        self.filter_tags = []
        self.in_filter_form = False
        self.feed(content.decode())

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form" and attributes.get("id") == "event-filters":
            self.in_filter_form = True
        self.tags.append((tag, attributes))
        if self.in_filter_form:
            self.filter_tags.append((tag, attributes))

    def handle_endtag(self, tag):
        if tag == "form":
            self.in_filter_form = False

    def find(self, tag, **attrs):
        return [item for name, item in self.tags if name == tag and all(item.get(k) == v for k, v in attrs.items())]


def test_get_form_has_labels_and_all_filters(client, staff_user, sample_event):
    html = Elements(list_response(client, staff_user).content)
    form = html.find("form", id="event-filters")
    assert len(form) == 1
    assert form[0]["method"].lower() == "get"
    assert form[0]["action"] == reverse("core:event_list")
    assert form[0]["hx-push-url"] == "true"
    assert form[0]["hx-target"] == "#event-table"
    expected = {
        "date_from",
        "date_to",
        "range",
        "doc_type",
        "client",
        "case",
        "created_by",
        "mine",
        "time_filter",
        "anonymous",
        "q",
    }
    controls = [a for t, a in html.filter_tags if t in {"input", "select"} and a.get("name") in expected]
    assert {a["name"] for a in controls} == expected
    labels = {a.get("for") for t, a in html.filter_tags if t == "label"}
    assert all(a.get("id") in labels for a in controls)
    assert html.find("button", type="submit")


@pytest.mark.parametrize("role,expected", [("assistant_user", False), ("staff_user", True)])
def test_case_filter_and_column_follow_role(client, request, role, expected, sample_event):
    html = Elements(list_response(client, request.getfixturevalue(role)).content)
    assert bool(html.find("select", name="case")) is expected
    assert len(html.find("th", scope="col")) == (5 if expected else 4)
    assert html.find("table")[0].get("aria-label") == "Kontakte"


def test_mobile_cards_show_metadata_and_detail_link(client, staff_user, sample_event):
    response = list_response(client, staff_user)
    html = Elements(response.content)
    cards = html.find("div", **{"data-testid": "event-mobile-cards"})
    assert cards and "md:hidden" in cards[0]["class"]
    assert html.find("a", href=reverse("core:event_detail", args=[sample_event.pk]))
    assert len(html.find("dl")) == 1
    assert sample_event.client.pseudonym.encode() in response.content
    assert sample_event.document_type.name.encode() in response.content


def test_htmx_contains_results_without_page_or_filters(client, staff_user, sample_event):
    html = Elements(list_response(client, staff_user, HTTP_HX_REQUEST="true").content)
    assert html.find("table")
    assert not html.find("html")
    assert not html.find("h1")
    # The only form in the partial is the filter form, sent out of band.
    assert [form.get("hx-swap-oob") for form in html.find("form")] == ["outerHTML"]
    assert html.find("div", **{"data-testid": "event-mobile-cards"})


def test_filters_are_selected_and_pagination_retains_normalized_state(client, staff_user, sample_event, facility):
    for _ in range(25):
        Event.objects.create(
            facility=facility,
            document_type=sample_event.document_type,
            created_by=staff_user,
            occurred_at=sample_event.occurred_at,
        )
    day = timezone.localdate(sample_event.occurred_at).isoformat()
    params = {
        "date_from": day,
        "date_to": day,
        "doc_type": str(sample_event.document_type_id),
        "created_by": str(staff_user.pk),
        "mine": "1",
        "anonymous": "no",
        "q": "",
        "junk": "ignored",
    }
    response = list_response(client, staff_user, params)
    html = Elements(response.content)
    assert html.find("input", name="date_from", value=day)
    assert "checked" in html.find("input", name="mine")[0]
    assert "selected" in html.find("option", value=str(sample_event.document_type_id))[0]
    links = html.find("a", **{"aria-label": "Nächste Seite"})
    assert links
    query = parse_qs(urlsplit(links[0]["href"]).query)
    assert query == {**{k: [v] for k, v in params.items() if v and k != "junk"}, "page": ["2"]}
    page_two = client.get(urljoin(reverse("core:event_list"), links[0]["href"]))
    assert page_two.context["events"].number == 2
    assert page_two.context["filters"] == response.context["filters"]


@pytest.mark.parametrize("role", ["assistant_user", "staff_user", "lead_user", "admin_user"])
def test_zeitstrom_contacts_navigation_preserves_day_type_and_shift(client, request, role, facility, doc_type_contact):
    from datetime import time

    shift = TimeFilter.objects.create(facility=facility, label="Day", start_time=time(8), end_time=time(16))
    client.force_login(request.getfixturevalue(role))
    response = client.get(
        reverse("core:zeitstrom"),
        {"date": "2026-09-10", "time_filter": str(shift.pk), "doc_type": str(doc_type_contact.pk), "type": "events"},
    )
    html = Elements(response.content)
    links = html.find("a", **{"data-testid": "zeitstrom-contacts-list"})
    assert len(links) == 1
    assert urlsplit(links[0]["href"]).path == reverse("core:event_list")
    assert parse_qs(urlsplit(links[0]["href"]).query) == {
        "date_from": ["2026-09-10"],
        "date_to": ["2026-09-10"],
        "doc_type": [str(doc_type_contact.pk)],
        "time_filter": [str(shift.pk)],
    }
    assert "Kontakte" in response.content.decode()


@pytest.mark.parametrize("role", ["assistant_user", "staff_user"])
def test_search_links_all_contacts_with_encoded_query(client, request, role, sample_event):
    Event.objects.filter(pk=sample_event.pk).update(search_text="needle & thread")
    client.force_login(request.getfixturevalue(role))
    response = client.get(reverse("core:search"), {"q": "needle & thread", "type": "event"})
    links = Elements(response.content).find("a", **{"data-testid": "search-contacts-list"})
    assert len(links) == 1
    assert urlsplit(links[0]["href"]).path == reverse("core:event_list")
    assert parse_qs(urlsplit(links[0]["href"]).query) == {"q": ["needle & thread"]}
    assert "Alle Treffer als Liste" in response.content.decode()


@pytest.mark.parametrize("route", ["core:zeitstrom", "core:search"])
def test_contacts_entry_is_unavailable_to_system_role(client, super_admin_user, route):
    client.force_login(super_admin_user)
    response = client.get(reverse(route), {"q": "needle"})
    assert response.status_code == 403
    assert b"zeitstrom-contacts-list" not in response.content
    assert b"search-contacts-list" not in response.content


def test_full_page_query_count_does_not_grow_with_rows(client, staff_user, sample_event, monkeypatch):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from core.views.event_list import EventListView

    for _ in range(24):
        Event.objects.create(
            facility=sample_event.facility,
            document_type=sample_event.document_type,
            client=sample_event.client,
            created_by=staff_user,
            occurred_at=sample_event.occurred_at,
        )
    client.force_login(staff_user)
    # Initialize the facility/session state before comparing page sizes.
    assert client.get(reverse("core:event_list")).status_code == 200
    counts = []
    for size in [1, 25]:
        monkeypatch.setattr(EventListView, "page_size", size)
        with CaptureQueriesContext(connection) as captured:
            response = client.get(reverse("core:event_list"))
            assert response.status_code == 200
            assert len(response.context["events"]) == size
        counts.append(len(captured))
    assert counts[0] == counts[1]


@pytest.mark.parametrize("feed_only", ["", "1"])
def test_feed_filter_refresh_updates_contacts_link(client, assistant_user, doc_type_contact, feed_only):
    client.force_login(assistant_user)
    response = client.get(
        reverse("core:zeitstrom_feed_partial"),
        {"date": "2026-09-11", "time_filter": "all", "doc_type": str(doc_type_contact.pk), "feed_only": feed_only},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    html = Elements(response.content)
    assert html.find("nav", id="contacts-navigation", **{"hx-swap-oob": "outerHTML"})
    links = html.find("a", **{"data-testid": "zeitstrom-contacts-list"})
    assert len(links) == 1
    assert parse_qs(urlsplit(links[0]["href"]).query) == {
        "date_from": ["2026-09-11"],
        "date_to": ["2026-09-11"],
        "doc_type": [str(doc_type_contact.pk)],
    }


def selected_values(html):
    """Return the selected option value per select name inside the filter form."""
    values, current = {}, None
    for tag, attrs in html.filter_tags:
        if tag == "select":
            current = attrs["name"]
        elif tag == "option" and "selected" in attrs and current:
            values[current] = attrs.get("value", "")
    return values


def test_htmx_response_carries_the_filter_form_out_of_band(client, staff_user, sample_event):
    """Browser history snapshots must show the controls of the state they hold."""
    params = {"doc_type": str(sample_event.document_type_id), "mine": "1"}
    response = list_response(client, staff_user, params, HTTP_HX_REQUEST="true")
    html = Elements(response.content)
    form = html.find("form", id="event-filters")
    assert len(form) == 1
    assert form[0]["hx-swap-oob"] == "outerHTML"
    assert selected_values(html)["doc_type"] == str(sample_event.document_type_id)
    mine = [a for t, a in html.filter_tags if t == "input" and a.get("name") == "mine"]
    assert "checked" in mine[0]


def test_full_page_renders_the_filter_form_once(client, staff_user, sample_event):
    html = Elements(list_response(client, staff_user, {"doc_type": str(sample_event.document_type_id)}).content)
    form = html.find("form", id="event-filters")
    assert len(form) == 1
    assert "hx-swap-oob" not in form[0]
    assert selected_values(html)["doc_type"] == str(sample_event.document_type_id)


def test_history_restore_request_gets_the_full_page(client, staff_user, sample_event):
    """A history cache miss swaps the response into the body: it must be a full page."""
    response = list_response(
        client,
        staff_user,
        {"doc_type": str(sample_event.document_type_id)},
        HTTP_HX_REQUEST="true",
        HTTP_HX_HISTORY_RESTORE_REQUEST="true",
    )
    assert response.templates[0].name == "core/events/list.html"
    assert len(Elements(response.content).find("form", id="event-filters")) == 1


def test_compact_filter_bar_and_safe_url_persistence(client, staff_user, sample_event):
    response = list_response(client, staff_user)
    html = Elements(response.content)
    form = html.find("form", id="event-filters")[0]
    assert {"no-print", "rounded-[10px]", "flex-wrap", "min-w-0"} <= set(form["class"].split())
    assert "data-filter-persist" in form and "data-filter-persist-url" in form
    assert form["hx-indicator"] == "#event-filters-spinner"
    search = html.find("input", name="q")[0]
    assert search["aria-label"] and search["placeholder"]
    assert html.find("svg", **{"data-testid": "filter-search-icon", "aria-hidden": "true"})
    assert html.find("span", id="event-filters-spinner")
    persisted = {"doc_type", "time_filter", "range", "anonymous"}
    controls = [a for t, a in html.filter_tags if t in {"input", "select"}]
    assert {a["name"] for a in controls if "data-filter-persist-exclude" not in a} == persisted
    assert html.find("input", name="mine")[0]["type"] == "checkbox"
    for control in controls:
        if control["name"] != "mine":
            assert "rounded-[10px]" in control["class"]
            assert "text-[13px]" in control["class"]
    for text in [
        "Alle Typen",
        "Alle Personen",
        "Alle Fälle",
        "Alle Erfassenden",
        "Alle Schichten",
        "Alle Zeiträume",
        "Alle Kontakte",
    ]:
        assert text in response.content.decode()
    assert html.find("a", **{"data-filter-reset": None})
    assert "open" not in html.find("details")[0]


@pytest.mark.parametrize("key", ["case", "created_by", "time_filter", "anonymous"])
def test_active_secondary_filter_is_open_and_named(client, staff_user, sample_event, facility, key):
    from datetime import time

    from core.models import Case

    case = Case.objects.create(facility=facility, client=sample_event.client, title="Named case", created_by=staff_user)
    shift = TimeFilter.objects.create(facility=facility, label="Named shift", start_time=time(8), end_time=time(16))
    value = {"case": str(case.pk), "created_by": str(staff_user.pk), "time_filter": str(shift.pk), "anonymous": "yes"}[
        key
    ]
    response = list_response(client, staff_user, {key: value}, HTTP_HX_REQUEST="true")
    html = Elements(response.content)
    assert "open" in html.find("details")[0]
    assert html.find("span", **{"data-active-filter": key})
    assert "Weitere Filter" in response.content.decode()
    assert html.find("form", id="event-filters")[0]["hx-swap-oob"] == "outerHTML"
