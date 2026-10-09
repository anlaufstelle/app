"""Validated metadata filters for the facility contacts list."""

from datetime import date, timedelta
from uuid import UUID

from django.db.models import BooleanField, Max, Min
from django.db.models.expressions import RawSQL
from django.utils import timezone
from psycopg.types.multirange import Multirange
from psycopg.types.range import Range

from core.models import Client, DocumentType, Event, TimeFilter, User
from core.services.case import cases_with_live_client
from core.services.compliance import allowed_sensitivities_for_user
from core.services.dashboard.search import MIN_QUERY_LENGTH, event_search_queryset
from core.services.events.feed import get_time_range


def event_list_options(facility, user):
    """Return tenant-scoped filter choices without document contents."""
    return {
        "document_types": DocumentType.objects.for_facility(facility)
        .filter(entity_type=DocumentType.EntityType.EVENT, sensitivity__in=allowed_sensitivities_for_user(user))
        .only("id", "name"),
        "clients": Client.objects.for_facility(facility)
        .filter(is_deleted=False, is_active=True)
        .only("id", "pseudonym"),
        "cases": cases_with_live_client().for_facility(facility).only("id", "title") if user.is_staff_or_above else [],
        "creators": User.objects.filter(facility=facility).only(
            "id", "username", "display_name", "first_name", "last_name"
        ),
        "time_filters": TimeFilter.objects.for_facility(facility),
    }


def _date_value(value):
    try:
        parsed = date.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    # Leave room for overnight windows and timezone conversion at both ends.
    return parsed if 1 < parsed.year < 9999 else None


def resolve_event_list_filters(params, facility, user, *, today=None, options=None):
    """Normalize invalid, missing and inaccessible IDs identically."""
    today = today or timezone.localdate()
    options = options if options is not None else event_list_options(facility, user)
    state = {}
    start, end = _date_value(params.get("date_from")), _date_value(params.get("date_to"))
    preset = params.get("range")
    if preset == "this_week":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        state["range"] = preset
    elif preset == "last_month":
        end = today.replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
        state["range"] = preset
    if start and end and start > end:
        start = end = None
    if start:
        state["date_from"] = start.isoformat()
    if end:
        state["date_to"] = end.isoformat()
    choices = {
        "doc_type": "document_types",
        "client": "clients",
        "created_by": "creators",
        "time_filter": "time_filters",
    }
    if user.is_staff_or_above:
        choices["case"] = "cases"
    for key, option_key in choices.items():
        try:
            identifier = int(params.get(key, "")) if key == "created_by" else UUID(params.get(key, ""))
        except (ValueError, TypeError, AttributeError):
            continue
        if any(item.pk == identifier for item in options[option_key]):
            state[key] = str(identifier)
    if params.get("mine") == "1":
        state["mine"] = "1"
    if params.get("anonymous") in ("yes", "no"):
        state["anonymous"] = params["anonymous"]
    query = params.get("q", "").strip()
    if query:
        state["q"] = query
    return state


def _apply_shift(queryset, shift, start, end):
    """Use feed windows, including DST and overnight start-day semantics.

    PostgreSQL multiranges avoid a growing OR expression for long periods.
    Only days within the remaining events' bounds need windows; this also
    avoids allocating ranges for arbitrary empty periods supplied in GET.
    """
    bounds = queryset.aggregate(first=Min("occurred_at"), last=Max("occurred_at"))
    if bounds["first"] is None:
        return queryset.none()
    first = timezone.localdate(bounds["first"])
    if shift.start_time > shift.end_time:
        first -= timedelta(days=1)
    last = timezone.localdate(bounds["last"])
    first = max(first, start) if start else first
    last = min(last, end) if end else last
    windows = []
    while first <= last:
        window_start, window_end = get_time_range(first, shift)
        windows.append(Range(window_start, window_end, "[]"))
        first += timedelta(days=1)
    if not windows:
        return queryset.none()
    return queryset.filter(
        RawSQL('"core_event"."occurred_at" <@ %s::tstzmultirange', (Multirange(windows),), output_field=BooleanField())
    )


def event_list_queryset(facility, user, filters, *, options=None):
    """Load list columns only, reusing the feed visibility and global search."""
    queryset = Event.objects.for_facility(facility).visible_to(user).filter(is_deleted=False)
    query = filters.get("q", "")
    if query:
        if len(query) < MIN_QUERY_LENGTH:
            queryset = queryset.none()
        else:
            queryset = queryset.filter(pk__in=event_search_queryset(facility, user, query).values("pk"))
    for key, field in {"doc_type": "document_type_id", "client": "client_id", "created_by": "created_by_id"}.items():
        if filters.get(key):
            queryset = queryset.filter(**{field: filters[key]})
    if user.is_staff_or_above and filters.get("case"):
        queryset = queryset.filter(case_id=filters["case"])
    if filters.get("mine"):
        queryset = queryset.filter(created_by=user)
    if filters.get("anonymous"):
        queryset = queryset.filter(is_anonymous=filters["anonymous"] == "yes")
    start, end = _date_value(filters.get("date_from")), _date_value(filters.get("date_to"))
    if filters.get("time_filter"):
        options = options if options is not None else event_list_options(facility, user)
        shift = next((item for item in options["time_filters"] if str(item.pk) == filters["time_filter"]), None)
        if shift is not None:
            queryset = _apply_shift(queryset, shift, start, end)
    else:
        if start:
            queryset = queryset.filter(occurred_at__gte=get_time_range(start)[0])
        if end:
            queryset = queryset.filter(occurred_at__lte=get_time_range(end)[1])
    related = ["document_type", "client", "created_by"]
    columns = [
        "id",
        "occurred_at",
        "is_anonymous",
        "document_type__id",
        "document_type__name",
        "client__id",
        "client__pseudonym",
        "created_by__id",
        "created_by__username",
        "created_by__display_name",
        "created_by__first_name",
        "created_by__last_name",
    ]
    if user.is_staff_or_above:
        related.append("case")
        columns.extend(["case__id", "case__title", "case__is_deleted"])
    return queryset.select_related(*related).only(*columns).order_by("-occurred_at", "-pk")


def event_list_params_from_zeitstrom(state):
    """Carry the resolved feed day and supported metadata filters to the list."""
    day = state.target_date.isoformat()
    params = {"date_from": day, "date_to": day}
    if state.doc_type_id:
        params["doc_type"] = state.doc_type_id
    if state.time_filter is not None:
        params["time_filter"] = state.time_filter_id
    return params
