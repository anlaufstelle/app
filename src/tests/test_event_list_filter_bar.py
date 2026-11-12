"""One-row filter bar of the contacts list: period rule, chips, panel, compatibility."""

from datetime import date, time, timedelta
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest
from django.utils import timezone

from core.models import Case, Event, TimeFilter
from core.services.events.list_filters import resolve_event_list_filters
from tests.test_event_list import event_ids, list_response

pytestmark = pytest.mark.django_db

TODAY = date(2026, 10, 9)  # a Friday


def resolve(params, facility, user, today=TODAY):
    return resolve_event_list_filters(params, facility, user, today=today)


class Page(HTMLParser):
    """Collects attributes and nesting of the filter form's elements."""

    def __init__(self, content):
        super().__init__()
        self.stack = []
        self.items = []  # (tag, attrs, ancestors)
        self.text = {}
        self._open_text = []
        self.feed(content.decode())

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.items.append((tag, attributes, [(t, a) for t, a in self.stack]))
        if tag not in {"input", "br", "img", "meta", "link", "hr", "path", "circle"}:
            self.stack.append((tag, attributes))
            self._open_text.append([])

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                texts = self._open_text[index:]
                del self._open_text[index:]
                self.text.setdefault(tag, []).append(" ".join("".join(texts[0]).split()) if texts else "")
                return

    def handle_data(self, data):
        for bucket in self._open_text:
            bucket.append(data)

    def find(self, tag, **attrs):
        return [(a, anc) for t, a, anc in self.items if t == tag and all(a.get(k) == v for k, v in attrs.items())]


def form_items(page):
    return [(t, a, anc) for t, a, anc in page.items if any(x[1].get("id") == "event-filters" for x in anc)]


def chip_links(page):
    return {a["data-filter-key"]: a for a, _ in page.find("a", **{"data-chip-remove": ""})}


def query_of(href):
    return {k: v[0] for k, v in parse_qs(urlsplit(href).query, keep_blank_values=True).items()}


# --- (a) period rule -----------------------------------------------------------------


@pytest.mark.parametrize(
    "preset, start, end",
    [
        ("today", "2026-10-09", "2026-10-09"),
        ("this_week", "2026-10-05", "2026-10-11"),
        ("this_month", "2026-10-01", "2026-10-31"),
        ("last_month", "2026-09-01", "2026-09-30"),
    ],
)
def test_each_preset_resolves_to_its_dates(facility, staff_user, preset, start, end):
    state = resolve({"range": preset}, facility, staff_user)
    assert state == {"range": preset, "date_from": start, "date_to": end}


def test_this_month_handles_leap_february_and_december(facility, staff_user):
    feb = resolve({"range": "this_month"}, facility, staff_user, today=date(2028, 2, 10))
    assert (feb["date_from"], feb["date_to"]) == ("2028-02-01", "2028-02-29")
    dec = resolve({"range": "this_month"}, facility, staff_user, today=date(2026, 12, 31))
    assert (dec["date_from"], dec["date_to"]) == ("2026-12-01", "2026-12-31")


def test_explicit_dates_win_over_a_different_preset_and_drop_it(facility, staff_user):
    """A preset must not silently replace dates the user typed: the dates stay, the preset goes."""
    state = resolve({"range": "this_week", "date_from": "2026-09-01", "date_to": "2026-09-15"}, facility, staff_user)
    assert state == {"date_from": "2026-09-01", "date_to": "2026-09-15"}


def test_one_sided_explicit_date_also_wins(facility, staff_user):
    state = resolve({"range": "last_month", "date_from": "2026-10-02"}, facility, staff_user)
    assert state == {"date_from": "2026-10-02"}


def test_old_form_echo_pair_keeps_following_the_preset(facility, staff_user):
    """The previous form re-sent the preset's own dates; such a link keeps meaning 'this week'."""
    state = resolve({"range": "this_week", "date_from": "2026-10-05", "date_to": "2026-10-11"}, facility, staff_user)
    assert state == {"range": "this_week", "date_from": "2026-10-05", "date_to": "2026-10-11"}


def test_invalid_explicit_dates_leave_the_preset_in_charge(facility, staff_user):
    state = resolve({"range": "today", "date_from": "garbage", "date_to": ""}, facility, staff_user)
    assert state["range"] == "today"


def test_unknown_preset_is_ignored(facility, staff_user):
    assert resolve({"range": "next_year"}, facility, staff_user) == {}


@pytest.mark.parametrize("value, expected", [("standard", "standard"), ("tally", "tally"), ("other", None), ("", None)])
def test_created_via_accepts_only_known_values(facility, staff_user, value, expected):
    state = resolve({"created_via": value}, facility, staff_user)
    assert state.get("created_via") == expected


def test_created_via_filters_the_list(client, staff_user, sample_event):
    tally = Event.objects.create(
        facility=sample_event.facility,
        document_type=sample_event.document_type,
        occurred_at=timezone.now(),
        created_by=staff_user,
        created_via=Event.CreatedVia.TALLY,
    )
    assert event_ids(list_response(client, staff_user, {"created_via": "tally"})) == {tally.pk}
    assert event_ids(list_response(client, staff_user, {"created_via": "standard"})) == {sample_event.pk}
    assert event_ids(list_response(client, staff_user)) == {tally.pk, sample_event.pk}


# --- (b) chips ---------------------------------------------------------------------------


@pytest.fixture
def filter_values(facility, staff_user, sample_event):
    case = Case.objects.create(facility=facility, client=sample_event.client, title="Named case", created_by=staff_user)
    shift = TimeFilter.objects.create(facility=facility, label="Named shift", start_time=time(8), end_time=time(16))
    return {
        "doc_type": str(sample_event.document_type_id),
        "client": str(sample_event.client_id),
        "case": str(case.pk),
        "created_by": str(staff_user.pk),
        "time_filter": str(shift.pk),
        "anonymous": "yes",
        "mine": "1",
        "created_via": "tally",
    }


def test_default_view_has_no_chips_and_no_reset(client, staff_user, sample_event):
    page = Page(list_response(client, staff_user).content)
    assert page.find("a", **{"data-chip-remove": ""}) == []
    assert page.find("a", **{"data-filter-clear-all": ""}) == []


@pytest.mark.parametrize(
    "key", ["doc_type", "client", "case", "created_by", "time_filter", "anonymous", "mine", "created_via"]
)
def test_every_active_filter_is_a_chip_that_removes_exactly_that_parameter(client, staff_user, filter_values, key):
    params = {key: filter_values[key], "q": "Test"}
    page = Page(list_response(client, staff_user, params).content)
    links = chip_links(page)
    assert set(links) == {key}
    assert query_of(links[key]["href"]) == {"q": "Test"}
    assert links[key]["hx-get"] == links[key]["href"]
    assert links[key]["hx-push-url"] == "true"
    assert links[key]["aria-label"]


def test_chip_removal_leaves_the_other_filters_untouched(client, staff_user, filter_values):
    params = {k: filter_values[k] for k in ("doc_type", "anonymous", "mine")}
    links = chip_links(Page(list_response(client, staff_user, params).content))
    assert set(links) == set(params)
    for key, link in links.items():
        assert query_of(link["href"]) == {k: v for k, v in params.items() if k != key}


def test_chip_labels_name_filter_and_value(client, staff_user, filter_values):
    params = {k: filter_values[k] for k in ("doc_type", "case", "time_filter", "anonymous", "mine", "created_via")}
    labels = {
        k: a["aria-label"] for k, a in chip_links(Page(list_response(client, staff_user, params).content)).items()
    }
    assert "Named case" in labels["case"]
    assert "Named shift" in labels["time_filter"]
    assert "Beratung" in labels["doc_type"] or labels["doc_type"].count(":") >= 1
    assert "Nur meine" in labels["mine"]
    assert "Nur Strichliste" in labels["created_via"]
    assert "Anonym" in labels["anonymous"]


def test_standard_created_via_chip_reads_without_tally(client, staff_user, sample_event):
    links = chip_links(Page(list_response(client, staff_user, {"created_via": "standard"}).content))
    assert "Ohne Strichliste" in links["created_via"]["aria-label"]


def test_search_text_is_not_a_chip(client, staff_user, sample_event):
    page = Page(list_response(client, staff_user, {"q": "Test"}).content)
    assert chip_links(page) == {}


def test_period_chip_removes_preset_and_dates_together(client, staff_user, sample_event):
    preset = chip_links(Page(list_response(client, staff_user, {"range": "this_week", "q": "ab"}).content))
    assert set(preset) == {"period"}
    assert query_of(preset["period"]["href"]) == {"q": "ab"}
    custom = chip_links(
        Page(list_response(client, staff_user, {"date_from": "2026-10-01", "date_to": "2026-10-23"}).content)
    )
    assert set(custom) == {"period"}
    assert query_of(custom["period"]["href"]) == {}
    assert "01.10." in custom["period"]["aria-label"] and "23.10." in custom["period"]["aria-label"]


def test_clear_all_returns_to_the_unfiltered_list_and_counter_matches(client, staff_user, filter_values):
    params = {k: filter_values[k] for k in ("doc_type", "mine", "anonymous")}
    params["range"] = "today"
    response = list_response(client, staff_user, params)
    page = Page(response.content)
    clear = page.find("a", **{"data-filter-clear-all": ""})
    assert len(clear) == 1
    assert urlsplit(clear[0][0]["href"]).query == ""
    assert response.context["active_filter_count"] == len(chip_links(page)) == 4
    summary = [a for a, _ in page.find("summary", **{"data-filter-panel-toggle": ""})]
    assert summary
    assert "(4)" in " ".join(page.text["summary"])


# --- (c) compatibility of old parameters ---------------------------------------------


def make_events(facility, doc_type, user, days):
    return {
        d: Event.objects.create(
            facility=facility,
            document_type=doc_type,
            occurred_at=timezone.make_aware(timezone.datetime.combine(d, time(12))),
            created_by=user,
        )
        for d in days
    }


def test_old_links_return_the_same_events(client, staff_user, facility, doc_type_contact):
    today = timezone.localdate()
    monday = today - timedelta(days=today.weekday())
    events = make_events(facility, doc_type_contact, staff_user, [monday, monday - timedelta(days=20)])
    in_week = {events[monday].pk}
    old_pair = {
        "range": "this_week",
        "date_from": monday.isoformat(),
        "date_to": (monday + timedelta(days=6)).isoformat(),
    }
    assert event_ids(list_response(client, staff_user, {"range": "this_week"})) == in_week
    assert event_ids(list_response(client, staff_user, old_pair)) == in_week
    explicit = {
        "date_from": (monday - timedelta(days=21)).isoformat(),
        "date_to": (monday - timedelta(days=19)).isoformat(),
    }
    assert event_ids(list_response(client, staff_user, explicit)) == {events[monday - timedelta(days=20)].pk}
    assert event_ids(
        list_response(
            client,
            staff_user,
            {"range": "last_month", "date_from": explicit["date_from"], "date_to": explicit["date_to"]},
        )
    ) == event_ids(list_response(client, staff_user, explicit))


def test_conflicting_period_is_shown_as_custom_range(client, staff_user, sample_event):
    params = {"range": "this_week", "date_from": "2026-09-01", "date_to": "2026-09-15"}
    page = Page(list_response(client, staff_user, params).content)
    segments = {a["data-period"]: a for a, _ in page.find("a", **{"data-period-segment": ""})}
    assert segments["custom"].get("aria-current") == "true"
    assert all(a.get("aria-current") != "true" for k, a in segments.items() if k != "custom")
    assert query_of(chip_links(page)["period"]["href"]) == {}


@pytest.mark.parametrize(
    "key, value", [("range", "this_week"), ("range", "last_month"), ("doc_type", None), ("anonymous", "no")]
)
def test_old_urls_still_render(client, staff_user, sample_event, key, value):
    value = value or str(sample_event.document_type_id)
    assert list_response(client, staff_user, {key: value}).status_code == 200


# --- (d) layout: one row, panel, labels ----------------------------------------------------


def test_default_view_shows_search_segment_and_panel_toggle_only(client, staff_user, sample_event):
    page = Page(list_response(client, staff_user).content)
    visible = [
        a["name"]
        for t, a, anc in form_items(page)
        if t in {"input", "select"}
        and a.get("type") != "hidden"
        and a.get("name")
        and not any(x[1].get("data-filter-panel") is not None for x in anc)
    ]
    assert visible == ["q"]
    segments = [a["data-period"] for a, _ in page.find("a", **{"data-period-segment": ""})]
    assert segments == ["today", "this_week", "this_month", "custom"]
    assert all(a.get("aria-current") != "true" for a, _ in page.find("a", **{"data-period-segment": ""}))
    panel = page.find("details", **{"data-filter-panel": ""})
    assert len(panel) == 1 and "open" not in panel[0][0]


def test_panel_holds_the_selects_and_labels_them(client, staff_user, sample_event):
    page = Page(list_response(client, staff_user).content)
    in_panel = [
        a
        for t, a, anc in form_items(page)
        if t in {"input", "select"} and any(x[1].get("data-filter-panel") is not None for x in anc)
    ]
    names = {a["name"] for a in in_panel if a.get("type") != "hidden"}
    assert names == {
        "doc_type",
        "client",
        "case",
        "created_by",
        "time_filter",
        "anonymous",
        "created_via",
        "mine",
        "date_from",
        "date_to",
    }
    labels = {a.get("for") for t, a, anc in form_items(page) if t == "label"}
    assert all(a["id"] in labels for a in in_panel if a.get("type") != "hidden")


def test_preset_replaces_the_date_fields_by_a_hidden_range(client, staff_user, sample_event):
    page = Page(list_response(client, staff_user, {"range": "this_week"}).content)
    controls = {a["name"]: a for t, a, _ in form_items(page) if t == "input" and a.get("name")}
    assert "date_from" not in controls and "date_to" not in controls
    assert controls["range"]["type"] == "hidden" and controls["range"]["value"] == "this_week"
    segments = {a["data-period"]: a for a, _ in page.find("a", **{"data-period-segment": ""})}
    assert segments["this_week"]["aria-current"] == "true"
    # Switching to a range starts from the preset's own days, so nothing is lost.
    today = timezone.localdate()
    monday = today - timedelta(days=today.weekday())
    assert query_of(segments["custom"]["href"]) == {
        "date_from": monday.isoformat(),
        "date_to": (monday + timedelta(days=6)).isoformat(),
    }
    assert segments["custom"]["hx-get"] == segments["custom"]["href"]


def test_custom_period_keeps_date_fields_with_date_attributes(client, staff_user, sample_event):
    page = Page(list_response(client, staff_user, {"date_from": "2026-10-01", "date_to": "2026-10-23"}).content)
    controls = {a["name"]: a for t, a, _ in form_items(page) if t == "input" and a.get("name")}
    assert controls["date_from"]["value"] == "2026-10-01" and controls["date_to"]["value"] == "2026-10-23"
    from core.utils.date_input import date_input_attributes

    expected = set(date_input_attributes("date"))
    assert expected and all(expected <= set(controls[k]) for k in ("date_from", "date_to"))
    assert controls["range"]["value"] == ""


def test_last_month_is_offered_in_the_panel(client, staff_user, sample_event):
    page = Page(list_response(client, staff_user).content)
    extra = page.find("a", **{"data-period-extra": "last_month"})
    assert len(extra) == 1
    # Link URLs are complete; hx-include on the link or an enclosing element would append the form values.
    links = [*extra, *page.find("a", **{"data-period-segment": ""})]
    links += page.find("a", **{"data-chip-remove": ""}) + page.find("a", **{"data-filter-clear-all": ""})
    for link, ancestors in links:
        assert "hx-include" not in link
        assert not any("hx-include" in attrs for _, attrs in ancestors)
    assert query_of(extra[0][0]["href"]) == {"range": "last_month"}


# --- immediate filtering, no-JS fallback ------------------------------------------------------


def test_search_and_panel_filter_immediately_and_submit_stays_as_fallback(client, staff_user, sample_event):
    page = Page(list_response(client, staff_user).content)
    form = page.find("form", id="event-filters")[0][0]
    assert form["hx-push-url"] == "true" and form["hx-get"] == form["action"]
    search = next(a for t, a, _ in form_items(page) if t == "input" and a.get("name") == "q")
    assert "keyup changed delay:300ms" in search["hx-trigger"]
    assert search["hx-include"] == "#event-filters"
    panel = page.find("div", **{"data-filter-fields": ""})[0][0]
    assert panel["hx-trigger"] == "change" and panel["hx-include"] == "#event-filters"
    submit = page.find("button", id="event-submit")
    assert len(submit) == 1 and submit[0][0]["type"] == "submit"
    assert "data-filter-submit-fallback" in submit[0][0] and "hidden" not in submit[0][0]


def test_labels_are_not_larger_than_their_controls(client, staff_user, sample_event):
    sizes = {"text-xs": 12, "text-[13px]": 13, "text-sm": 14, "text-base": 16, "text-lg": 18}

    def size(attrs):
        found = [sizes[c] for c in attrs.get("class", "").split() if c in sizes]
        return max(found) if found else None

    page = Page(list_response(client, staff_user).content)
    controls = {a["id"]: a for t, a, _ in form_items(page) if t in {"input", "select"} and a.get("id")}
    pairs = [(a, controls[a["for"]]) for t, a, _ in form_items(page) if t == "label" and a.get("for") in controls]
    assert pairs
    for label, control in pairs:
        if "sr-only" in label.get("class", "") or control.get("type") in {"checkbox", "search"}:
            continue
        assert size(label) is not None and size(control) is not None
        assert size(label) <= size(control), (label, control)
