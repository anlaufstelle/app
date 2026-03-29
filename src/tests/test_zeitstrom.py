"""Tests für den Zeitstrom (unified activity stream)."""

from datetime import date, datetime, time, timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Event, TimeFilter
from core.services.feed import get_time_range


@pytest.fixture
def time_filter_frueh(facility):
    return TimeFilter.objects.create(
        facility=facility,
        label="Frühdienst",
        start_time="08:00",
        end_time="16:00",
        is_default=True,
        sort_order=0,
    )


@pytest.fixture
def time_filter_nacht(facility):
    return TimeFilter.objects.create(
        facility=facility,
        label="Nachtdienst",
        start_time=time(22, 0),
        end_time=time(8, 0),
        sort_order=2,
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


@pytest.mark.django_db
class TestZeitstromView:
    def test_zeitstrom_renders(self, client, staff_user, facility):
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        assert response.status_code == 200
        assert "Zeitstrom" in response.content.decode()

    def test_zeitstrom_is_root_url(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get("/")
        assert response.status_code == 200
        assert "Zeitstrom" in response.content.decode()

    def test_zeitstrom_requires_auth(self, client):
        response = client.get(reverse("core:zeitstrom"))
        assert response.status_code == 302

    def test_context_contains_feed_items(self, client, staff_user, facility):
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        assert "feed_items" in response.context

    def test_shows_events_for_today(self, client, staff_user, facility, doc_type_contact, client_identified):
        now = timezone.now()
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=now,
            data_json={"dauer": 10},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        feed_items = response.context["feed_items"]
        assert len(feed_items) >= 1

    def test_time_filter_tabs(self, client, staff_user, time_filter_frueh, time_filter_spaet):
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        content = response.content.decode()
        assert "Frühdienst" in content
        assert "Spätdienst" in content

    def test_time_filter_selection(self, client, staff_user, facility, time_filter_frueh, doc_type_contact):
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.get(
            reverse("core:zeitstrom"),
            {"time_filter": str(time_filter_frueh.pk)},
        )
        assert response.context["selected_filter"] == time_filter_frueh

    def test_doc_type_filter(self, client, staff_user, facility, doc_type_contact, doc_type_crisis):
        now = timezone.now()
        Event.objects.create(
            facility=facility, document_type=doc_type_contact, occurred_at=now, data_json={}, created_by=staff_user
        )
        Event.objects.create(
            facility=facility, document_type=doc_type_crisis, occurred_at=now, data_json={}, created_by=staff_user
        )
        client.force_login(staff_user)
        response = client.get(
            reverse("core:zeitstrom"),
            {"doc_type": str(doc_type_contact.pk), "time_filter": "all"},
        )
        feed_items = response.context["feed_items"]
        event_items = [i for i in feed_items if i["type"] == "event"]
        assert all(i["object"].document_type_id == doc_type_contact.pk for i in event_items)

    def test_date_navigation_context(self, client, staff_user, facility):
        client.force_login(staff_user)
        today = timezone.localdate()
        response = client.get(reverse("core:zeitstrom"))
        assert response.context["target_date"] == today
        assert response.context["prev_date"] == today - timedelta(days=1)
        assert response.context["next_date"] == today + timedelta(days=1)

    def test_date_param(self, client, staff_user, facility):
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"), {"date": "2025-06-15"})
        assert response.context["target_date"] == date(2025, 6, 15)

    def test_workitems_sidebar(self, client, staff_user, sample_workitem):
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        assert "workitems" in response.context
        workitems = list(response.context["workitems"])
        assert len(workitems) >= 1

    def test_ban_banner_context(self, client, staff_user, facility):
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        assert "active_bans" in response.context

    def test_document_types_in_context(self, client, staff_user, facility, doc_type_contact):
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        assert "document_types" in response.context

    def test_facility_scoping(self, client, staff_user, facility, other_facility, doc_type_contact):
        """Events from other facilities are not shown."""
        other_doc = doc_type_contact.__class__.objects.create(
            facility=other_facility, name="Kontakt", category="contact"
        )
        Event.objects.create(
            facility=other_facility,
            document_type=other_doc,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        feed_items = response.context["feed_items"]
        assert len(feed_items) == 0


@pytest.mark.django_db
class TestZeitstromFeedPartial:
    def test_feed_partial_renders(self, client, staff_user, facility):
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom_feed_partial"))
        assert response.status_code == 200
        assert "feed_items" in response.context

    def test_feed_partial_requires_auth(self, client):
        response = client.get(reverse("core:zeitstrom_feed_partial"))
        assert response.status_code == 302

    def test_feed_partial_with_time_filter(self, client, staff_user, time_filter_frueh):
        client.force_login(staff_user)
        response = client.get(
            reverse("core:zeitstrom_feed_partial"),
            {"time_filter": str(time_filter_frueh.pk)},
        )
        assert response.status_code == 200

    def test_feed_partial_with_type_filter(self, client, staff_user, facility, sample_event, client_identified):
        from core.models import Activity
        from core.services.activity import log_activity

        log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.CREATED,
            target=client_identified,
            summary="Test",
        )
        client.force_login(staff_user)
        response = client.get(
            reverse("core:zeitstrom_feed_partial"),
            {"type": "events"},
        )
        feed_items = response.context["feed_items"]
        types = {item["type"] for item in feed_items}
        assert "activity" not in types

    def test_feed_partial_with_doc_type_filter(self, client, staff_user, facility, doc_type_contact, doc_type_crisis):
        now = timezone.now()
        Event.objects.create(
            facility=facility, document_type=doc_type_contact, occurred_at=now, data_json={}, created_by=staff_user
        )
        Event.objects.create(
            facility=facility, document_type=doc_type_crisis, occurred_at=now, data_json={}, created_by=staff_user
        )
        client.force_login(staff_user)
        today = timezone.localdate().isoformat()
        response = client.get(
            reverse("core:zeitstrom_feed_partial"),
            {"doc_type": str(doc_type_contact.pk), "date": today},
        )
        assert response.status_code == 200
        feed_items = response.context["feed_items"]
        event_items = [i for i in feed_items if i["type"] == "event"]
        assert len(event_items) == 1


@pytest.mark.django_db
class TestZeitstromRedirects:
    def test_aktivitaetslog_redirects_to_zeitstrom(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get("/aktivitaetslog/")
        assert response.status_code == 301
        assert response.url == "/"

    def test_timeline_redirects_to_zeitstrom(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get("/timeline/")
        assert response.status_code == 301
        assert response.url == "/"


@pytest.mark.django_db
class TestZeitstromPreviewEnrichment:
    """Tests that feed items get preview_fields via view integration."""

    def test_event_preview_fields_in_context(self, client, staff_user, facility, doc_type_contact, client_identified):
        """Feed items should have preview_fields after enrichment in the view."""
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 42},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        feed_items = response.context["feed_items"]
        event_items = [i for i in feed_items if i["type"] == "event"]
        assert len(event_items) >= 1
        event = event_items[0]["object"]
        assert hasattr(event, "preview_fields")
        assert any(pf["label"] == "Dauer" for pf in event.preview_fields)

    def test_handover_summary_with_filter(self, client, staff_user, time_filter_frueh):
        """When a TimeFilter is selected, handover_summary is in context."""
        client.force_login(staff_user)
        response = client.get(
            reverse("core:zeitstrom"),
            {"time_filter": str(time_filter_frueh.pk)},
        )
        assert "handover_summary" in response.context
        assert response.context["handover_summary"] is not None

    def test_no_handover_summary_without_filter(self, client, staff_user, facility):
        """Without a TimeFilter, handover_summary should be None."""
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"), {"time_filter": "all"})
        assert response.context.get("handover_summary") is None


@pytest.mark.django_db
class TestHandoverView:
    """Tests for the /uebergabe/ page."""

    def test_handover_page_renders(self, client, staff_user, facility):
        client.force_login(staff_user)
        response = client.get(reverse("core:handover"))
        assert response.status_code == 200
        assert "Übergabe" in response.content.decode()

    def test_handover_requires_auth(self, client):
        response = client.get(reverse("core:handover"))
        assert response.status_code == 302

    def test_handover_context_has_summary(self, client, staff_user, facility):
        client.force_login(staff_user)
        response = client.get(reverse("core:handover"))
        assert "summary" in response.context

    def test_handover_with_time_filter(self, client, staff_user, time_filter_frueh):
        client.force_login(staff_user)
        response = client.get(
            reverse("core:handover"),
            {"time_filter": str(time_filter_frueh.pk)},
        )
        assert response.status_code == 200
        summary = response.context["summary"]
        assert summary["shift_label"] == "Frühdienst"


@pytest.mark.django_db
class TestTimeRangeService:
    """Tests for the get_time_range function (migrated from test_timeline.py)."""

    def test_nachtdienst_spans_two_days(self, facility, staff_user, doc_type_contact, time_filter_nacht):
        """Nachtdienst for date X covers X 22:00 to X+1 08:00."""
        target_date = date(2025, 6, 15)
        start_dt, end_dt = get_time_range(target_date, time_filter_nacht)

        assert start_dt.hour == 22
        assert start_dt.date() == target_date
        assert end_dt.hour == 8
        assert end_dt.date() == target_date + timedelta(days=1)

    def test_nachtdienst_morning_event_belongs_to_previous_day(
        self, facility, staff_user, doc_type_contact, time_filter_nacht
    ):
        """Event at 03:42 should be in previous day's night shift range."""
        event_date = date(2025, 6, 15)
        previous_date = event_date - timedelta(days=1)

        event = Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.make_aware(datetime.combine(event_date, time(3, 42))),
            data_json={},
            created_by=staff_user,
        )

        # Previous day's night shift range should include 03:42 on event_date
        start_prev, end_prev = get_time_range(previous_date, time_filter_nacht)
        assert start_prev <= event.occurred_at <= end_prev

        # Same day's night shift range should NOT include 03:42
        start_same, end_same = get_time_range(event_date, time_filter_nacht)
        assert not (start_same <= event.occurred_at <= end_same)

    def test_auto_select_adjusts_date_for_morning_hours(
        self, client, staff_user, facility, time_filter_nacht, time_filter_frueh
    ):
        """Auto-select at 04:32 picks night shift and sets target_date to yesterday."""
        today = date(2025, 6, 15)
        mock_now = timezone.make_aware(datetime.combine(today, time(4, 32)))

        with (
            patch("core.views.zeitstrom.timezone.localdate", return_value=today),
            patch("core.views.zeitstrom.timezone.localtime", return_value=mock_now),
        ):
            client.force_login(staff_user)
            response = client.get(reverse("core:zeitstrom"))

        assert response.context["selected_filter"] == time_filter_nacht
        assert response.context["target_date"] == today - timedelta(days=1)
