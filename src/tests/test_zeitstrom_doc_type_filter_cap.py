"""The Zeitstrom document-type filter applies before the feed cap.

On a day with more than ``FEED_MAX_PER_TYPE`` newer events of other types,
filtering by one document type must still show its events.
"""

from datetime import datetime, time, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.constants import FEED_MAX_PER_TYPE
from core.models import DocumentType, Event
from core.services.zeitstrom_filters import resolve_zeitstrom_state
from core.services.zeitstrom_watch import build_zeitstrom_component_validator
from tests._factories import make_document_type, make_event


@pytest.fixture
def crowded_day(facility, staff_user):
    """Yesterday: one old "Krise" event behind ``FEED_MAX_PER_TYPE`` newer "Kontakt" events."""
    day = timezone.localdate() - timedelta(days=1)
    noon = timezone.make_aware(datetime.combine(day, time(12, 0)))
    frequent = make_document_type(facility=facility, name="Kontakt", sensitivity=DocumentType.Sensitivity.NORMAL)
    rare = make_document_type(facility=facility, name="Krise", sensitivity=DocumentType.Sensitivity.NORMAL)
    Event.objects.bulk_create(
        [
            Event(
                facility=facility,
                document_type=frequent,
                occurred_at=noon + timedelta(seconds=index + 1),
                data_json={},
                created_by=staff_user,
            )
            for index in range(FEED_MAX_PER_TYPE)
        ]
    )
    rare_event = make_event(
        facility=facility,
        client=None,
        document_type=rare,
        occurred_at=noon - timedelta(hours=1),
        data_json={},
        created_by=staff_user,
    )
    return {"day": day, "rare": rare, "rare_event": rare_event}


def _params(crowded_day, **extra):
    return {
        "date": crowded_day["day"].isoformat(),
        "time_filter": "all",
        "doc_type": str(crowded_day["rare"].pk),
        **extra,
    }


def _event_ids(response):
    return [item["object"].pk for item in response.context["feed_items"] if item["type"] == "event"]


@pytest.mark.django_db
class TestDocTypeFilterBeforeCap:
    @pytest.mark.parametrize("feed_type", ["", "events"])
    def test_full_page_shows_filtered_event_behind_the_cap(self, client, staff_user, crowded_day, feed_type):
        client.force_login(staff_user)

        response = client.get(reverse("core:zeitstrom"), _params(crowded_day, type=feed_type))

        assert response.status_code == 200
        assert _event_ids(response) == [crowded_day["rare_event"].pk]

    @pytest.mark.parametrize("feed_type", ["", "events"])
    def test_feed_partial_shows_filtered_event_behind_the_cap(self, client, staff_user, crowded_day, feed_type):
        client.force_login(staff_user)

        response = client.get(
            reverse("core:zeitstrom_feed_partial"),
            _params(crowded_day, type=feed_type),
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert _event_ids(response) == [crowded_day["rare_event"].pk]

    def test_feed_validator_follows_the_filtered_event_behind_the_cap(self, facility, staff_user, crowded_day):
        state = resolve_zeitstrom_state(_params(crowded_day), facility)
        before = build_zeitstrom_component_validator(staff_user, facility, state, "feed")

        rare_event = crowded_day["rare_event"]
        rare_event.occurred_at = rare_event.occurred_at - timedelta(minutes=5)
        rare_event.save()

        after = build_zeitstrom_component_validator(staff_user, facility, state, "feed")
        assert before != after
