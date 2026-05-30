"""Follow-Up-Tests für Mutation-Survivors in ``core.services.case.handover``.

Refs Welle 7 (#930). Ziel: Mutationen in den Branch-Grenzen von
``_collect_highlights`` (33 Survivors) und ``_collect_open_tasks``
(52 Survivors) gezielt killen.

Beide Funktionen kombinieren ORM-Filter (priority/status/system_type),
explizite Slice-Limits (``[:10]``) und Sortierreihenfolgen — also genau
die Stellen, an denen Mutmut gerne ``<=`` ↔ ``<``, ``in`` ↔ ``not in``,
``-foo`` ↔ ``foo`` und Konstanten-Off-by-One mutiert.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import DocumentType, Event, WorkItem
from core.services.case import _collect_highlights, _collect_open_tasks

# ---------------------------------------------------------------------------
# Helper-Factories — bewusst klein, damit Tests nicht von komplexen
# Fixture-Bäumen abhängen. Bestehende ``facility``-/``*_user``-Fixtures
# kommen aus ``conftest.py``.
# ---------------------------------------------------------------------------


def _make_doc_type(facility, *, system_type: str | None = None, name: str = "Doc") -> DocumentType:
    kwargs = dict(
        facility=facility,
        category=DocumentType.Category.SERVICE,
        name=name,
    )
    if system_type:
        kwargs["system_type"] = system_type
    return DocumentType.objects.create(**kwargs)


def _make_event(
    facility,
    doc_type,
    *,
    client=None,
    user=None,
    offset_minutes: int = 0,
) -> Event:
    """Event mit ``occurred_at = now + offset_minutes`` (oft negativ)."""
    return Event.objects.create(
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=timezone.now() + timedelta(minutes=offset_minutes),
        created_by=user,
    )


def _make_workitem(
    facility,
    user,
    *,
    title: str = "WI",
    priority: str = WorkItem.Priority.NORMAL,
    status: str = WorkItem.Status.OPEN,
    assigned_to=None,
    due_date=None,
    created_offset_minutes: int = 0,
) -> WorkItem:
    wi = WorkItem.objects.create(
        facility=facility,
        created_by=user,
        assigned_to=assigned_to,
        title=title,
        priority=priority,
        status=status,
        due_date=due_date,
    )
    if created_offset_minutes:
        # ``created_at`` ist auto_now_add; einmaliger Bulk-Update fuer Tests OK.
        new_ts = timezone.now() + timedelta(minutes=created_offset_minutes)
        WorkItem.objects.filter(pk=wi.pk).update(created_at=new_ts)
        wi.refresh_from_db()
    return wi


def _wide_time_range():
    now = timezone.now()
    return (now - timedelta(days=1), now + timedelta(days=1))


# ---------------------------------------------------------------------------
# _collect_highlights
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCollectHighlights:
    """Refs Welle 7 — `_collect_highlights`.

    Funktionsverhalten:
    - Sammelt bis zu 10 Crisis-Events, 10 Ban-Events, 10 Urgent/Important-
      Tasks (jeweils im Zeitfenster für Tasks).
    - Mischt sie und sortiert nach Zeit DESC.
    - Anreicherung von event-typ-Einträgen über ``enrich_events_with_preview``.
    """

    def test_returns_empty_when_no_events_and_no_tasks(self, facility, staff_user):
        visible_events = Event.objects.visible_to(staff_user).filter(facility=facility)
        result = _collect_highlights(facility, visible_events, _wide_time_range(), staff_user)
        assert result == []

    def test_crisis_event_listed_with_type_crisis(self, facility, staff_user):
        dt_crisis = _make_doc_type(facility, system_type=DocumentType.SystemType.CRISIS, name="Krise")
        _make_event(facility, dt_crisis, user=staff_user)
        visible_events = Event.objects.visible_to(staff_user).filter(facility=facility)
        result = _collect_highlights(facility, visible_events, _wide_time_range(), staff_user)
        types = [h["type"] for h in result]
        assert types == ["crisis"]

    def test_ban_event_listed_with_type_ban(self, facility, staff_user):
        dt_ban = _make_doc_type(facility, system_type=DocumentType.SystemType.BAN, name="Ban")
        _make_event(facility, dt_ban, user=staff_user)
        visible_events = Event.objects.visible_to(staff_user).filter(facility=facility)
        result = _collect_highlights(facility, visible_events, _wide_time_range(), staff_user)
        assert [h["type"] for h in result] == ["ban"]

    def test_non_crisis_non_ban_event_excluded(self, facility, staff_user):
        """Boundary: ``system_type="crisis"`` / ``"ban"`` als exakte Filter.

        Ein Event ohne system_type darf nicht in Highlights landen.
        """
        dt_plain = _make_doc_type(facility, name="Kontakt")
        _make_event(facility, dt_plain, user=staff_user)
        visible_events = Event.objects.visible_to(staff_user).filter(facility=facility)
        result = _collect_highlights(facility, visible_events, _wide_time_range(), staff_user)
        assert result == []

    def test_urgent_task_in_range_listed_as_task(self, facility, lead_user):
        _make_workitem(facility, lead_user, priority=WorkItem.Priority.URGENT, title="urgent task")
        visible_events = Event.objects.visible_to(lead_user).filter(facility=facility)
        result = _collect_highlights(facility, visible_events, _wide_time_range(), lead_user)
        assert [h["type"] for h in result] == ["task"]

    def test_important_task_in_range_listed_as_task(self, facility, lead_user):
        """Boundary: ``priority__in=["urgent", "important"]`` — beide Werte erlaubt."""
        _make_workitem(facility, lead_user, priority=WorkItem.Priority.IMPORTANT)
        visible_events = Event.objects.visible_to(lead_user).filter(facility=facility)
        result = _collect_highlights(facility, visible_events, _wide_time_range(), lead_user)
        assert len(result) == 1
        assert result[0]["type"] == "task"

    def test_normal_priority_task_excluded(self, facility, lead_user):
        """Boundary: ``priority__in`` schliesst ``normal`` aus."""
        _make_workitem(facility, lead_user, priority=WorkItem.Priority.NORMAL)
        visible_events = Event.objects.visible_to(lead_user).filter(facility=facility)
        result = _collect_highlights(facility, visible_events, _wide_time_range(), lead_user)
        assert result == []

    def test_task_outside_time_range_excluded(self, facility, lead_user):
        """Boundary: ``created_at__range=time_range`` filtert ausserhalb liegende Tasks."""
        _make_workitem(facility, lead_user, priority=WorkItem.Priority.URGENT, created_offset_minutes=-3 * 24 * 60)
        visible_events = Event.objects.visible_to(lead_user).filter(facility=facility)
        result = _collect_highlights(facility, visible_events, _wide_time_range(), lead_user)
        assert result == []

    def test_crisis_events_cap_at_10(self, facility, staff_user):
        """Boundary: ``[:10]``-Slice. 11 Crisis-Events → genau 10 in Highlights."""
        dt_crisis = _make_doc_type(facility, system_type=DocumentType.SystemType.CRISIS, name="Krise")
        for i in range(11):
            _make_event(facility, dt_crisis, user=staff_user, offset_minutes=-i)
        visible_events = Event.objects.visible_to(staff_user).filter(facility=facility)
        result = _collect_highlights(facility, visible_events, _wide_time_range(), staff_user)
        assert sum(1 for h in result if h["type"] == "crisis") == 10

    def test_ban_events_cap_at_10(self, facility, staff_user):
        dt_ban = _make_doc_type(facility, system_type=DocumentType.SystemType.BAN, name="Ban")
        for i in range(11):
            _make_event(facility, dt_ban, user=staff_user, offset_minutes=-i)
        visible_events = Event.objects.visible_to(staff_user).filter(facility=facility)
        result = _collect_highlights(facility, visible_events, _wide_time_range(), staff_user)
        assert sum(1 for h in result if h["type"] == "ban") == 10

    def test_urgent_tasks_cap_at_10(self, facility, lead_user):
        for i in range(11):
            _make_workitem(
                facility,
                lead_user,
                priority=WorkItem.Priority.URGENT,
                title=f"u{i}",
                created_offset_minutes=-i,
            )
        visible_events = Event.objects.visible_to(lead_user).filter(facility=facility)
        result = _collect_highlights(facility, visible_events, _wide_time_range(), lead_user)
        assert sum(1 for h in result if h["type"] == "task") == 10

    def test_sort_is_descending_by_time(self, facility, staff_user, lead_user):
        """``highlights.sort(key=lambda h: h["time"], reverse=True)``.

        Mutmut könnte ``reverse=True`` → ``reverse=False`` flippen.
        """
        dt_crisis = _make_doc_type(facility, system_type=DocumentType.SystemType.CRISIS)
        # Drei Events in unterschiedlicher Reihenfolge erzeugen, aber
        # ``occurred_at`` zeitlich gestaffelt: oldest → middle → newest.
        e_old = _make_event(facility, dt_crisis, user=staff_user, offset_minutes=-60)
        e_new = _make_event(facility, dt_crisis, user=staff_user, offset_minutes=-1)
        e_mid = _make_event(facility, dt_crisis, user=staff_user, offset_minutes=-30)
        visible_events = Event.objects.visible_to(staff_user).filter(facility=facility)
        result = _collect_highlights(facility, visible_events, _wide_time_range(), staff_user)
        times = [h["time"] for h in result]
        assert times == [e_new.occurred_at, e_mid.occurred_at, e_old.occurred_at]


# ---------------------------------------------------------------------------
# _collect_open_tasks
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCollectOpenTasks:
    """Refs Welle 7 — `_collect_open_tasks`.

    Funktionsverhalten:
    - Filter ``status__in=["open", "in_progress"]``.
    - LEAD/FACILITY_ADMIN sehen alle Tasks der Facility,
      STAFF/ASSISTANT nur eigene/zugewiesene (analog Mutate-Permission).
    - Priorität-Sortierung: urgent(0), important(1), normal(2).
    - Final sort: ``priority_rank, due_date, -created_at``, slice ``[:10]``.
    """

    def test_includes_open_status(self, facility, lead_user):
        wi = _make_workitem(facility, lead_user, status=WorkItem.Status.OPEN)
        result = list(_collect_open_tasks(facility, lead_user))
        assert wi in result

    def test_includes_in_progress_status(self, facility, lead_user):
        wi = _make_workitem(facility, lead_user, status=WorkItem.Status.IN_PROGRESS)
        result = list(_collect_open_tasks(facility, lead_user))
        assert wi in result

    def test_excludes_done_status(self, facility, lead_user):
        """Boundary: ``status__in=["open", "in_progress"]`` schliesst ``done`` aus."""
        _make_workitem(facility, lead_user, status=WorkItem.Status.DONE)
        result = list(_collect_open_tasks(facility, lead_user))
        assert result == []

    def test_excludes_dismissed_status(self, facility, lead_user):
        _make_workitem(facility, lead_user, status=WorkItem.Status.DISMISSED)
        result = list(_collect_open_tasks(facility, lead_user))
        assert result == []

    def test_lead_sees_others_tasks(self, facility, lead_user, staff_user):
        """LEAD darf alle Facility-Tasks sehen."""
        wi = _make_workitem(facility, staff_user, title="staff-created")
        result = list(_collect_open_tasks(facility, lead_user))
        assert wi in result

    def test_facility_admin_sees_others_tasks(self, facility, admin_user, staff_user):
        wi = _make_workitem(facility, staff_user, title="staff-created")
        result = list(_collect_open_tasks(facility, admin_user))
        assert wi in result

    def test_staff_sees_only_own_or_assigned(self, facility, staff_user, lead_user):
        """STAFF darf nur eigene oder zugewiesene Tasks sehen.

        Boundary: ``Q(created_by=user) | Q(assigned_to=user)`` — wenn
        Mutmut ``|`` ↔ ``&`` flippt, würde nur Tasks zaehlen, wo BEIDES
        zutrifft. Wir prüfen, dass beide Disjunkte einzeln greifen.
        """
        own = _make_workitem(facility, staff_user, title="own")
        assigned = _make_workitem(facility, lead_user, title="assigned", assigned_to=staff_user)
        # Task von anderem User, NICHT staff_user assigned → darf nicht sichtbar sein.
        other = _make_workitem(facility, lead_user, title="other")
        result = list(_collect_open_tasks(facility, staff_user))
        assert own in result
        assert assigned in result
        assert other not in result

    def test_assistant_sees_only_own_or_assigned(self, facility, assistant_user, lead_user):
        own = _make_workitem(facility, assistant_user, title="own")
        other = _make_workitem(facility, lead_user, title="other")
        result = list(_collect_open_tasks(facility, assistant_user))
        assert own in result
        assert other not in result

    def test_priority_order_urgent_first(self, facility, lead_user):
        """Boundary: priority_rank ``urgent=0 < important=1 < normal=2``.

        Tests in umgekehrter Erstellungsreihenfolge, damit ``-created_at``
        die Reihenfolge nicht zufaellig richtig stellt.
        """
        normal = _make_workitem(facility, lead_user, title="n", priority=WorkItem.Priority.NORMAL)
        important = _make_workitem(facility, lead_user, title="i", priority=WorkItem.Priority.IMPORTANT)
        urgent = _make_workitem(facility, lead_user, title="u", priority=WorkItem.Priority.URGENT)
        result = list(_collect_open_tasks(facility, lead_user))
        assert result == [urgent, important, normal]

    def test_due_date_tiebreak_within_same_priority(self, facility, lead_user):
        """Boundary: zweite Sort-Spalte ``due_date`` (ASC) bricht Priority-Gleichstand."""
        today = timezone.now().date()
        later = _make_workitem(
            facility, lead_user, title="later", priority=WorkItem.Priority.NORMAL, due_date=today + timedelta(days=10)
        )
        sooner = _make_workitem(
            facility, lead_user, title="sooner", priority=WorkItem.Priority.NORMAL, due_date=today + timedelta(days=1)
        )
        result = list(_collect_open_tasks(facility, lead_user))
        assert result.index(sooner) < result.index(later)

    def test_cap_at_10(self, facility, lead_user):
        """Boundary: ``[:10]``-Slice."""
        for i in range(12):
            _make_workitem(facility, lead_user, title=f"t{i}", priority=WorkItem.Priority.URGENT)
        result = list(_collect_open_tasks(facility, lead_user))
        assert len(result) == 10

    def test_none_user_skips_visibility_filter(self, facility, staff_user):
        """``user is None``-Branch: kein Filter, alle Tasks sichtbar (z.B. fuer
        System-Aggregatoren). Boundary: ``if user is not None`` vs ``is None``.
        """
        wi = _make_workitem(facility, staff_user)
        result = list(_collect_open_tasks(facility, None))
        assert wi in result
