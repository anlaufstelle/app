"""Tests für die Team-Fokusbox der Zeitstrom-Sidebar (Refs #1128).

Die Sidebar zeigt nicht mehr eine flache Top-5-Liste, sondern gruppiert
offene/laufende Team-Aufgaben nach Handlungsdruck (überfällig, heute fällig,
dringend/wichtig, in Bearbeitung). Nur Gruppen mit Inhalt erscheinen; eine
Begrenzung samt Gesamtzahl macht transparent, dass nur ein Ausschnitt gezeigt
wird.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import WorkItem
from core.services.dashboard import build_focus_box


def _make_item(facility, user, **kwargs):
    defaults = {
        "facility": facility,
        "created_by": user,
        "item_type": WorkItem.ItemType.TASK,
        "status": WorkItem.Status.OPEN,
        "title": "Aufgabe",
    }
    defaults.update(kwargs)
    return WorkItem.objects.create(**defaults)


@pytest.mark.django_db
class TestBuildFocusBox:
    def test_empty_facility_returns_empty_box(self, facility):
        box = build_focus_box(facility)
        assert box["groups"] == []
        assert box["shown_count"] == 0
        assert box["total_open_count"] == 0
        assert box["has_more"] is False

    def test_overdue_item_in_overdue_group(self, facility, staff_user):
        today = timezone.localdate()
        _make_item(facility, staff_user, title="Überfällig", due_date=today - timedelta(days=2))
        box = build_focus_box(facility)
        keys = [g["key"] for g in box["groups"]]
        assert "overdue" in keys
        overdue = next(g for g in box["groups"] if g["key"] == "overdue")
        assert [wi.title for wi in overdue["items"]] == ["Überfällig"]

    def test_due_today_in_today_group(self, facility, staff_user):
        today = timezone.localdate()
        _make_item(facility, staff_user, title="Heute", due_date=today)
        box = build_focus_box(facility)
        keys = [g["key"] for g in box["groups"]]
        assert "today" in keys
        assert "overdue" not in keys

    def test_urgent_without_due_in_priority_group(self, facility, staff_user):
        _make_item(facility, staff_user, title="Dringend", priority=WorkItem.Priority.URGENT)
        box = build_focus_box(facility)
        keys = [g["key"] for g in box["groups"]]
        assert "priority" in keys
        prio = next(g for g in box["groups"] if g["key"] == "priority")
        assert [wi.title for wi in prio["items"]] == ["Dringend"]

    def test_in_progress_normal_in_inprogress_group(self, facility, staff_user):
        _make_item(
            facility,
            staff_user,
            title="Läuft",
            status=WorkItem.Status.IN_PROGRESS,
        )
        box = build_focus_box(facility)
        keys = [g["key"] for g in box["groups"]]
        assert keys == ["in_progress"]

    def test_open_normal_without_due_is_excluded(self, facility, staff_user):
        """Eine offene Normal-Aufgabe ohne Frist erzeugt keinen Handlungsdruck."""
        _make_item(facility, staff_user, title="Irgendwann")
        box = build_focus_box(facility)
        assert box["groups"] == []
        assert box["shown_count"] == 0
        # Sie zählt aber zu den offenen Aufgaben (Transparenz-Zähler).
        assert box["total_open_count"] == 1
        assert box["has_more"] is True

    def test_item_appears_in_only_one_group(self, facility, staff_user):
        """Überfällig + dringend + in Bearbeitung → nur die höchste Stufe."""
        today = timezone.localdate()
        _make_item(
            facility,
            staff_user,
            title="Alles",
            due_date=today - timedelta(days=1),
            priority=WorkItem.Priority.URGENT,
            status=WorkItem.Status.IN_PROGRESS,
        )
        box = build_focus_box(facility)
        all_items = [wi for g in box["groups"] for wi in g["items"]]
        assert len(all_items) == 1
        assert box["groups"][0]["key"] == "overdue"

    def test_group_order_is_pressure_descending(self, facility, staff_user):
        today = timezone.localdate()
        _make_item(facility, staff_user, title="O", due_date=today - timedelta(days=1))
        _make_item(facility, staff_user, title="H", due_date=today)
        _make_item(facility, staff_user, title="P", priority=WorkItem.Priority.IMPORTANT)
        _make_item(facility, staff_user, title="I", status=WorkItem.Status.IN_PROGRESS)
        box = build_focus_box(facility)
        assert [g["key"] for g in box["groups"]] == ["overdue", "today", "priority", "in_progress"]

    def test_overdue_sorted_by_due_date_then_priority(self, facility, staff_user):
        today = timezone.localdate()
        _make_item(facility, staff_user, title="Vorgestern", due_date=today - timedelta(days=2))
        _make_item(facility, staff_user, title="Gestern", due_date=today - timedelta(days=1))
        box = build_focus_box(facility)
        overdue = next(g for g in box["groups"] if g["key"] == "overdue")
        assert [wi.title for wi in overdue["items"]] == ["Vorgestern", "Gestern"]

    def test_limit_caps_shown_items_and_sets_has_more(self, facility, staff_user):
        today = timezone.localdate()
        for i in range(10):
            _make_item(facility, staff_user, title=f"Ü{i}", due_date=today - timedelta(days=i + 1))
        box = build_focus_box(facility)
        assert box["shown_count"] == 8
        assert box["total_open_count"] == 10
        assert box["has_more"] is True

    def test_no_has_more_when_all_shown(self, facility, staff_user):
        today = timezone.localdate()
        _make_item(facility, staff_user, title="A", due_date=today - timedelta(days=1))
        _make_item(facility, staff_user, title="B", due_date=today)
        box = build_focus_box(facility)
        assert box["shown_count"] == 2
        assert box["total_open_count"] == 2
        assert box["has_more"] is False

    def test_done_and_dismissed_excluded(self, facility, staff_user):
        today = timezone.localdate()
        _make_item(
            facility,
            staff_user,
            title="Erledigt",
            status=WorkItem.Status.DONE,
            due_date=today - timedelta(days=1),
        )
        _make_item(
            facility,
            staff_user,
            title="Verworfen",
            status=WorkItem.Status.DISMISSED,
            due_date=today - timedelta(days=1),
        )
        box = build_focus_box(facility)
        assert box["groups"] == []
        assert box["total_open_count"] == 0

    def test_facility_scoping(self, facility, other_facility, staff_user):
        today = timezone.localdate()
        _make_item(facility, staff_user, title="Meine", due_date=today - timedelta(days=1))
        _make_item(other_facility, staff_user, title="Fremde", due_date=today - timedelta(days=1))
        box = build_focus_box(facility)
        all_items = [wi for g in box["groups"] for wi in g["items"]]
        assert [wi.title for wi in all_items] == ["Meine"]
        assert box["total_open_count"] == 1

    def test_unassigned_team_item_included(self, facility, staff_user):
        """Team-Fokusbox zeigt Aufgaben unabhängig von der zugewiesenen Person."""
        today = timezone.localdate()
        _make_item(
            facility,
            staff_user,
            title="Niemandem zugewiesen",
            assigned_to=None,
            due_date=today - timedelta(days=1),
        )
        box = build_focus_box(facility)
        overdue = next(g for g in box["groups"] if g["key"] == "overdue")
        assert [wi.title for wi in overdue["items"]] == ["Niemandem zugewiesen"]
