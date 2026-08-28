"""Tests für den kanonischen Zeitstrom-Zustand und Dokumentationstyp-Filter."""

from datetime import date, datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Event, TimeFilter
from core.services.events.tally import TallyLiveContext
from core.services.zeitstrom_filters import resolve_zeitstrom_state


@pytest.fixture
def time_filter_frueh(facility):
    return TimeFilter.objects.create(
        facility=facility,
        label="Frühdienst",
        start_time="08:00",
        end_time="16:00",
        sort_order=0,
    )


@pytest.fixture
def time_filter_spaet(facility):
    return TimeFilter.objects.create(
        facility=facility,
        label="Spätdienst",
        start_time="16:00",
        end_time="22:00",
        sort_order=1,
    )


@pytest.fixture
def time_filter_nacht(facility):
    return TimeFilter.objects.create(
        facility=facility,
        label="Nachtdienst",
        start_time="22:00",
        end_time="08:00",
        sort_order=2,
    )


@pytest.mark.django_db
class TestZeitstromDocTypeFilter:
    """Zeitstrom filtert Events nach Dokumentationstyp."""

    def test_filter_by_doc_type(self, client, staff_user, facility, doc_type_contact, doc_type_crisis):
        """Nur Events des gewählten Dokumentationstyps werden angezeigt."""
        client.force_login(staff_user)
        now = timezone.now()
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=now,
            data_json={},
            created_by=staff_user,
        )
        Event.objects.create(
            facility=facility,
            document_type=doc_type_crisis,
            occurred_at=now,
            data_json={},
            created_by=staff_user,
        )

        today = timezone.localdate().isoformat()
        response = client.get(
            reverse("core:zeitstrom_feed_partial"),
            {"doc_type": str(doc_type_contact.pk), "date": today},
        )
        assert response.status_code == 200
        feed_items = response.context["feed_items"]
        event_items = [i for i in feed_items if i["type"] == "event"]
        assert all(i["object"].document_type_id == doc_type_contact.pk for i in event_items)
        assert len(event_items) == 1

    def test_no_filter_returns_all(self, client, staff_user, facility, doc_type_contact, doc_type_crisis):
        """Ohne Filter werden alle Events angezeigt."""
        client.force_login(staff_user)
        now = timezone.now()
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=now,
            data_json={},
            created_by=staff_user,
        )
        Event.objects.create(
            facility=facility,
            document_type=doc_type_crisis,
            occurred_at=now,
            data_json={},
            created_by=staff_user,
        )

        today = timezone.localdate().isoformat()
        response = client.get(
            reverse("core:zeitstrom_feed_partial"),
            {"date": today},
        )
        assert response.status_code == 200
        feed_items = response.context["feed_items"]
        event_items = [i for i in feed_items if i["type"] == "event"]
        assert len(event_items) == 2

    def test_zeitstrom_has_document_types(self, client, staff_user, facility, doc_type_contact):
        """Zeitstrom-Seite enthält Dokumentationstypen im Kontext."""
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        assert response.status_code == 200
        assert "document_types" in response.context


@pytest.mark.django_db
class TestResolveZeitstromState:
    def test_normalizes_all_five_contract_fields(self, facility, time_filter_frueh):
        state = resolve_zeitstrom_state(
            {
                "date": "2025-06-15",
                "time_filter": str(time_filter_frueh.pk),
                "type": "events",
                "doc_type": "A1DCE83F-3DDB-418F-AC24-2A6BF67CB2D4",
                "view": "unsupported",
            },
            facility,
        )

        assert state.target_date == date(2025, 6, 15)
        assert state.time_filter == time_filter_frueh
        assert state.params == {
            "date": "2025-06-15",
            "time_filter": str(time_filter_frueh.pk),
            "type": "events",
            "doc_type": "a1dce83f-3ddb-418f-ac24-2a6bf67cb2d4",
            "view": "",
        }

    def test_invalid_values_fall_back_to_canonical_defaults(self, facility):
        state = resolve_zeitstrom_state(
            {
                "date": "invalid",
                "time_filter": "not-a-uuid",
                "type": "not-a-feed-type",
                "doc_type": "not-a-uuid",
                "view": "not-a-view",
            },
            facility,
        )

        assert state.params == {
            "date": timezone.localdate().isoformat(),
            "time_filter": "all",
            "type": "",
            "doc_type": "",
            "view": "",
        }

    def test_full_and_feed_partial_share_the_same_normalized_state(
        self, client, staff_user, facility, time_filter_frueh
    ):
        client.force_login(staff_user)
        params = {
            "date": "2025-06-15",
            "time_filter": str(time_filter_frueh.pk),
            "type": "events",
            "doc_type": "",
            "view": "",
        }

        full = client.get(reverse("core:zeitstrom"), params)
        partial = client.get(reverse("core:zeitstrom_feed_partial"), params)

        assert full.context["zeitstrom_filter_state"] == partial.context["zeitstrom_filter_state"]

    def test_explicit_feed_view_keeps_live_shift_selection(self, facility, time_filter_frueh):
        now = timezone.make_aware(datetime(2025, 6, 15, 10, 0))

        state = resolve_zeitstrom_state(
            {"date": "2025-06-15", "view": "feed"},
            facility,
            today=date(2025, 6, 15),
            now=now,
        )

        assert state.view == "feed"
        assert state.time_filter == time_filter_frueh
        assert state.is_handover is False

    def test_handover_at_0432_uses_previous_shift_on_night_shift_business_date(
        self,
        facility,
        time_filter_frueh,
        time_filter_spaet,
        time_filter_nacht,
    ):
        today = date(2025, 6, 15)
        now = timezone.make_aware(datetime(2025, 6, 15, 4, 32))

        state = resolve_zeitstrom_state(
            {"view": "uebergabe"},
            facility,
            today=today,
            now=now,
        )

        assert state.live_context.time_filter == time_filter_nacht
        assert state.live_context.shift_date == date(2025, 6, 14)
        assert state.time_filter == time_filter_spaet
        assert state.target_date == date(2025, 6, 14)

    def test_handover_at_0900_wraps_to_last_completed_night_shift(
        self,
        facility,
        time_filter_frueh,
        time_filter_spaet,
        time_filter_nacht,
    ):
        today = date(2025, 6, 15)
        now = timezone.make_aware(datetime(2025, 6, 15, 9, 0))

        state = resolve_zeitstrom_state(
            {"view": "uebergabe"},
            facility,
            today=today,
            now=now,
        )

        assert state.live_context.time_filter == time_filter_frueh
        assert state.live_context.shift_date == today
        assert state.time_filter == time_filter_nacht
        assert state.target_date == date(2025, 6, 14)

    def test_handover_at_0300_without_night_shift_uses_previous_days_late_shift(
        self,
        facility,
        time_filter_frueh,
        time_filter_spaet,
    ):
        today = date(2025, 6, 15)
        now = timezone.make_aware(datetime(2025, 6, 15, 3, 0))

        state = resolve_zeitstrom_state(
            {"view": "uebergabe"},
            facility,
            today=today,
            now=now,
        )

        assert state.live_context.time_filter is None
        assert state.time_filter == time_filter_spaet
        assert state.target_date == date(2025, 6, 14)

    def test_handover_tolerates_live_filter_missing_from_active_filters(
        self,
        facility,
        time_filter_frueh,
        time_filter_spaet,
    ):
        today = date(2025, 6, 15)
        now = timezone.make_aware(datetime(2025, 6, 15, 10, 0))
        stale_filter = TimeFilter.objects.create(
            facility=facility,
            label="Inzwischen deaktiviert",
            start_time="08:00",
            end_time="16:00",
            sort_order=2,
            is_active=False,
        )
        live_context = TallyLiveContext(stale_filter, today, now, now)

        state = resolve_zeitstrom_state(
            {"view": "uebergabe"},
            facility,
            today=today,
            now=now,
            live_context=live_context,
        )

        assert state.time_filter == time_filter_spaet
        assert state.target_date == date(2025, 6, 14)
