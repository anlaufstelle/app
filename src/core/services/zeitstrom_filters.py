"""Kanonischer Filterzustand für Zeitstrom-Ansichten und ihre Teilantworten."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

ZEITSTROM_FILTER_KEYS = ("date", "time_filter", "type", "doc_type", "view")


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
