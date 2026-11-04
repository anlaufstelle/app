"""Parse the configured field-encryption key ring.

Shared by the settings start gate and the runtime field encryption. The module
depends on the standard library only, so settings can import it while Django's
app registry is not ready yet.
"""

from __future__ import annotations


def split_key_ring(value: str | None) -> list[str]:
    """Return stripped, non-empty entries from a comma-separated key ring."""
    return [entry.strip() for entry in (value or "").split(",") if entry.strip()]
