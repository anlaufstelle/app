"""Shared parsing for the configured field-encryption key ring."""

from __future__ import annotations


def split_key_ring(value: str | None) -> list[str]:
    """Return stripped, non-empty entries from a comma-separated key ring."""
    return [entry.strip() for entry in (value or "").split(",") if entry.strip()]
