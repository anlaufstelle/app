"""Numbers of the Zeitstrom KPI row.

Every number that links to a list is the hit count of exactly that list: the
contact count runs through the same filter resolution and queryset as the
contact list, with the same parameters as the link. The tally total comes
from the same grouped query as the tile counters, filtered by tally origin
across all types, so it costs no query of its own.
"""

from __future__ import annotations

from urllib.parse import urlencode

from django.utils import timezone

from core.models import Event
from core.services.dashboard.main import zeitstrom_workitem_counts
from core.services.events.list_filters import (
    event_list_options,
    event_list_params_from_zeitstrom,
    event_list_queryset,
    resolve_event_list_filters,
)


def _contacts(facility, user, state, today) -> dict:
    params = {**event_list_params_from_zeitstrom(state), "created_via": Event.CreatedVia.STANDARD.value}
    # Only the shift choices are read here (filters are validated against
    # them); the other option lists stay lazy and unevaluated.
    options = event_list_options(facility, user)
    options["time_filters"] = list(state.time_filters)
    filters = resolve_event_list_filters(params, facility, user, today=today, options=options)
    count = event_list_queryset(facility, user, filters, options=options).count()
    if state.time_filter is not None:
        scope = "shift"
    elif state.target_date == today:
        scope = "today"
    else:
        scope = "day"
    return {"contacts_count": count, "contacts_params": urlencode(params), "contacts_scope": scope}


def _tally(tally_summary) -> dict:
    if tally_summary is None or not tally_summary["rows"]:
        return {"tally_total": None, "tally_params": None}
    shift = tally_summary["time_filter"]
    day = tally_summary["shift_date"] if shift is not None else timezone.localtime(tally_summary["window_start"]).date()
    params = {"date_from": day.isoformat(), "date_to": day.isoformat()}
    if shift is not None:
        params["time_filter"] = str(shift.pk)
    params["created_via"] = Event.CreatedVia.TALLY.value
    return {
        "tally_total": tally_summary["tally_total"],
        "tally_params": urlencode(params),
    }


def build_zeitstrom_kpis(facility, user, state, *, tally_summary, today=None, workitem_counts=None) -> dict:
    """Return the counts and link parameters of the feed-mode KPI row."""
    today = today or timezone.localdate()
    counts = workitem_counts if workitem_counts is not None else zeitstrom_workitem_counts(user, facility, today=today)
    return {
        **_contacts(facility, user, state, today),
        **_tally(tally_summary),
        "my_open_count": counts["my_open"],
        "my_overdue_count": counts["my_overdue"],
    }
