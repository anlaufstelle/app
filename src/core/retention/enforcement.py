"""Retention-Enforcement: Soft-Delete-Strategien fuer Events (#744).

API-Grenze fuer die vier Enforce-Strategien (anonymous, identified,
qualified, document_type) plus Pipeline (process_facility_retention)
und Activities-Hard-Delete (enforce_activities).

**Phase 1 (#744):** Re-Exports aus :mod:`core.services.retention`.
Physische Verschiebung des Codes in dieses Modul ist Phase 2 — die
API-Grenze besteht bereits, neue Aufrufer sollen ueber dieses Modul
importieren.
"""

from core.services.retention import (
    collect_doomed_events,
    enforce_activities,
    enforce_anonymous,
    enforce_document_type_retention,
    enforce_identified,
    enforce_qualified,
    process_facility_retention,
)

__all__ = [
    "collect_doomed_events",
    "enforce_activities",
    "enforce_anonymous",
    "enforce_document_type_retention",
    "enforce_identified",
    "enforce_qualified",
    "process_facility_retention",
]
