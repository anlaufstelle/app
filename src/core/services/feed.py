"""Service for building unified activity feeds."""

from collections import defaultdict
from datetime import datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from core.models import Activity, DocumentTypeField, Event, WorkItem
from core.services.encryption import safe_decrypt
from core.services.sensitivity import user_can_see_field


def get_time_range(target_date, time_filter=None):
    """Return (start_dt, end_dt) for a given date, optionally scoped by a TimeFilter.

    Without time_filter: full day (00:00:00 to 23:59:59.999999).
    With time_filter:
      - Normal range (start <= end): same day from start_time to end_time.
      - Midnight overlap (start > end, e.g. 22:00-08:00): start on target_date,
        end on target_date + 1 day.
    """
    if time_filter is not None:
        start_dt = timezone.make_aware(datetime.combine(target_date, time_filter.start_time))
        if time_filter.start_time <= time_filter.end_time:
            end_dt = timezone.make_aware(datetime.combine(target_date, time_filter.end_time))
        else:
            end_dt = timezone.make_aware(datetime.combine(target_date + timedelta(days=1), time_filter.end_time))
        return start_dt, end_dt

    start_dt = timezone.make_aware(datetime.combine(target_date, time.min))
    end_dt = timezone.make_aware(datetime.combine(target_date, time.max))
    return start_dt, end_dt


def build_feed_items(facility, target_date, feed_type, time_filter=None, user=None):
    """Build a unified feed of events, activities, work items, and bans for a given date.

    *user* is required for sensitivity-based event visibility — without it the
    feed includes events whose existence the caller is not allowed to know
    about (#522). Callers must always pass ``request.user``.
    """
    start_dt, end_dt = get_time_range(target_date, time_filter)

    events_list = []
    activities_list = []
    workitems_list = []
    bans_list = []

    include_all = feed_type == "" or feed_type == "all"

    # Events (exclude bans when loading all types to avoid duplicates with bans feed)
    if feed_type == "events" or include_all:
        events_qs = Event.objects.visible_to(user).filter(
            facility=facility,
            is_deleted=False,
            occurred_at__gte=start_dt,
            occurred_at__lte=end_dt,
        )
        if include_all:
            events_qs = events_qs.exclude(document_type__system_type="ban")
        events = events_qs.select_related("document_type", "client", "created_by").order_by("-occurred_at")[:200]
        events_list = [{"type": "event", "occurred_at": e.occurred_at, "object": e} for e in events]

    # Activities
    if feed_type == "activities" or include_all:
        activities_qs = Activity.objects.filter(
            facility=facility,
            occurred_at__gte=start_dt,
            occurred_at__lte=end_dt,
        )
        # In mixed feed: exclude 'created' activities (redundant with first-class object cards)
        if include_all:
            activities_qs = activities_qs.exclude(verb=Activity.Verb.CREATED)

        # Filter out activities whose target is an Event the user may not see (#562)
        if user is not None:
            from django.contrib.contenttypes.models import ContentType

            event_ct = ContentType.objects.get_for_model(Event)
            visible_event_ids = Event.objects.visible_to(user).values_list("pk", flat=True)
            activities_qs = activities_qs.exclude(
                Q(target_type=event_ct) & ~Q(target_id__in=visible_event_ids),
            )

        activities = activities_qs.select_related("actor", "target_type").order_by("-occurred_at")[:200]
        activities_list = [{"type": "activity", "occurred_at": a.occurred_at, "object": a} for a in activities]

    # Work items
    if feed_type == "workitems" or include_all:
        workitems = (
            WorkItem.objects.filter(
                facility=facility,
                created_at__gte=start_dt,
                created_at__lte=end_dt,
            )
            .select_related("client", "assigned_to")
            .order_by("-created_at")[:200]
        )
        workitems_list = [{"type": "workitem", "occurred_at": wi.created_at, "object": wi} for wi in workitems]

    # Bans
    if feed_type == "bans" or include_all:
        bans = (
            Event.objects.visible_to(user)
            .filter(
                facility=facility,
                is_deleted=False,
                document_type__system_type="ban",
                occurred_at__gte=start_dt,
                occurred_at__lte=end_dt,
            )
            .select_related("document_type", "client", "created_by")
            .order_by("-occurred_at")[:200]
        )
        bans_list = [{"type": "ban", "occurred_at": e.occurred_at, "object": e} for e in bans]

    return sorted(
        events_list + activities_list + workitems_list + bans_list,
        key=lambda x: x["occurred_at"],
        reverse=True,
    )[:200]


def _format_preview_value(value, ft):
    """Format a data_json value for preview display."""
    if ft.field_type == "boolean":
        return "Ja" if value else "Nein"

    if ft.field_type == "select" and ft.options_json:
        label_map = {o["slug"]: o["label"] for o in ft.options_json if isinstance(o, dict)}
        return label_map.get(value, str(value))

    if ft.field_type == "multi_select" and ft.options_json and isinstance(value, list):
        label_map = {o["slug"]: o["label"] for o in ft.options_json if isinstance(o, dict)}
        return ", ".join(label_map.get(v, str(v)) for v in value)

    if isinstance(value, dict):
        # File-Marker (Stufe A: einzelnes Attachment, Stufe B: Liste mit Versionen)
        # treten in `data_json` fuer File-Felder auf (siehe services/event.py:250-267).
        # Sie wuerden sonst als rohes Dict-Repr im Preview landen — Privacy-Leak
        # (UUIDs sichtbar) und UX-Bug (Refs #670 FND-12).
        if value.get("__file__"):
            return _("[Datei]")
        if value.get("__files__"):
            count = len(value.get("entries") or [])
            return ngettext("[%(n)d Datei]", "[%(n)d Dateien]", count) % {"n": count}
        return safe_decrypt(value, default="[verschlüsselt]")

    return str(value)


def enrich_events_with_preview(feed_items, user):
    """Attach preview_fields to each event/ban in feed_items."""
    dt_ids = {item["object"].document_type_id for item in feed_items if item["type"] in ("event", "ban")}
    if not dt_ids:
        return

    dtf_qs = (
        DocumentTypeField.objects.filter(document_type_id__in=dt_ids)
        .select_related("field_template")
        .order_by("document_type_id", "sort_order")
    )
    dt_fields = defaultdict(list)
    for dtf in dtf_qs:
        dt_fields[dtf.document_type_id].append(dtf.field_template)

    for item in feed_items:
        if item["type"] not in ("event", "ban"):
            continue

        event = item["object"]
        doc_sensitivity = event.document_type.sensitivity
        data = event.data_json or {}
        preview_fields = []
        expanded_fields = []

        for ft in dt_fields.get(event.document_type_id, []):
            if not user_can_see_field(user, doc_sensitivity, ft.sensitivity):
                continue
            value = data.get(ft.slug)
            if value is None or value == "":
                continue
            if isinstance(value, list) and len(value) == 0:
                continue
            if ft.field_type == "boolean" and not value:
                continue
            formatted = _format_preview_value(value, ft)
            entry = {"label": ft.name, "value": formatted, "is_textarea": ft.field_type == "textarea"}
            expanded_fields.append(entry)
            # Preview bleibt kompakt (max 3 Felder, keine textareas);
            # Expanded zeigt alles inkl. textarea, damit Notizen ohne
            # Detail-Klick lesbar sind (Refs #707).
            if ft.field_type != "textarea" and len(preview_fields) < 3:
                preview_fields.append(entry)

        event.preview_fields = preview_fields
        event.expanded_fields = expanded_fields
