"""Time-Source fuer Compliance-Checks (Refs #958-M3).

Submodule wie ``backup``, ``retention`` und ``audit_events`` rufen ``now()`` statt
``datetime.now(tz=UTC)`` direkt. Damit kann der Mutation-Test-Layer alle Time-
Branches an genau einer Stelle patchen, ohne pro Submodul einen eigenen
``patch.object(..., "datetime", ...)`` zu brauchen.
"""

from __future__ import annotations

from datetime import UTC, datetime


def now() -> datetime:
    """UTC-aware Jetzt — single source of truth fuer Compliance-Boundary-Tests."""
    return datetime.now(tz=UTC)
