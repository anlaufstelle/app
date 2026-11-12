"""Result totals beside the heading survive filtering and pagination."""

import re

import pytest

from core.models import Event
from tests.test_event_list import list_response
from tests.test_event_list_templates import Elements

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("htmx", [False, True])
@pytest.mark.parametrize("filter_name, expected", [("all", 2), ("anonymous", 1), ("empty", 0)])
def test_heading_total_matches_paginator_and_filters(client, staff_user, sample_event, htmx, filter_name, expected):
    Event.objects.create(
        facility=sample_event.facility,
        document_type=sample_event.document_type,
        occurred_at=sample_event.occurred_at,
        is_anonymous=True,
    )
    params = {"all": {}, "anonymous": {"anonymous": "yes"}, "empty": {"q": "absent"}}[filter_name]
    response = list_response(client, staff_user, params, **({"HTTP_HX_REQUEST": "true"} if htmx else {}))
    html = Elements(response.content)
    counter = html.find("span", id="event-result-count")
    assert len(counter) == 1
    assert response.context["events"].paginator.count == expected
    assert re.search(rf'id="event-result-count"[^>]*>\s*{expected} Treffer\s*</span>', response.content.decode())
    assert counter[0].get("hx-swap-oob") == ("outerHTML" if htmx else None)
    assert bool(html.find("h1")) is (not htmx)
    form = html.find("form", id="event-filters")[0]
    assert form["hx-swap"] == "innerHTML settle:0"
    assert form.get("hx-swap-oob") == ("outerHTML" if htmx else None)
    if expected == 0:
        assert "Keine Kontakte gefunden." in response.content.decode()


@pytest.mark.parametrize("page_number", [1, 2])
def test_heading_total_is_complete_result_not_page_size(client, staff_user, sample_event, page_number):
    for _ in range(25):
        Event.objects.create(
            facility=sample_event.facility,
            document_type=sample_event.document_type,
            occurred_at=sample_event.occurred_at,
        )
    response = list_response(client, staff_user, {"page": str(page_number)}, HTTP_HX_REQUEST="true")
    assert len(response.context["events"]) == (25 if page_number == 1 else 1)
    assert response.context["events"].paginator.count == 26
    assert re.search(r'id="event-result-count"[^>]*>\s*26 Treffer\s*</span>', response.content.decode())


def test_history_restore_has_full_page_counter_without_oob(client, staff_user, sample_event):
    response = list_response(client, staff_user, HTTP_HX_REQUEST="true", HTTP_HX_HISTORY_RESTORE_REQUEST="true")
    html = Elements(response.content)
    assert html.find("h1")
    assert len(html.find("span", id="event-result-count")) == 1
    assert "hx-swap-oob" not in html.find("span", id="event-result-count")[0]
