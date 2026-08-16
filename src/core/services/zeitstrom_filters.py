"""Kanonischer Zeitstrom-Zustand für Vollansicht, Partials und Watcher."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from django.utils import timezone

from core.models import TimeFilter
from core.services.events.tally import TallyLiveContext, current_tally_context

ZEITSTROM_FILTER_KEYS = ("date", "time_filter", "type", "doc_type", "view")
ZEITSTROM_FEED_TYPES = frozenset({"", "events", "activities", "workitems", "bans"})


@dataclass(frozen=True)
class ZeitstromState:
    """Vollständig aufgelöster, kanonischer Request-Zustand."""

    target_date: date
    time_filter: TimeFilter | None
    time_filters: tuple[TimeFilter, ...]
    live_context: TallyLiveContext
    feed_type: str
    doc_type_id: str
    view: str
    local_date: date

    @property
    def time_filter_id(self) -> str:
        return str(self.time_filter.pk) if self.time_filter is not None else "all"

    @property
    def params(self) -> dict[str, str]:
        return {
            "date": self.target_date.isoformat(),
            "time_filter": self.time_filter_id,
            "type": self.feed_type,
            "doc_type": self.doc_type_id,
            "view": self.view,
        }

    @property
    def is_handover(self) -> bool:
        return self.view == "uebergabe"

    @property
    def is_live_tally_context(self) -> bool:
        if self.is_handover:
            return False
        live_filter = self.live_context.time_filter
        if self.time_filter is not None:
            return (
                live_filter is not None
                and self.time_filter.pk == live_filter.pk
                and self.target_date == self.live_context.shift_date
            )
        return self.target_date == self.local_date


def _canonical_uuid(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(UUID(value))
    except (TypeError, ValueError, AttributeError):
        return ""


def _target_date(params: Mapping[str, str], today: date) -> date:
    raw = params.get("date")
    if raw:
        try:
            return date.fromisoformat(raw)
        except (TypeError, ValueError):
            pass
    return today


def _handover_filter(
    params: Mapping[str, str],
    time_filters: tuple[TimeFilter, ...],
    target_date: date,
    today: date,
    now,
) -> TimeFilter | None:
    raw = params.get("time_filter")
    if raw and raw != "all":
        canonical = _canonical_uuid(raw)
        return next((item for item in time_filters if str(item.pk) == canonical), None)
    if raw == "all" or target_date != today:
        return None
    previous = None
    for item in time_filters:
        if item.covers_time(now):
            break
        previous = item
    return previous


def resolve_zeitstrom_state(
    params: Mapping[str, str],
    facility,
    *,
    today: date | None = None,
    now=None,
    live_context: TallyLiveContext | None = None,
) -> ZeitstromState:
    """Parse and normalize the five Zeitstrom fields once.

    The returned object is the shared contract for the full page, every
    follow-up partial, and the JSON watcher. Invalid or cross-facility shift
    identifiers never reach a model filter; they fall back to the existing
    live-selection semantics.
    """

    today = today or timezone.localdate()
    now = now or timezone.localtime()
    filters = tuple(TimeFilter.objects.for_facility(facility).filter(is_active=True).order_by("sort_order", "pk"))
    live_context = live_context or current_tally_context(facility, time_filters=filters, now=now)
    target_date = _target_date(params, today)
    raw_view = params.get("view", "")
    view = raw_view if raw_view in {"feed", "uebergabe"} else ""

    if view == "uebergabe":
        selected_filter = _handover_filter(params, filters, target_date, today, now)
    else:
        raw_filter = params.get("time_filter")
        canonical_filter = _canonical_uuid(raw_filter) if raw_filter and raw_filter != "all" else ""
        selected_filter = next((item for item in filters if str(item.pk) == canonical_filter), None)
        if selected_filter is None and raw_filter != "all" and target_date == today:
            selected_filter = live_context.time_filter
            if selected_filter is not None:
                target_date = live_context.shift_date

    feed_type = params.get("type", "")
    if feed_type == "all":
        feed_type = ""
    if feed_type not in ZEITSTROM_FEED_TYPES:
        feed_type = ""

    return ZeitstromState(
        target_date=target_date,
        time_filter=selected_filter,
        time_filters=filters,
        live_context=live_context,
        feed_type=feed_type,
        doc_type_id=_canonical_uuid(params.get("doc_type")),
        view=view,
        local_date=today,
    )


def zeitstrom_filter_state(
    params: Mapping[str, str],
    *,
    target_date: date,
    time_filter_id: str = "all",
) -> dict[str, str]:
    """Return the single server-rendered contract for all feed filters."""
    return {
        "date": target_date.isoformat(),
        "time_filter": time_filter_id,
        "type": params.get("type", ""),
        "doc_type": params.get("doc_type", ""),
        "view": params.get("view", ""),
    }
