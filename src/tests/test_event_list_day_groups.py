"""Day headings count the complete role-visible filtered result."""

import re
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags

from core.models import DocumentType, Event, TimeFilter
from tests.test_event_list import list_response
from tests.test_event_list_templates import Elements

pytestmark = pytest.mark.django_db


def headings(response):
    return [
        (day, " ".join(strip_tags(text).split()))
        for day, text in re.findall(
            r'<h2[^>]*data-event-day="([^"]+)"[^>]*>(.*?)</h2>', response.content.decode(), re.S
        )
    ]


def create_contact(facility, doc_type, day, hour=12, minute=0, **kwargs):
    return Event.objects.create(
        facility=facility,
        document_type=doc_type,
        occurred_at=datetime.combine(day, time(hour, minute), tzinfo=ZoneInfo("Europe/Berlin")),
        **kwargs,
    )


def test_split_day_keeps_full_filtered_count_on_both_pages(client, staff_user, facility, doc_type_contact, monkeypatch):
    from core.views.event_list import EventListView

    monkeypatch.setattr(EventListView, "page_size", 3)
    for day, count in [(date(2026, 10, 24), 1), (date(2026, 10, 23), 4), (date(2026, 10, 22), 1)]:
        for hour in range(12, 12 + count):
            create_contact(facility, doc_type_contact, day, hour)
    first = list_response(client, staff_user)
    second = list_response(client, staff_user, {"page": "2"})
    assert headings(first) == [
        ("2026-10-24", "Sa, 24. Okt — 1 Kontakt"),
        ("2026-10-23", "Fr, 23. Okt — 4 Kontakte"),
    ]
    assert headings(second) == [
        ("2026-10-23", "Fr, 23. Okt — 4 Kontakte"),
        ("2026-10-22", "Do, 22. Okt — 1 Kontakt"),
    ]
    assert first.context["events"].paginator.count == second.context["events"].paginator.count == 6


@pytest.mark.parametrize("day", [date(2026, 3, 29), date(2026, 10, 25)])
def test_local_midnight_and_dst_transition_share_calendar_day(client, staff_user, facility, doc_type_contact, day):
    with timezone.override("Europe/Berlin"):
        midnight = create_contact(facility, doc_type_contact, day, 0, 30)
        assert midnight.occurred_at.astimezone(ZoneInfo("UTC")).date() < day
        for hour in (1, 3):
            create_contact(facility, doc_type_contact, day, hour, 30)
        response = list_response(client, staff_user)
        assert len(headings(response)) == 1
        assert headings(response)[0][0] == day.isoformat()
        assert headings(response)[0][1].endswith("— 3 Kontakte")


def test_day_count_uses_type_and_overnight_multirange_filters(client, staff_user, facility, doc_type_contact):
    shift = TimeFilter.objects.create(facility=facility, label="Night", start_time=time(22), end_time=time(6))
    other_type = DocumentType.objects.create(facility=facility, name="Other")
    for day in (date(2026, 10, 23), date(2026, 10, 24)):
        for hour in (0, 5, 12, 23):
            create_contact(facility, doc_type_contact, day, hour)
        create_contact(facility, other_type, day, 23)
    response = list_response(
        client,
        staff_user,
        {
            "doc_type": str(doc_type_contact.pk),
            "time_filter": str(shift.pk),
            "date_from": "2026-10-23",
            "date_to": "2026-10-23",
        },
    )
    assert headings(response) == [
        ("2026-10-24", "Sa, 24. Okt — 2 Kontakte"),
        ("2026-10-23", "Fr, 23. Okt — 1 Kontakt"),
    ]
    assert response.context["events"].paginator.count == 3


@pytest.mark.parametrize("role, count", [("assistant_user", 1), ("staff_user", 2), ("lead_user", 3), ("admin_user", 3)])
def test_day_counts_follow_list_role_and_tenant_visibility(client, request, role, count, facility, foreign_event):
    day = date(2026, 10, 23)
    for sensitivity in DocumentType.Sensitivity.values:
        doc_type = DocumentType.objects.create(facility=facility, name=sensitivity, sensitivity=sensitivity)
        create_contact(facility, doc_type, day)
        create_contact(facility, doc_type, day, is_deleted=True)
    response = list_response(client, request.getfixturevalue(role))
    assert response.context["events"].paginator.count == count
    assert len(headings(response)) == 1
    assert headings(response)[0][1].endswith(f"— {count} Kontakt" + ("e" if count != 1 else ""))
    assert foreign_event.pk not in {event.pk for event in response.context["events"]}


@pytest.mark.parametrize("page_number", [1, 2])
def test_page_query_budget_adds_one_grouped_query(client, staff_user, sample_event, monkeypatch, page_number):
    from core.views.event_list import EventListView

    monkeypatch.setattr(EventListView, "page_size", 5)
    for _ in range(24):
        Event.objects.create(
            facility=sample_event.facility,
            document_type=sample_event.document_type,
            client=sample_event.client,
            created_by=staff_user,
            occurred_at=sample_event.occurred_at,
            data_json={"dauer": 15, "notiz": "Page content"},
        )
    list_response(client, staff_user)
    with TestCase().assertNumQueries(24) as captured:
        response = client.get(reverse("core:event_list"), {"page": str(page_number)})
        assert len(response.context["events"]) == 5
    grouped = [query["sql"] for query in captured if "GROUP BY" in query["sql"]]
    assert len(grouped) == 1


def test_grouped_rows_have_time_only_and_accessible_columns(client, staff_user, sample_event):
    response = list_response(client, staff_user)
    html = Elements(response.content)
    card = re.search(
        r'<button[^>]*data-testid="event-card-toggle"[^>]*>(.*?)</button>', response.content.decode(), re.S
    )[1]
    time_cell = re.search(r'data-feed-column="time"[^>]*>(.*?)</span>', card, re.S)[1]
    assert " ".join(strip_tags(time_cell).split()) == timezone.localtime(sample_event.occurred_at).strftime("%H:%M")
    labels = ["Zeit", "Typ", "Person", "Angaben", "Erfasst von"]
    toggle = html.find("button", **{"aria-controls": f"event-detail-{sample_event.pk}"})[0]
    accessible_name = toggle["aria-label"]
    for label in labels:
        assert f"{label}:" in accessible_name
    assert sample_event.document_type.name in accessible_name
    assert sample_event.client.pseudonym in accessible_name
    assert sample_event.created_by.username in accessible_name
    assert html.find("div", **{"data-event-columns": "", "aria-hidden": "true"})
    assert html.find("button", **{"aria-controls": f"event-detail-{sample_event.pk}"})


def test_shared_card_still_renders_date_outside_grouped_list(sample_event):
    from django.template.loader import render_to_string

    body = render_to_string("components/_event_card.html", {"event": sample_event, "show_date": True})
    local = timezone.localtime(sample_event.occurred_at)
    assert local.strftime("%d.%m.%y") in body
    assert 'aria-label="Zeit"' not in body


@pytest.mark.django_db
def test_day_group_falls_back_to_page_rows_when_aggregate_misses_the_day(sample_event):
    from core.services.events.list_filters import event_list_day_groups

    groups = event_list_day_groups(Event.objects.none(), [sample_event])

    assert [(group["day"], group["count"]) for group in groups] == [(timezone.localdate(sample_event.occurred_at), 1)]
