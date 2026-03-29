"""Service for building structured handover summaries."""

from django.db.models import Case as DBCase
from django.db.models import Count, IntegerField, Value, When

from core.models import Activity, Client, Event, WorkItem
from core.services.feed import enrich_events_with_preview, get_time_range


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

    # Shift metadata
    if time_filter:
        shift_label = time_filter.label
        shift_range = f"{start_dt.strftime('%H:%M')} – {end_dt.strftime('%H:%M')}"
    else:
        shift_label = "Ganzer Tag"
        shift_range = "00:00 – 23:59"

    # --- Stats ---
    time_range = (start_dt, end_dt)

    events_total = Event.objects.filter(
        facility=facility,
        is_deleted=False,
        occurred_at__range=time_range,
    ).count()

    events_by_type = list(
        Event.objects.filter(
            facility=facility,
            is_deleted=False,
            occurred_at__range=time_range,
        )
        .values("document_type__name", "document_type__color")
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

    bans_new = Event.objects.filter(
        facility=facility,
        is_deleted=False,
        document_type__system_type="ban",
        occurred_at__range=time_range,
    ).count()

    clients_new = Client.objects.filter(
        facility=facility,
        created_at__range=time_range,
    ).count()

    stats = {
        "events_total": events_total,
        "events_by_type": events_by_type,
        "activities_total": activities_total,
        "workitems_new": workitems_new,
        "workitems_completed": workitems_completed,
        "bans_new": bans_new,
        "clients_new": clients_new,
    }

    # --- Highlights ---
    crisis_events = (
        Event.objects.filter(
            facility=facility,
            document_type__system_type="crisis",
            is_deleted=False,
            occurred_at__range=time_range,
        )
        .select_related("document_type", "client", "created_by")
        .order_by("-occurred_at")[:10]
    )

    ban_events = (
        Event.objects.filter(
            facility=facility,
            document_type__system_type="ban",
            is_deleted=False,
            occurred_at__range=time_range,
        )
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

    # --- Open tasks (across all time, not just this shift) ---
    priority_order = DBCase(
        When(priority="urgent", then=Value(0)),
        When(priority="important", then=Value(1)),
        When(priority="normal", then=Value(2)),
        default=Value(2),
        output_field=IntegerField(),
    )
    open_tasks = (
        WorkItem.objects.filter(
            facility=facility,
            status__in=["open", "in_progress"],
        )
        .annotate(priority_rank=priority_order)
        .select_related("client", "assigned_to")
        .order_by("priority_rank", "due_date", "-created_at")[:10]
    )

    return {
        "shift_label": shift_label,
        "shift_range": shift_range,
        "date": target_date,
        "stats": stats,
        "highlights": highlights,
        "open_tasks": open_tasks,
    }
