"""Klient-Anonymisierungs-Trigger fuer den Retention-Pfad (#744, #780).

Anonymisiert Klienten, deren Events alle soft-geloescht sind — Bridge
zwischen Event-Retention und der eigentlichen Anonymisierungs-Logik in
:mod:`core.services.clients` (Hard-Anonymize) bzw.
:mod:`core.services.k_anonymization` (K-Anonymize).

Welcher Pfad gewaehlt wird, haengt am Facility-Setting
``retention_use_k_anonymization`` (Refs #780): Default ``False`` = Hard,
``True`` = K-Anon mit Schwelle aus ``k_anonymity_threshold``.
"""

from django.db.models import Count, Q

# Refs #818 — Inline-Imports an Modulkopf gehoben.
from core.models import AuditLog, Client, Settings
from core.services.audit import audit_retention_decision
from core.services.k_anonymization import k_anonymize_client


def anonymize_clients(facility, dry_run):
    """Anonymize clients whose events have all been soft-deleted.

    A client is anonymized when they have at least one event and all of them have
    ``is_deleted=True``. Already anonymized clients (pseudonym starts with
    ``Gelöscht-`` or ``k_anonymized=True``) are skipped.

    Honors ``Settings.retention_use_k_anonymization`` (Refs #780): when active
    on the facility, falls back to :func:`k_anonymize_client` with
    ``k=settings.k_anonymity_threshold``; otherwise the classical
    ``Client.anonymize()`` path.

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
        try:
            settings_obj = facility.settings
        except Settings.DoesNotExist:
            settings_obj = None
        use_k_anon = bool(settings_obj and settings_obj.retention_use_k_anonymization)
        k = (settings_obj.k_anonymity_threshold if settings_obj else 5) or 5

        category = "client_k_anonymized" if use_k_anon else "client_anonymized"
        for client in candidates.iterator():
            if use_k_anon:
                k_anonymize_client(client, k=k)
            else:
                client.anonymize()
        audit_retention_decision(
            facility,
            target_type="Client",
            action=AuditLog.Action.DELETE,
            category=category,
            command="enforce_retention",
            count=count,
        )
    return {"count": count}
