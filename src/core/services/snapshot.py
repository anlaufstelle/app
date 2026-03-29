"""Snapshot service: create, ensure and retrieve monthly statistics snapshots."""

import calendar
import json
import logging
from datetime import date

from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from core.models import DocumentType, StatisticsSnapshot
from core.services.export import get_jugendamt_statistics
from core.services.statistics import get_statistics

logger = logging.getLogger(__name__)


def create_or_update_snapshot(facility, year, month):
    """Create or update a statistics snapshot for a given facility and month."""
    date_from = date(year, month, 1)
    _, last_day = calendar.monthrange(year, month)
    date_to = date(year, month, last_day)

    stats = get_statistics(facility, date_from, date_to)
    jg_stats = get_jugendamt_statistics(facility, date_from, date_to)

    # Remove top_clients (not snapshot-safe due to anonymization)
    stats.pop("top_clients", None)

    # Enrich by_document_type with system_type and document_type_id
    for entry in stats.get("by_document_type", []):
        try:
            dt = DocumentType.objects.get(
                facility=facility,
                name=entry["name"],
                category=entry["category"],
            )
            entry["system_type"] = dt.system_type or ""
            entry["document_type_id"] = str(dt.id)
        except DocumentType.DoesNotExist:
            entry["system_type"] = ""
            entry["document_type_id"] = ""

    # Force-convert lazy translation strings to plain strings for JSON storage
    stats = json.loads(json.dumps(stats, cls=DjangoJSONEncoder))
    jg_stats = json.loads(json.dumps(jg_stats, cls=DjangoJSONEncoder))

    StatisticsSnapshot.objects.update_or_create(
        facility=facility,
        year=year,
        month=month,
        defaults={"data": stats, "jugendamt_data": jg_stats},
    )
    logger.info(
        "Snapshot created/updated: facility=%s year=%d month=%d",
        facility.name,
        year,
        month,
    )


def ensure_snapshots_for_months(facility, event_queryset):
    """Create snapshots for all months covered by the given events (excluding current month)."""
    today = timezone.localdate()
    current = (today.year, today.month)

    months = event_queryset.values_list("occurred_at__year", "occurred_at__month").distinct()
    for year, month in months:
        if (year, month) < current:  # Only past, completed months
            create_or_update_snapshot(facility, year, month)


def get_snapshot(facility, year, month):
    """Return snapshot data dict or None if no snapshot exists."""
    snap = StatisticsSnapshot.objects.filter(facility=facility, year=year, month=month).first()
    return snap.data if snap else None


# ---------------------------------------------------------------------------
# Hybrid query helpers
# ---------------------------------------------------------------------------


def _split_into_segments(date_from, date_to):
    """Split a date range into monthly segments.

    Returns a list of ``(seg_from, seg_to, use_snapshot)`` tuples.

    A segment gets ``use_snapshot=True`` when it covers a **full** calendar
    month **and** that month lies strictly before the current month.
    """
    today = timezone.localdate()
    current_ym = (today.year, today.month)

    segments = []
    cursor = date_from

    while cursor <= date_to:
        year, month = cursor.year, cursor.month
        _, last_day = calendar.monthrange(year, month)
        first_of_month = date(year, month, 1)
        last_of_month = date(year, month, last_day)

        seg_from = cursor
        seg_to = min(last_of_month, date_to)

        is_full_month = seg_from == first_of_month and seg_to == last_of_month
        is_past_month = (year, month) < current_ym

        segments.append((seg_from, seg_to, is_full_month and is_past_month))

        # Advance cursor to the first day of the next month
        if month == 12:
            cursor = date(year + 1, 1, 1)
        else:
            cursor = date(year, month + 1, 1)

    return segments


def _empty_stats():
    """Return an empty statistics dict matching the ``get_statistics`` shape."""
    return {
        "total_contacts": 0,
        "by_contact_stage": {"anonym": 0, "identifiziert": 0, "qualifiziert": 0},
        "by_document_type": [],
        "by_age_cluster": [],
        "unique_clients": 0,
    }


def _merge_stats(stats_list):
    """Merge multiple statistics dicts into one.

    Merge logic per field:
    - ``total_contacts`` – sum
    - ``by_contact_stage`` – sum per key
    - ``by_document_type`` – merge by ``(name, category)`` composite key, sum ``count``
    - ``by_age_cluster`` – merge by ``cluster`` key, sum ``count``, keep labels
    - ``unique_clients`` – sum (approximation)
    """
    if not stats_list:
        return _empty_stats()

    merged = _empty_stats()

    # Accumulators for list-based fields
    doc_type_map = {}  # (name, category) → count
    age_cluster_map = {}  # cluster → {label, count}

    for stats in stats_list:
        merged["total_contacts"] += stats.get("total_contacts", 0)
        merged["unique_clients"] += stats.get("unique_clients", 0)

        # by_contact_stage
        for key in ("anonym", "identifiziert", "qualifiziert"):
            merged["by_contact_stage"][key] += stats.get("by_contact_stage", {}).get(key, 0)

        # by_document_type – merge by (name, category)
        for entry in stats.get("by_document_type", []):
            composite = (entry["name"], entry["category"])
            if composite in doc_type_map:
                doc_type_map[composite]["count"] += entry["count"]
            else:
                doc_type_map[composite] = {**entry}

        # by_age_cluster – merge by cluster
        for entry in stats.get("by_age_cluster", []):
            cluster = entry["cluster"]
            if cluster in age_cluster_map:
                age_cluster_map[cluster]["count"] += entry["count"]
            else:
                age_cluster_map[cluster] = {**entry}

    merged["by_document_type"] = sorted(doc_type_map.values(), key=lambda e: e["count"], reverse=True)
    merged["by_age_cluster"] = sorted(age_cluster_map.values(), key=lambda e: e["count"], reverse=True)

    return merged


def _empty_jugendamt_stats():
    """Return an empty Jugendamt statistics dict."""
    return {
        "total": 0,
        "by_category": [],
        "by_age_cluster": [],
        "unique_clients": 0,
    }


def _merge_jugendamt_stats(stats_list):
    """Merge multiple Jugendamt statistics dicts.

    Merge logic:
    - ``total`` – sum
    - ``by_category`` – merge by category name, sum count.
      Normalizes both tuples and lists from JSON to output tuples.
    - ``by_age_cluster`` – merge by ``cluster`` key, sum ``count``
    - ``unique_clients`` – sum (approximation)
    """
    if not stats_list:
        return _empty_jugendamt_stats()

    merged = _empty_jugendamt_stats()
    category_map = {}  # name → count
    age_cluster_map = {}  # cluster → {label, count}

    for stats in stats_list:
        merged["total"] += stats.get("total", 0)
        merged["unique_clients"] += stats.get("unique_clients", 0)

        # by_category – normalize tuples/lists from JSON snapshots
        for entry in stats.get("by_category", []):
            name, count = entry[0], entry[1]
            category_map[name] = category_map.get(name, 0) + count

        # by_age_cluster
        for entry in stats.get("by_age_cluster", []):
            cluster = entry["cluster"]
            if cluster in age_cluster_map:
                age_cluster_map[cluster]["count"] += entry["count"]
            else:
                age_cluster_map[cluster] = {**entry}

    merged["by_category"] = [(name, count) for name, count in category_map.items()]
    merged["by_age_cluster"] = sorted(age_cluster_map.values(), key=lambda e: e["count"], reverse=True)

    return merged


def get_statistics_hybrid(facility, date_from, date_to):
    """Return statistics for a date range, using snapshots for full past months.

    Segments that cover a complete past calendar month are served from
    pre-computed snapshots (if available), all others fall back to a live
    database query.  ``top_clients`` is always computed live over the full
    range because it cannot be meaningfully merged from monthly snapshots.
    """
    segments = _split_into_segments(date_from, date_to)
    segment_stats = []

    for seg_from, seg_to, use_snapshot in segments:
        stats = None
        if use_snapshot:
            stats = get_snapshot(facility, seg_from.year, seg_from.month)
        if stats is None:
            stats = get_statistics(facility, seg_from, seg_to)
            # Remove top_clients from segment stats (merged separately)
            stats.pop("top_clients", None)
        segment_stats.append(stats)

    merged = _merge_stats(segment_stats)

    # top_clients always from a live full-range query
    live_full = get_statistics(facility, date_from, date_to)
    merged["top_clients"] = live_full["top_clients"]

    return merged


def get_jugendamt_statistics_hybrid(facility, date_from, date_to):
    """Return Jugendamt statistics for a date range, using snapshots where possible.

    Same hybrid pattern as :func:`get_statistics_hybrid` but for Jugendamt
    report data.
    """
    segments = _split_into_segments(date_from, date_to)
    segment_stats = []

    for seg_from, seg_to, use_snapshot in segments:
        stats = None
        if use_snapshot:
            snap = StatisticsSnapshot.objects.filter(
                facility=facility,
                year=seg_from.year,
                month=seg_from.month,
            ).first()
            if snap and snap.jugendamt_data:
                stats = snap.jugendamt_data
        if stats is None:
            stats = get_jugendamt_statistics(facility, seg_from, seg_to)
        segment_stats.append(stats)

    return _merge_jugendamt_stats(segment_stats)
