"""Validated metadata filters for the facility contacts list."""

from calendar import monthrange
from datetime import date, timedelta
from urllib.parse import urlencode
from uuid import UUID

from django.db.models import BooleanField, Count, Max, Min
from django.db.models.expressions import RawSQL
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _
from psycopg.types.multirange import Multirange
from psycopg.types.range import Range

from core.models import Client, DocumentType, Event, TimeFilter, User
from core.services.case import cases_with_live_client
from core.services.compliance import allowed_sensitivities_for_user
from core.services.dashboard.search import MIN_QUERY_LENGTH, event_search_queryset
from core.services.events.feed import enrich_events_with_preview, events_for_feed_read, get_time_range
from core.services.workitem_visibility import readable_person_filter


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


PERIOD_PRESETS = ("today", "this_week", "this_month", "last_month")


def _preset_dates(preset, today):
    """Return (start, end) of a known period preset, else None."""
    if preset == "today":
        return today, today
    if preset == "this_week":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    if preset == "this_month":
        return today.replace(day=1), today.replace(day=monthrange(today.year, today.month)[1])
    if preset == "last_month":
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    return None


def resolve_event_list_filters(params, facility, user, *, today=None, options=None):
    """Normalize invalid, missing and inaccessible IDs identically."""
    today = today or timezone.localdate()
    options = options if options is not None else event_list_options(facility, user)
    state = {}
    start, end = _date_value(params.get("date_from")), _date_value(params.get("date_to"))
    if start and end and start > end:
        start = end = None
    preset = params.get("range")
    preset_dates = _preset_dates(preset, today)
    # Preset and explicit dates exclude each other: dates the user gave are never
    # replaced silently. A pair that merely repeats the preset's own days (what
    # the previous form re-sent) keeps following the preset.
    if preset_dates and (not (start or end) or (start, end) == preset_dates):
        start, end = preset_dates
        state["range"] = preset
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
    if params.get("created_via") in Event.CreatedVia.values:
        state["created_via"] = params["created_via"]
    query = params.get("q", "").strip()
    if query:
        state["q"] = query
    return state


def _apply_shift(queryset, shift, start, end):
    """Use feed windows, including DST and overnight start-day semantics.

    PostgreSQL multiranges avoid a growing OR expression for long periods.
    Only days within the remaining events' bounds need windows; this also
    avoids allocating ranges for arbitrary empty periods supplied in GET.
    A btree index cannot serve the multirange operator, so the aggregate and
    the final filter additionally carry plain range bounds.
    """
    bounded = queryset
    # One day of margin on both sides covers overnight windows and DST shifts.
    if start:
        bounded = bounded.filter(occurred_at__gte=get_time_range(start)[0] - timedelta(days=1))
    if end:
        bounded = bounded.filter(occurred_at__lte=get_time_range(end)[1] + timedelta(days=1))
    bounds = bounded.aggregate(first=Min("occurred_at"), last=Max("occurred_at"))
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
        occurred_at__gte=min(window.lower for window in windows),
        occurred_at__lte=max(window.upper for window in windows),
    ).filter(
        RawSQL('"core_event"."occurred_at" <@ %s::tstzmultirange', (Multirange(windows),), output_field=BooleanField())
    )


def event_list_queryset(facility, user, filters, *, options=None):
    """Load list columns only, reusing the feed visibility and global search."""
    queryset = Event.objects.for_facility(facility).visible_to(user).filter(readable_person_filter(), is_deleted=False)
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
    if filters.get("created_via"):
        queryset = queryset.filter(created_via=filters["created_via"])
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


def event_list_day_groups(queryset, page_events):
    """Group page rows by local day, counting the full filtered result for those days."""
    groups = {}
    for event in page_events:
        day = timezone.localdate(event.occurred_at)
        groups.setdefault(day, {"day": day, "events": []})["events"].append(event)
    if not groups:
        return []
    counts = (
        queryset.order_by()
        .annotate(day=TruncDate("occurred_at", tzinfo=timezone.get_current_timezone()))
        .filter(day__in=groups)
        .values("day")
        .annotate(count=Count("pk"))
    )
    for row in counts:
        groups[row["day"]]["count"] = row["count"]
    for group in groups.values():
        # A row deleted between the page query and the aggregate must not leave a heading without a number.
        group.setdefault("count", len(group["events"]))
    return list(groups.values())


def event_list_page_contents(facility, user, page_events):
    """Reload only page contents through the feed read boundary, preserving list order."""
    page_ids = [event.pk for event in page_events]
    if not page_ids:
        return []
    readable = (
        events_for_feed_read(facility, user)
        .filter(pk__in=page_ids)
        .select_related("document_type", "client", "created_by", "case")
    )
    by_id = {event.pk: event for event in readable}
    events = [by_id[pk] for pk in page_ids if pk in by_id]
    enrich_events_with_preview([{"type": "event", "object": event} for event in events], user)
    return events


def event_list_params_from_zeitstrom(state):
    """Carry the resolved feed day and supported metadata filters to the list."""
    day = state.target_date.isoformat()
    params = {"date_from": day, "date_to": day}
    if state.doc_type_id:
        params["doc_type"] = state.doc_type_id
    if state.time_filter is not None:
        params["time_filter"] = state.time_filter_id
    return params


def event_list_url_params(filters):
    """URL parameters that reproduce a resolved state.

    A preset stands for its own days, so the computed dates are left out; the
    link then keeps meaning the preset when it is opened later.
    """
    params = dict(filters)
    if "range" in params:
        params.pop("date_from", None)
        params.pop("date_to", None)
    return params


def _url(base_url, params):
    return f"{base_url}?{urlencode(params)}" if params else base_url


def _without(params, *keys):
    return {key: value for key, value in params.items() if key not in keys}


def _short_date(value, today):
    fmt = _("d.m.") if value.year == today.year else _("d.m.Y")
    return date_format(value, fmt)


def _period_text(filters, today):
    labels = {
        "today": _("Heute"),
        "this_week": _("Diese Woche"),
        "this_month": _("Dieser Monat"),
        "last_month": _("Letzter Monat"),
    }
    if filters.get("range") in labels:
        return labels[filters["range"]]
    start, end = _date_value(filters.get("date_from")), _date_value(filters.get("date_to"))
    if start and end:
        return f"{_short_date(start, today)}–{_short_date(end, today)}"
    if start:
        return _("ab %(date)s") % {"date": _short_date(start, today)}
    return _("bis %(date)s") % {"date": _short_date(end, today)}


def _option_text(items, identifier, attribute):
    item = next((item for item in items if str(item.pk) == identifier), None)
    return str(getattr(item, attribute) if attribute else item) if item is not None else identifier


def event_list_active_filters(filters, options, *, base_url, today=None):
    """One chip per active filter; each carries the URL that drops exactly that filter.

    The counter of the filter button is the length of this list. The search
    text is not a chip: its field is always visible and clearable.
    """
    today = today or timezone.localdate()
    params = event_list_url_params(filters)
    chips = []

    def add(key, text, drop):
        chips.append({"key": key, "text": text, "remove_url": _url(base_url, _without(params, *drop))})

    if "range" in filters or "date_from" in filters or "date_to" in filters:
        add(
            "period",
            _("Zeitraum: %(period)s") % {"period": _period_text(filters, today)},
            ("range", "date_from", "date_to"),
        )
    named = (
        ("doc_type", _("Typ"), "document_types", "name"),
        ("client", _("Person"), "clients", "pseudonym"),
        ("case", _("Fall"), "cases", "title"),
        ("time_filter", _("Schicht"), "time_filters", "label"),
        ("created_by", _("Erfasst von"), "creators", None),
    )
    for key, label, option_key, attribute in named:
        if filters.get(key):
            value = _option_text(options.get(option_key, []), filters[key], attribute)
            add(key, f"{label}: {value}", (key,))
    if filters.get("anonymous"):
        label = _("Anonym")
        value = _("ja") if filters["anonymous"] == "yes" else _("nein")
        add("anonymous", f"{label}: {value}", ("anonymous",))
    if filters.get("mine"):
        add("mine", _("Nur meine"), ("mine",))
    if filters.get("created_via"):
        add(
            "created_via",
            _("Nur Strichliste") if filters["created_via"] == Event.CreatedVia.TALLY else _("Ohne Strichliste"),
            ("created_via",),
        )
    return chips


def event_list_period_segments(filters, *, base_url, today=None):
    """Segment Heute | Woche | Monat | Zeitraum plus the extra 'last month' choice.

    A preset link replaces the dates; the active preset's link switches it off.
    'Zeitraum' turns an active preset into the same explicit days, so nothing
    the user sees is lost; without a preset it leads to the date fields.
    """
    today = today or timezone.localdate()
    rest = _without(event_list_url_params(filters), "range", "date_from", "date_to")
    active_preset = filters.get("range")

    def preset_segment(key, label):
        active = active_preset == key
        return {
            "key": key,
            "label": label,
            "active": active,
            "url": _url(base_url, rest if active else {**rest, "range": key}),
            "htmx": True,
        }

    segments = [
        preset_segment("today", _("Heute")),
        preset_segment("this_week", _("Woche")),
        preset_segment("this_month", _("Monat")),
    ]
    if active_preset:
        start, end = _preset_dates(active_preset, today)
        custom_url = _url(base_url, {**rest, "date_from": start.isoformat(), "date_to": end.isoformat()})
    else:
        dates = {k: filters[k] for k in ("date_from", "date_to") if k in filters}
        custom_url = f"{_url(base_url, {**rest, **dates})}#event-date_from"
    segments.append(
        {
            "key": "custom",
            "label": _("Zeitraum"),
            "active": not active_preset and ("date_from" in filters or "date_to" in filters),
            "url": custom_url,
            "htmx": bool(active_preset),
        }
    )
    return segments, preset_segment("last_month", _("Letzter Monat"))
