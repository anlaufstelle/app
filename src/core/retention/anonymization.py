"""Klient-Anonymisierungs-Trigger fuer den Retention-Pfad (#744).

Anonymisiert Klienten, deren Events alle soft-geloescht sind — Bridge
zwischen Event-Retention und der eigentlichen Anonymisierungs-Logik in
:mod:`core.services.clients`.
"""

from django.db.models import Count, Q

# Refs #818 — Inline-Imports an Modulkopf gehoben.
from core.models import AuditLog, Client


def anonymize_clients(facility, dry_run):
    """Anonymize clients whose events have all been soft-deleted.

    A client is anonymized when they have at least one event and all of them have
    ``is_deleted=True``. Already anonymized clients (pseudonym starts with
    ``Gelöscht-`` or ``k_anonymized=True``) are skipped.

    Returns ``{"count": N}``.
    """
    candidates = (
        Client.objects.filter(facility=facility)
        .exclude(Q(pseudonym__startswith="Gelöscht-") | Q(k_anonymized=True))
        .annotate(
            total_events=Count("events"),
            active_events=Count("events", filter=Q(events__is_deleted=False)),
        )
        .filter(total_events__gt=0, active_events=0)
    )

    count = candidates.count()
    if count and not dry_run:
        for client in candidates.iterator():
            client.anonymize()
        AuditLog.objects.create(
            facility=facility,
            action=AuditLog.Action.DELETE,
            target_type="Client",
            detail={
                "command": "enforce_retention",
                "category": "client_anonymized",
                "count": count,
            },
        )
    return {"count": count}
