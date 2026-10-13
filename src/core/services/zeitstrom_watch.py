"""User-visible revision projections for the Zeitstrom live watcher."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from django.db.models import Case as DBCase
from django.db.models import Count, IntegerField, Q, Value, When
from django.db.models.functions import Coalesce
from django.utils.crypto import constant_time_compare, salted_hmac

from core.constants import FEED_MAX_PER_TYPE
from core.models import Activity, Client, DocumentTypeField, Event, WorkItem
from core.services.compliance import user_can_see_field
from core.services.events import build_tally_summary
from core.services.workitem_visibility import workitems_for_read
from core.services.zeitstrom_filters import ZeitstromState

WATCH_COMPONENTS = (
    "feed",
    "tally_counts",
    "tally_topology",
    "current_shift",
    "focus_box",
    "bans",
    "handover_note",
)
_COMPONENT_STATE_KEYS = {
    "feed": ("date", "time_filter", "type", "doc_type", "view"),
    "tally_counts": ("date", "time_filter", "view"),
    "tally_topology": ("date", "time_filter", "view"),
    "current_shift": (),
    "focus_box": (),
    # Das Banner zeigt immer die JETZT aktiven Hausverbote — unabhängig von
    # Datum/Filter (Refs #1917).
    "bans": (),
    "handover_note": ("date", "time_filter", "view"),
}


@dataclass(frozen=True)
class ZeitstromWatchSnapshot:
    etag: str
    changed_components: tuple[str, ...]
    changed_validators: dict[str, str]


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def build_zeitstrom_page_scope(user, facility) -> str:
    """Opaque binding for the authorization scope of one rendered page."""

    from core.services.compliance import allowed_sensitivities_for_user

    source = {
        "user": str(user.pk),
        "facility": str(facility.pk),
        "role": user.role,
        "active": user.is_active,
        "sensitivities": sorted(allowed_sensitivities_for_user(user)),
    }
    return salted_hmac("core.zeitstrom.watch.scope.v1", _json(source), algorithm="sha256").hexdigest()


def zeitstrom_page_scope_matches(candidate: str, user, facility) -> bool:
    """Validate a page scope without exposing or timing-comparing identifiers."""

    if not isinstance(candidate, str) or len(candidate) != 64:
        # Keep malformed input on the same constant-time comparison path.
        candidate = "0" * 64
    return constant_time_compare(candidate, build_zeitstrom_page_scope(user, facility))


def _opaque_validator(*, user, facility, state: ZeitstromState, component: str, projection) -> str:
    component_state = {key: state.params[key] for key in _COMPONENT_STATE_KEYS[component]}
    source = {
        "user": str(user.pk),
        "facility": str(facility.pk),
        "state": component_state,
        "component": component,
        "projection": projection,
    }
    digest = salted_hmac("core.zeitstrom.watch.component.v1", _json(source), algorithm="sha256").hexdigest()
    return f'"{digest}"'


def _page_etag(*, user, facility, state: ZeitstromState, validators: dict[str, str]) -> str:
    source = {
        "user": str(user.pk),
        "facility": str(facility.pk),
        "state": state.params,
        "components": validators,
    }
    digest = salted_hmac("core.zeitstrom.watch.page.v1", _json(source), algorithm="sha256").hexdigest()
    return f'"{digest}"'


_EVENT_VALUES = (
    "id",
    "occurred_at",
    "document_type_id",
    "document_type__name",
    "document_type__sensitivity",
    "is_anonymous",
    "client_id",
    "client__pseudonym",
    "created_by_id",
    "created_by__username",
    "created_by__display_name",
    "created_by__first_name",
    "created_by__last_name",
    "data_json",
)

_WORKITEM_VALUES = (
    "id",
    "created_at",
    "updated_at",
    "completed_at",
    "title",
    "description",
    "item_type",
    "status",
    "priority",
    "due_date",
    "remind_at",
    "recurrence",
    "is_deleted",
    "client_id",
    "client__pseudonym",
    "assigned_to_id",
    "assigned_to__username",
    "assigned_to__display_name",
    "assigned_to__first_name",
    "assigned_to__last_name",
)


def _related_user(row, prefix: str):
    user_id = row.get(f"{prefix}_id")
    if user_id is None:
        return None
    return (
        str(user_id),
        row.get(f"{prefix}__username"),
        row.get(f"{prefix}__display_name"),
        row.get(f"{prefix}__first_name"),
        row.get(f"{prefix}__last_name"),
    )


def _visible_field_configs(event_rows, user):
    """One bounded metadata query, grouped by visible DocumentType."""

    doc_sensitivities = {row["document_type_id"]: row["document_type__sensitivity"] for row in event_rows}
    if not doc_sensitivities:
        return {}
    assignments = (
        DocumentTypeField.objects.filter(document_type_id__in=doc_sensitivities)
        .annotate(
            block_pos=Coalesce("field_group__sort_order", "sort_order"),
            block_is_free=DBCase(
                When(field_group__isnull=True, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            ),
        )
        .values(
            "document_type_id",
            "field_template__slug",
            "field_template__name",
            "field_template__field_type",
            "field_template__sensitivity",
            "field_template__options_json",
            "block_pos",
            "block_is_free",
            "sort_order",
            "id",
        )
        .order_by("document_type_id", "block_pos", "block_is_free", "sort_order", "id")
    )
    result: dict = {doc_type_id: [] for doc_type_id in doc_sensitivities}
    for assignment in assignments:
        doc_type_id = assignment["document_type_id"]
        if not user_can_see_field(
            user,
            doc_sensitivities[doc_type_id],
            assignment["field_template__sensitivity"],
        ):
            continue
        result[doc_type_id].append(
            (
                assignment["field_template__slug"],
                assignment["field_template__name"],
                assignment["field_template__field_type"],
                assignment["field_template__options_json"],
            )
        )
    return result


def _dynamic_projection(row, field_configs):
    data = row["data_json"] or {}
    visible = []
    for slug, name, field_type, options in field_configs.get(row["document_type_id"], ()):
        value = data.get(slug)
        if value is None or value == "" or (isinstance(value, list) and not value):
            continue
        if field_type == "boolean" and not value:
            continue
        # Raw encrypted values are safe inside the server-side HMAC source.
        # Crucially, hidden slugs never enter this tuple, so changing a HIGH
        # field cannot alter a LOW user's validator.
        visible.append((slug, name, field_type, options, value))
    return tuple(visible)


def _event_projection(row, field_configs):
    return (
        str(row["id"]),
        row["occurred_at"],
        row["document_type__name"],
        row["is_anonymous"],
        (str(row["client_id"]), row["client__pseudonym"]) if row["client_id"] else None,
        _related_user(row, "created_by"),
        _dynamic_projection(row, field_configs),
    )


def _ban_card_projection(row):
    return (
        str(row["id"]),
        row["occurred_at"],
        (str(row["client_id"]), row["client__pseudonym"]) if row["client_id"] else None,
        _related_user(row, "created_by"),
    )


def _workitem_projection(row):
    return tuple(row.get(name) for name in _WORKITEM_VALUES)


def _feed_projection(facility, user, state: ZeitstromState):
    """Bounded values-only mirror of ``build_feed_items``."""

    from core.services.events.feed import activities_for_feed_read, events_for_feed_read, get_time_range

    start_dt, end_dt = get_time_range(state.target_date, state.time_filter)
    include_all = state.feed_type == ""
    items: list[dict] = []

    if state.feed_type == "events" or include_all:
        events = events_for_feed_read(facility, user).filter(
            occurred_at__gte=start_dt,
            occurred_at__lte=end_dt,
        )
        if include_all:
            events = events.exclude(document_type__system_type="ban")
        rows = list(events.values(*_EVENT_VALUES).order_by("-occurred_at")[:FEED_MAX_PER_TYPE])
        if state.doc_type_id:
            rows = [row for row in rows if str(row["document_type_id"]) == state.doc_type_id]
        items.extend({"type": "event", "time": row["occurred_at"], "row": row} for row in rows)

    if state.feed_type == "activities" or include_all:
        from core.services.dashboard.activity import activity_target_prefetch, render_activity_summary

        activities = activities_for_feed_read(facility, user).filter(
            occurred_at__gte=start_dt,
            occurred_at__lte=end_dt,
        )
        if include_all:
            activities = activities.exclude(verb=Activity.Verb.CREATED)
        activity_rows = (
            activities.select_related("actor", "target_type")
            .prefetch_related(activity_target_prefetch())
            .order_by("-occurred_at")[:FEED_MAX_PER_TYPE]
        )
        rows = [
            {
                "id": activity.id,
                "occurred_at": activity.occurred_at,
                "verb": activity.verb,
                "summary": render_activity_summary(activity),
                "actor_id": activity.actor_id,
                "actor__username": activity.actor.username if activity.actor else None,
                "actor__display_name": activity.actor.display_name if activity.actor else None,
                "actor__first_name": activity.actor.first_name if activity.actor else None,
                "actor__last_name": activity.actor.last_name if activity.actor else None,
                "target_type_id": activity.target_type_id,
                "target_type__model": activity.target_type.model,
                "target_id": activity.target_id,
            }
            for activity in activity_rows
        ]
        items.extend({"type": "activity", "time": row["occurred_at"], "row": row} for row in rows)

    if state.feed_type == "workitems" or include_all:
        rows = (
            workitems_for_read(facility)
            .filter(created_at__gte=start_dt, created_at__lte=end_dt)
            .values(*_WORKITEM_VALUES)
            .order_by("-created_at")[:FEED_MAX_PER_TYPE]
        )
        items.extend({"type": "workitem", "time": row["created_at"], "row": row} for row in rows)

    if state.feed_type == "bans" or include_all:
        rows = (
            events_for_feed_read(facility, user)
            .filter(
                document_type__system_type="ban",
                occurred_at__gte=start_dt,
                occurred_at__lte=end_dt,
            )
            .values(*_EVENT_VALUES)
            .order_by("-occurred_at")[:FEED_MAX_PER_TYPE]
        )
        items.extend({"type": "ban", "time": row["occurred_at"], "row": row} for row in rows)

    items.sort(key=lambda item: item["time"], reverse=True)
    items = items[:FEED_MAX_PER_TYPE]
    event_rows = [item["row"] for item in items if item["type"] == "event"]
    field_configs = _visible_field_configs(event_rows, user)

    projection = []
    for item in items:
        row = item["row"]
        if item["type"] == "event":
            value = _event_projection(row, field_configs)
        elif item["type"] == "ban":
            value = _ban_card_projection(row)
        elif item["type"] == "activity":
            value = (
                str(row["id"]),
                row["occurred_at"],
                row["verb"],
                row["summary"],
                _related_user(row, "actor"),
                row["target_type_id"],
                row["target_type__model"],
                str(row["target_id"]),
            )
        else:
            value = _workitem_projection(row)
        projection.append((item["type"], value))
    return tuple(projection)


def _tally_counts_projection(facility, user, state: ZeitstromState, *, summary=None):
    if not state.is_live_tally_context:
        return ("hidden",)
    summary = summary or build_tally_summary(facility, user, context=state.live_context)
    return tuple((str(row["document_type"].pk), row["count"]) for row in summary["rows"])


def _tally_topology_projection(facility, user, state: ZeitstromState, *, summary=None):
    if not state.is_live_tally_context:
        return ("hidden",)
    summary = summary or build_tally_summary(facility, user, context=state.live_context)
    rows = []
    for row in summary["rows"]:
        document_type = row["document_type"]
        required_fields = []
        for assignment in getattr(document_type, "_tally_policy_assignments", ()):
            field = assignment.field_template
            if not field.is_required:
                continue
            required_fields.append(
                (
                    str(assignment.pk),
                    assignment.sort_order,
                    str(field.pk),
                    field.name,
                    field.slug,
                    field.field_type,
                    field.min_value,
                    field.max_value,
                    field.sensitivity,
                    field.is_active,
                )
            )
        rows.append(
            (
                str(document_type.pk),
                document_type.name,
                row["requires_number_input"],
                tuple(required_fields),
            )
        )
    return (
        str(summary["time_filter"].pk) if summary["time_filter"] else None,
        summary["time_filter"].label if summary["time_filter"] else None,
        summary["shift_date"],
        summary["window_start"],
        summary["window_end"],
        tuple(rows),
    )


def build_current_shift_summary(facility, user, state: ZeitstromState):
    """Renderer-only helper used by full page and current-shift partial."""

    from core.services.case import build_handover_summary

    if state.live_context.time_filter is None:
        return None
    return build_handover_summary(
        facility,
        state.live_context.shift_date,
        state.live_context.time_filter,
        user,
    )


def _current_shift_projection(facility, user, state: ZeitstromState):
    """Values/aggregates only; never calls the handover renderer."""

    live_filter = state.live_context.time_filter
    if live_filter is None:
        return None
    start_dt = state.live_context.window_start
    end_dt = state.live_context.window_end
    visible_events = (
        Event.objects.visible_to(user)
        .exclude(client__is_deleted=True)
        .exclude(client__is_active=False)
        .filter(
            facility=facility,
            is_deleted=False,
            occurred_at__range=(start_dt, end_dt),
        )
    )
    events_by_type = list(
        visible_events.values("document_type__name", "document_type__color")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    stats = {
        "events_total": sum(item["count"] for item in events_by_type),
        "events_by_type": events_by_type,
        "workitems_new": workitems_for_read(facility).filter(created_at__range=(start_dt, end_dt)).count(),
        "clients_new": Client.objects.for_facility(facility)
        .filter(is_deleted=False, is_active=True, created_at__range=(start_dt, end_dt))
        .count(),
    }

    highlights: list[dict[str, Any]] = []
    for highlight_type, system_type in (("crisis", "crisis"), ("ban", "ban")):
        rows = list(
            visible_events.filter(document_type__system_type=system_type)
            .values(*_EVENT_VALUES)
            .order_by("-occurred_at")[:10]
        )
        highlights.extend({"type": highlight_type, "time": row["occurred_at"], "row": row} for row in rows)
    task_rows = list(
        workitems_for_read(facility)
        .filter(
            priority__in=[WorkItem.Priority.URGENT, WorkItem.Priority.IMPORTANT],
            created_at__range=(start_dt, end_dt),
        )
        .values(*_WORKITEM_VALUES)
        .order_by("-created_at")[:10]
    )
    highlights.extend({"type": "task", "time": row["created_at"], "row": row} for row in task_rows)
    highlights.sort(key=lambda item: item["time"], reverse=True)
    event_rows = [item["row"] for item in highlights if item["type"] != "task"]
    field_configs = _visible_field_configs(event_rows, user)
    highlight_projection = []
    for item in highlights:
        row = item["row"]
        value = _workitem_projection(row) if item["type"] == "task" else _event_projection(row, field_configs)
        highlight_projection.append((item["type"], item["time"], value))

    return {
        "shift": (str(live_filter.pk), live_filter.label, live_filter.start_time, live_filter.end_time),
        "date": state.live_context.shift_date,
        "window": (start_dt, end_dt),
        "stats": stats,
        "highlights": tuple(highlight_projection),
    }


def _focus_box_projection(facility):
    # Local import keeps this module's dependency surface projection-focused.
    from django.utils import timezone

    today = timezone.localdate()
    open_statuses = [WorkItem.Status.OPEN, WorkItem.Status.IN_PROGRESS]
    base = workitems_for_read(facility).filter(status__in=open_statuses)
    total_open = base.count()
    eligible = base.filter(
        Q(due_date__lte=today)
        | Q(priority__in=[WorkItem.Priority.URGENT, WorkItem.Priority.IMPORTANT])
        | Q(status=WorkItem.Status.IN_PROGRESS)
    ).annotate(
        priority_order=DBCase(
            When(priority=WorkItem.Priority.URGENT, then=Value(0)),
            When(priority=WorkItem.Priority.IMPORTANT, then=Value(1)),
            default=Value(2),
            output_field=IntegerField(),
        ),
        due_date_bucket=DBCase(
            When(due_date__lt=today, then=Value(0)),
            When(due_date=today, then=Value(1)),
            When(due_date__gt=today, then=Value(2)),
            default=Value(9),
            output_field=IntegerField(),
        ),
    )
    rows = list(
        eligible.values(*_WORKITEM_VALUES).order_by("due_date_bucket", "due_date", "priority_order", "-created_at")[:8]
    )
    groups = {"overdue": [], "today": [], "priority": [], "in_progress": []}
    for row in rows:
        if row["due_date"] is not None and row["due_date"] < today:
            key = "overdue"
        elif row["due_date"] == today:
            key = "today"
        elif row["priority"] in (WorkItem.Priority.URGENT, WorkItem.Priority.IMPORTANT):
            key = "priority"
        else:
            key = "in_progress"
        groups[key].append(_workitem_projection(row))
    ordered = tuple((key, tuple(groups[key])) for key in ("overdue", "today", "priority", "in_progress") if groups[key])
    return (today, ordered, len(rows), total_open, total_open > len(rows))


def _bans_projection(facility, user):
    """Refs #1917: opake Hausverbots-Revision — eine Single-Row-SELECT.

    Bewusst KEIN Zugriff auf ``core_event`` und KEINE Entschlüsselung im
    Poll-Pfad; die Projektion enthält nur das rollen-sichtbare
    Revisions-Präfix (Seitenkanal-Argument in
    :mod:`core.services.system.ban_revision`). Das autoritative Banner
    rendert erst das Follow-up-Partial.
    """
    from core.services.system.ban_revision import ban_revision_projection

    return ban_revision_projection(facility, user)


def _handover_note_projection(facility, user, state: ZeitstromState):
    """Rendered note state; only its opaque HMAC leaves the watch endpoint."""

    from core.services.case.handover_note import Shift, build_handover_note_context

    displayed_shift = Shift(state.target_date, state.time_filter)
    context = build_handover_note_context(facility, user, displayed_shift)
    if context is None:
        return None
    return {
        "can_write": context["can_write"],
        "blocks": tuple(
            (
                block.kind,
                block.shift.shift_date,
                str(block.shift.time_filter_id or ""),
                block.label,
                block.range_label,
                block.text,
                block.author,
                block.updated_at,
                block.editable,
                block.editable_until,
            )
            for block in context["blocks"]
        ),
    }


def build_zeitstrom_component_validator(
    user,
    facility,
    state: ZeitstromState,
    component: str,
    *,
    tally_summary=None,
) -> str:
    """Return the validator for the state rendered by one follow-up."""

    projections = {
        "feed": lambda: _feed_projection(facility, user, state),
        "tally_counts": lambda: _tally_counts_projection(facility, user, state, summary=tally_summary),
        "tally_topology": lambda: _tally_topology_projection(facility, user, state, summary=tally_summary),
        "current_shift": lambda: _current_shift_projection(facility, user, state),
        "focus_box": lambda: _focus_box_projection(facility),
        "bans": lambda: _bans_projection(facility, user),
        "handover_note": lambda: _handover_note_projection(facility, user, state),
    }
    if component not in projections:
        raise ValueError(f"Unknown Zeitstrom component: {component}")
    return _opaque_validator(
        user=user,
        facility=facility,
        state=state,
        component=component,
        projection=projections[component](),
    )


def build_zeitstrom_tally_topology_validator(user, facility, state, *, tally_summary=None) -> str:
    return build_zeitstrom_component_validator(
        user,
        facility,
        state,
        "tally_topology",
        tally_summary=tally_summary,
    )


def build_zeitstrom_watch_snapshot(
    user,
    facility,
    state: ZeitstromState,
    *,
    previous_validators: dict[str, str] | None = None,
) -> ZeitstromWatchSnapshot:
    """Compute fixed-query, user-visible component validators."""

    if state.is_handover:
        from core.services.case.handover_note import can_read_notes

        component_names: tuple[str, ...] = ("handover_note",) if can_read_notes(user) else ()
    else:
        component_names = tuple(
            name
            for name in WATCH_COMPONENTS
            if name != "handover_note" and (name != "tally_counts" or state.is_live_tally_context)
        )

    tally_summary = None
    if state.is_live_tally_context and {"tally_counts", "tally_topology"}.intersection(component_names):
        tally_summary = build_tally_summary(facility, user, context=state.live_context)
    validators = {
        name: build_zeitstrom_component_validator(
            user,
            facility,
            state,
            name,
            tally_summary=tally_summary,
        )
        for name in component_names
    }
    previous_validators = previous_validators or {}
    changed = tuple(name for name in component_names if previous_validators.get(name) != validators[name])
    return ZeitstromWatchSnapshot(
        etag=_page_etag(user=user, facility=facility, state=state, validators=validators),
        changed_components=changed,
        changed_validators={name: validators[name] for name in changed},
    )
