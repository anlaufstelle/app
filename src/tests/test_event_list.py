"""Visibility and filter contracts for the contacts list."""

from datetime import date, datetime, time
from uuid import uuid4

import pytest
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, Case, DocumentType, Event, Facility, TimeFilter
from core.services.dashboard.search import event_search_queryset
from core.services.events.feed import build_feed_items

pytestmark = pytest.mark.django_db


def list_response(client, user, params=None, **headers):
    if client.session.get("_auth_user_id") != str(user.pk):
        client.force_login(user)
    return client.get(reverse("core:event_list"), params or {}, **headers)


def event_ids(response):
    assert response.status_code == 200
    return {event.pk for event in response.context["events"]}


@pytest.mark.parametrize("role", ["assistant_user", "staff_user", "lead_user", "admin_user"])
def test_visibility_matches_event_feed(client, request, role, facility, staff_user, doc_type_contact, foreign_event):
    user = request.getfixturevalue(role)
    events = []
    for sensitivity in DocumentType.Sensitivity.values:
        doc_type = DocumentType.objects.create(facility=facility, name=sensitivity, sensitivity=sensitivity)
        events.append(
            Event.objects.create(
                facility=facility, document_type=doc_type, occurred_at=timezone.now(), created_by=staff_user
            )
        )
    Event.objects.create(facility=facility, document_type=doc_type_contact, occurred_at=timezone.now(), is_deleted=True)
    response = list_response(client, user)
    feed = build_feed_items(facility, timezone.localdate(), "events", user=user)
    expected = {item["object"].pk for item in feed if item["type"] == "event"}
    assert event_ids(response) == expected
    assert foreign_event.pk not in event_ids(response)
    assert all("data_json" in event.get_deferred_fields() for event in response.context["events"])


def test_combined_filters_and_no_audit(client, staff_user, sample_event):
    event = sample_event
    params = {
        "date_from": timezone.localdate(event.occurred_at).isoformat(),
        "date_to": timezone.localdate(event.occurred_at).isoformat(),
        "doc_type": str(event.document_type_id),
        "client": str(event.client_id),
        "created_by": str(staff_user.pk),
        "mine": "1",
        "anonymous": "no",
        "q": "Test-ID",
    }
    client.force_login(staff_user)
    before = AuditLog.objects.count()
    response = list_response(client, staff_user, params, HTTP_HX_REQUEST="true")
    assert event_ids(response) == {event.pk}
    assert AuditLog.objects.count() == before
    assert "q=Test-ID" in response.context["pagination_params"]
    assert response.templates[0].name == "core/events/partials/table.html"
    assert event_ids(list_response(client, staff_user, {**params, "anonymous": "yes"})) == set()


@pytest.mark.parametrize("key", ["client", "case", "doc_type", "created_by", "time_filter"])
def test_invalid_and_foreign_identifiers_are_indistinguishable(
    client,
    staff_user,
    sample_event,
    foreign_client,
    foreign_case,
    foreign_doc_type,
    foreign_staff,
    second_facility,
    key,
):
    foreign_shift = TimeFilter.objects.create(
        facility=second_facility, label="Foreign", start_time=time(8), end_time=time(16)
    )
    foreign = {
        "client": foreign_client,
        "case": foreign_case,
        "doc_type": foreign_doc_type,
        "created_by": foreign_staff,
        "time_filter": foreign_shift,
    }[key]
    responses = [list_response(client, staff_user, {key: value}) for value in ["broken", str(uuid4()), str(foreign.pk)]]
    assert all(event_ids(response) == {sample_event.pk} for response in responses)
    assert all(not response.context["filters"].get(key) for response in responses)


def test_hidden_type_is_ignored_and_not_offered(client, assistant_user, sample_event, doc_type_crisis):
    response = list_response(client, assistant_user, {"doc_type": str(doc_type_crisis.pk)})
    assert event_ids(response) == {sample_event.pk}
    assert doc_type_crisis.pk not in {item.pk for item in response.context["document_types"]}
    assert not response.context["filters"].get("doc_type")


def test_case_metadata_and_filter_require_staff(client, assistant_user, staff_user, sample_event, facility):
    case = Case.objects.create(
        facility=facility, client=sample_event.client, title="Private case", created_by=staff_user
    )
    sample_event.case = case
    sample_event.save(update_fields=["case"])
    response = list_response(client, assistant_user, {"case": str(uuid4())})
    assert event_ids(response) == {sample_event.pk}
    assert not response.context["show_cases"]
    assert not response.context["cases"]
    assert b"Private case" not in response.content
    assert "case" not in response.context["filters"]
    response = list_response(client, staff_user, {"case": str(case.pk)})
    assert event_ids(response) == {sample_event.pk}
    assert b"Private case" in response.content
    case.is_deleted = True
    case.save(update_fields=["is_deleted"])
    assert b"Private case" not in list_response(client, staff_user).content


def test_free_text_uses_global_search_and_excludes_trashed_person(client, staff_user, sample_event, facility):
    Event.objects.filter(pk=sample_event.pk).update(search_text="needle")
    response = list_response(client, staff_user, {"q": "needle"})
    assert event_ids(response) == set(
        event_search_queryset(facility, staff_user, "needle").values_list("pk", flat=True)
    )
    assert event_ids(response) == {sample_event.pk}
    sample_event.client.is_deleted = True
    sample_event.client.save(update_fields=["is_deleted"])
    assert not event_ids(list_response(client, staff_user, {"q": "needle"}))
    assert event_ids(list_response(client, staff_user)) == {sample_event.pk}
    assert not event_ids(list_response(client, staff_user, {"q": "n"}))


@pytest.mark.parametrize("day", [date(2026, 3, 28), date(2026, 10, 24)])
def test_shift_range_reuses_feed_dst_window(client, staff_user, facility, doc_type_contact, day):
    from core.services.events.feed import get_time_range

    shift = TimeFilter.objects.create(facility=facility, label="Night", start_time=time(20), end_time=time(2, 45))
    start, end = get_time_range(day, shift)
    expected = set()
    for moment in [start, end, timezone.make_aware(datetime.combine(day, time(12)))]:
        event = Event.objects.create(facility=facility, document_type=doc_type_contact, occurred_at=moment)
        if moment in [start, end]:
            expected.add(event.pk)
    assert (
        event_ids(
            list_response(
                client,
                staff_user,
                {"date_from": day.isoformat(), "date_to": day.isoformat(), "time_filter": str(shift.pk)},
            )
        )
        == expected
    )


def test_shift_filter_statements_carry_plain_range_bounds(client, staff_user, facility, doc_type_contact):
    """A btree index cannot serve the multirange operator; plain bounds keep the scan on the index.

    Deliberately a query-shape guard, not a runtime benchmark: it pins the SQL form that
    made the 50k-contact EXPLAIN use the index. Runtime itself is not asserted here.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    shift = TimeFilter.objects.create(facility=facility, label="Day", start_time=time(8), end_time=time(16))
    day = date(2026, 6, 10)
    morning = timezone.make_aware(datetime.combine(day, time(9)))
    Event.objects.create(facility=facility, document_type=doc_type_contact, occurred_at=morning)
    params = {"date_from": day.isoformat(), "date_to": day.isoformat(), "time_filter": str(shift.pk)}
    client.force_login(staff_user)
    with CaptureQueriesContext(connection) as captured:
        assert len(event_ids(list_response(client, staff_user, params))) == 1
    statements = [
        query["sql"]
        for query in captured.captured_queries
        if 'FROM "core_event"' in query["sql"] and ("MIN(" in query["sql"] or "tstzmultirange" in query["sql"])
    ]
    assert len(statements) == 3  # bounds aggregate, count, page
    for sql in statements:
        assert '"core_event"."occurred_at" >=' in sql
        assert '"core_event"."occurred_at" <=' in sql


@pytest.mark.parametrize("preset", ["this_week", "last_month"])
def test_quick_ranges(client, staff_user, facility, doc_type_contact, preset):
    from core.services.events.list_filters import resolve_event_list_filters

    state = resolve_event_list_filters({"range": preset}, facility, staff_user, today=date(2026, 10, 9))
    expected = ("2026-10-05", "2026-10-11") if preset == "this_week" else ("2026-09-01", "2026-09-30")
    assert (state["date_from"], state["date_to"]) == expected


@pytest.mark.parametrize(
    "params", [{"date_from": "bad"}, {"date_to": "9999-12-31"}, {"date_from": "2026-10-10", "date_to": "2026-10-01"}]
)
def test_invalid_dates_do_not_crash(client, staff_user, sample_event, params):
    assert event_ids(list_response(client, staff_user, params)) == {sample_event.pk}


def test_query_count_is_constant_for_page_size(staff_user, sample_event, django_assert_num_queries):
    from core.views.event_list import EventListView

    for _ in range(24):
        Event.objects.create(
            facility=sample_event.facility,
            document_type=sample_event.document_type,
            client=sample_event.client,
            created_by=staff_user,
            occurred_at=timezone.now(),
        )
    # One settings read per render comes from the offline key lifetime in the context processor;
    # a fresh facility instance per request keeps that read from being served by the related-object cache.
    for page_size in [1, 25]:
        request = RequestFactory().get("/events/", HTTP_HX_REQUEST="true")
        request.user = staff_user
        request.current_facility = Facility.objects.get(pk=sample_event.facility_id)
        with django_assert_num_queries(8):
            response = EventListView.as_view(page_size=page_size)(request)
            assert response.status_code == 200


def test_creator_filter_accepts_user_primary_key(client, staff_user, admin_user, sample_event):
    other = Event.objects.create(
        facility=sample_event.facility,
        document_type=sample_event.document_type,
        occurred_at=sample_event.occurred_at,
        created_by=admin_user,
    )
    response = list_response(client, staff_user, {"created_by": str(admin_user.pk)})
    assert event_ids(response) == {other.pk}
    assert response.context["filters"]["created_by"] == str(admin_user.pk)
