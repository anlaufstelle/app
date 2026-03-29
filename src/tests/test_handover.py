"""Tests for handover summary service."""

from datetime import datetime, time, timedelta

import pytest
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from core.models import Activity, DocumentType, Event, TimeFilter, WorkItem
from core.services.handover import build_handover_summary


@pytest.fixture
def time_filter_frueh(facility):
    return TimeFilter.objects.create(
        facility=facility,
        label="Fruhdienst",
        start_time=time(0, 0),
        end_time=time(23, 59),
        is_default=True,
        sort_order=0,
    )


@pytest.fixture
def doc_type_ban(facility):
    return DocumentType.objects.create(
        facility=facility,
        name="Hausverbot",
        category=DocumentType.Category.ADMIN,
        sensitivity=DocumentType.Sensitivity.ELEVATED,
        system_type="ban",
        color="red",
    )


def _make_activity(facility, user, target_obj, **kwargs):
    """Helper to create an Activity with required GenericForeignKey fields."""
    ct = ContentType.objects.get_for_model(target_obj)
    defaults = {
        "facility": facility,
        "actor": user,
        "verb": "created",
        "summary": "Test activity",
        "occurred_at": timezone.now(),
        "target_type": ct,
        "target_id": target_obj.pk,
    }
    defaults.update(kwargs)
    return Activity.objects.create(**defaults)


@pytest.mark.django_db
class TestBuildHandoverSummary:
    def test_stats_aggregation(self, facility, staff_user, client_identified, doc_type_contact, time_filter_frueh):
        """Events, activities, workitems in range are counted."""
        today = timezone.localdate()
        now = timezone.make_aware(datetime.combine(today, time(12, 0)))
        ev1 = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=now,
            data_json={},
            created_by=staff_user,
        )
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=now + timedelta(minutes=30),
            data_json={},
            created_by=staff_user,
        )
        _make_activity(facility, staff_user, ev1, occurred_at=now)

        result = build_handover_summary(facility, today, time_filter_frueh, staff_user)
        assert result["stats"]["events_total"] == 2
        assert result["stats"]["activities_total"] == 1
        assert result["shift_label"] == "Fruhdienst"

    def test_events_by_type_breakdown(
        self, facility, staff_user, client_identified, doc_type_contact, time_filter_frueh
    ):
        """Events are grouped by document type."""
        today = timezone.localdate()
        now = timezone.make_aware(datetime.combine(today, time(12, 0)))
        doc_type_contact.color = "indigo"
        doc_type_contact.save()
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=now,
            data_json={},
            created_by=staff_user,
        )
        result = build_handover_summary(facility, today, time_filter_frueh, staff_user)
        by_type = result["stats"]["events_by_type"]
        assert len(by_type) == 1
        assert by_type[0]["document_type__name"] == "Kontakt"
        assert by_type[0]["count"] == 1

    def test_crisis_events_in_highlights(
        self, facility, staff_user, client_identified, doc_type_crisis, time_filter_frueh
    ):
        """Crisis events appear in highlights."""
        today = timezone.localdate()
        now = timezone.make_aware(datetime.combine(today, time(12, 0)))
        doc_type_crisis.system_type = "crisis"
        doc_type_crisis.save()
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_crisis,
            occurred_at=now,
            data_json={},
            created_by=staff_user,
        )
        result = build_handover_summary(facility, today, time_filter_frueh, staff_user)
        crisis_highlights = [h for h in result["highlights"] if h["type"] == "crisis"]
        assert len(crisis_highlights) == 1

    def test_ban_events_in_highlights(self, facility, staff_user, client_identified, doc_type_ban, time_filter_frueh):
        """Ban events appear in highlights."""
        today = timezone.localdate()
        now = timezone.make_aware(datetime.combine(today, time(12, 0)))
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_ban,
            occurred_at=now,
            data_json={},
            created_by=staff_user,
        )
        result = build_handover_summary(facility, today, time_filter_frueh, staff_user)
        ban_highlights = [h for h in result["highlights"] if h["type"] == "ban"]
        assert len(ban_highlights) == 1

    def test_urgent_tasks_in_highlights(self, facility, staff_user, client_identified, time_filter_frueh):
        """Urgent/important workitems created today appear in highlights."""
        today = timezone.localdate()
        # WorkItem.created_at is auto_now_add, so it will be "now" which is today
        WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            item_type="task",
            status="open",
            priority="urgent",
            title="Dringend",
        )
        result = build_handover_summary(facility, today, time_filter_frueh, staff_user)
        task_highlights = [h for h in result["highlights"] if h["type"] == "task"]
        assert len(task_highlights) == 1

    def test_time_range_scoping(self, facility, staff_user, client_identified, doc_type_contact):
        """Events outside the time range are not counted."""
        today = timezone.localdate()
        # Narrow time filter: 09:00 - 11:00
        narrow_filter = TimeFilter.objects.create(
            facility=facility,
            label="Vormittag",
            start_time=time(9, 0),
            end_time=time(11, 0),
            is_default=False,
            sort_order=1,
        )
        in_range = timezone.make_aware(timezone.datetime(today.year, today.month, today.day, 10, 0))
        out_of_range = timezone.make_aware(timezone.datetime(today.year, today.month, today.day, 20, 0))
        # In range (10:00)
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=in_range,
            data_json={},
            created_by=staff_user,
        )
        # Out of range (20:00)
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=out_of_range,
            data_json={},
            created_by=staff_user,
        )
        result = build_handover_summary(facility, today, narrow_filter, staff_user)
        assert result["stats"]["events_total"] == 1

    def test_empty_shift(self, facility, staff_user, time_filter_frueh):
        """No items -> all stats zero, empty highlights."""
        today = timezone.localdate()
        result = build_handover_summary(facility, today, time_filter_frueh, staff_user)
        assert result["stats"]["events_total"] == 0
        assert result["stats"]["activities_total"] == 0
        assert result["highlights"] == []

    def test_full_day_without_filter(self, facility, staff_user, client_identified, doc_type_contact):
        """Without time_filter, summarizes the full day."""
        today = timezone.localdate()
        now = timezone.make_aware(datetime.combine(today, time(12, 0)))
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=now,
            data_json={},
            created_by=staff_user,
        )
        result = build_handover_summary(facility, today, None, staff_user)
        assert result["shift_label"] == "Ganzer Tag"
        assert result["shift_range"] == "00:00 – 23:59"
        assert result["stats"]["events_total"] == 1
