"""Shared formatting and parsing helpers."""

from __future__ import annotations

from datetime import date
from typing import Any


def isoformat_or_none(value: Any) -> str | None:
    """ISO-8601 eines optionalen Datums-/Zeitstempels — ``None`` bleibt ``None``.

    Refs #1827: ``value.isoformat() if value else None`` stand als benannte
    Funktion zweimal (``services/system/facility_export.py``,
    ``services/client/export.py``) und inline an acht weiteren Stellen
    (JSON-Serialisierung von Offline-Bundles/Kontexten).
    """
    return value.isoformat() if value else None


def format_file_size(size_bytes: int) -> str:
    """Format file size for display (B / KB / MB)."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def parse_date(raw: str | None, default: date | None = None) -> date | None:
    """Parse an ISO-8601 date string, returning *default* on failure."""
    if not raw:
        return default
    try:
        return date.fromisoformat(raw)
    except (ValueError, TypeError):
        return default
