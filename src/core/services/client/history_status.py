"""Content-free history status for anonymised person pages (Refs #2524)."""

from core.models import EventHistory
from core.services.events.history_diff import is_redaction_marker


def pending_history_count(client):
    """Count versions that still contain values, including held versions."""
    if client.anonymized_at is None:
        return 0
    rows = EventHistory.objects.filter(event__facility=client.facility, event__client=client).values_list(
        "data_before", "data_after"
    )
    return sum(any(payload and not is_redaction_marker(payload) for payload in row) for row in rows.iterator())
