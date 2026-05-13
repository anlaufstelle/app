"""Service for building structured handover summaries."""

from django.db.models import Case as DBCase
from django.db.models import Count, IntegerField, Value, When

from core.models import Activity, Client, Event, WorkItem
from core.services.feed import enrich_events_with_preview, get_time_range


def _build_shift_metadata(time_filter, start_dt, end_dt):
    """Return (shift_label, shift_range) strings for a shift window."""
    if time_filter:
        shift_label = time_filter.label
        shift_range = f"{start_dt.strftime('%H:%M')} – {end_dt.strftime('%H:%M')}"
    else:
        shift_label = "Ganzer Tag"
        shift_range = "00:00 – 23:59"
    return shift_label, shift_range


def _collect_stats(facility, visible_events, time_range):
    """Aggregate counters for the shift (events, activities, workitems, bans, clients)."""
    events_total = visible_events.count()

    events_by_type = list(
        visible_events.values("document_type__name", "document_type__color")
        .annotate(count=Count("id"))
        .order_by("-count")
    )

    activities_total = Activity.objects.filter(
        facility=facility,
        occurred_at__range=time_range,
    ).count()

    workitems_new = WorkItem.objects.filter(
        facility=facility,
        created_at__range=time_range,
    ).count()

    workitems_completed = WorkItem.objects.filter(
        facility=facility,
        status="done",
        updated_at__range=time_range,
    ).count()

    bans_new = visible_events.filter(document_type__system_type="ban").count()

    clients_new = Client.objects.filter(
        facility=facility,
        created_at__range=time_range,
    ).count()

    return {
        "events_total": events_total,
        "events_by_type": events_by_type,
        "activities_total": activities_total,
        "workitems_new": workitems_new,
        "workitems_completed": workitems_completed,
        "bans_new": bans_new,
        "clients_new": clients_new,
    }


def _collect_highlights(facility, visible_events, time_range, user):
    """Combine crisis/ban events and urgent tasks into a sorted highlights list."""
    crisis_events = (
        visible_events.filter(document_type__system_type="crisis")
        .select_related("document_type", "client", "created_by")
        .order_by("-occurred_at")[:10]
    )

    ban_events = (
        visible_events.filter(document_type__system_type="ban")
        .select_related("document_type", "client", "created_by")
        .order_by("-occurred_at")[:10]
    )

    urgent_tasks = (
        WorkItem.objects.filter(
            facility=facility,
            priority__in=["urgent", "important"],
            created_at__range=time_range,
        )
        .select_related("client", "assigned_to")
        .order_by("-created_at")[:10]
    )

    highlights = []
    for e in crisis_events:
        highlights.append({"type": "crisis", "time": e.occurred_at, "object": e})
    for e in ban_events:
        highlights.append({"type": "ban", "time": e.occurred_at, "object": e})
    for wi in urgent_tasks:
        highlights.append({"type": "task", "time": wi.created_at, "object": wi})
    highlights.sort(key=lambda h: h["time"], reverse=True)

    # Enrich event highlights with preview fields
    event_highlights = [
        {"type": h["type"], "occurred_at": h["time"], "object": h["object"]}
        for h in highlights
        if h["type"] in ("crisis", "ban")
    ]
    if event_highlights:
        enrich_events_with_preview(event_highlights, user)

    return highlights


def _collect_open_tasks(facility, user):
    """Return up to 10 open/in-progress work items ordered by priority and due date.

    Refs #734: ``user``-Parameter erzwingt Sichtbarkeitsprueung analog
    ``can_user_mutate_workitem`` — Lead/Admin sehen alle WorkItems der
    Facility, Staff/Assistant nur eigene + zugewiesene. Verhindert, dass
    der Handover-Summary Pseudonyme von WorkItems anzeigt, die der
    aktuelle User nicht oeffnen darf.
    """
    from django.db.models import Q

    from core.models import User as UserModel

    priority_order = DBCase(
        When(priority="urgent", then=Value(0)),
        When(priority="important", then=Value(1)),
        When(priority="normal", then=Value(2)),
        default=Value(2),
        output_field=IntegerField(),
    )
    qs = WorkItem.objects.filter(
        facility=facility,
        status__in=["open", "in_progress"],
    )
    # Lead/Admin sehen alles in der Facility, Staff/Assistant filtern auf
    # owner/assignee — analog can_user_mutate_workitem().
    if user is not None and getattr(user, "role", None) not in (UserModel.Role.LEAD, UserModel.Role.FACILITY_ADMIN):
        qs = qs.filter(Q(created_by=user) | Q(assigned_to=user))
    return (
        qs.annotate(priority_rank=priority_order)
        .select_related("client", "assigned_to")
        .order_by("priority_rank", "due_date", "-created_at")[:10]
    )


def build_handover_summary(facility, target_date, time_filter, user):
    """Build structured handover summary for a shift.

    Args:
        facility: Facility instance
        target_date: date object
        time_filter: TimeFilter instance (or None for full day)
        user: User instance (for sensitivity checks)

    Returns dict with keys: shift_label, shift_range, date, stats, highlights, open_tasks
    """
    start_dt, end_dt = get_time_range(target_date, time_filter)
    shift_label, shift_range = _build_shift_metadata(time_filter, start_dt, end_dt)

    time_range = (start_dt, end_dt)
    visible_events = Event.objects.visible_to(user).filter(
        facility=facility,
        is_deleted=False,
        occurred_at__range=time_range,
    )

    stats = _collect_stats(facility, visible_events, time_range)
    highlights = _collect_highlights(facility, visible_events, time_range, user)
    open_tasks = _collect_open_tasks(facility, user)

    return {
        "shift_label": shift_label,
        "shift_range": shift_range,
        "date": target_date,
        "stats": stats,
        "highlights": highlights,
        "open_tasks": open_tasks,
    }
