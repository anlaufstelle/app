"""Klient-Anonymisierungs-Trigger fuer den Retention-Pfad (#744, #780).

Anonymisiert Klienten, deren Events alle soft-geloescht sind — Bridge
zwischen Event-Retention und der eigentlichen Anonymisierungs-Logik in
:mod:`core.services.client.main` (Hard-Anonymize) bzw.
:mod:`core.services.compliance.k_anonymization` (K-Anonymize).

Welcher Pfad gewaehlt wird, haengt am Facility-Setting
``retention_use_k_anonymization`` (Refs #780): Default ``False`` = Hard,
``True`` = K-Anon mit Schwelle aus ``k_anonymity_threshold``.

Security-Review N5: Die Schwelle ``k`` wird jetzt erzwungen — pro Klient
prueft ``is_k_anonymous`` die Aequivalenzklasse ``(age_cluster,
contact_stage)``; unterbesetzte Buckets (< k) fallen fail-safe auf den
Hard-Pfad zurueck, statt faelschlich als ``k_anonymized=True`` markiert zu
werden.

Beide Pfade tilgen die Freitext-Kaskade auf Faelle/Episoden/Aufgaben: der
Hard-Pfad inline in ``anonymize_client``, der K-Anon-Pfad hier im Bridge-Layer
(Refs #1094) — die ``k_anonymize_client``-Primitive selbst bleibt client-only.
"""

from django.db.models import Count, Q

# Refs #818 — Inline-Imports an Modulkopf gehoben.
from core.models import AuditLog, Client, Settings
from core.services.audit import audit_retention_decision
from core.services.client import _redact_cases_and_episodes, _redact_workitems
from core.services.compliance import is_k_anonymous, k_anonymize_client


def anonymize_clients(facility, dry_run):
    """Anonymize clients whose events have all been soft-deleted.

    A client is anonymized when they have at least one event and all of them have
    ``is_deleted=True``. Already anonymized clients (pseudonym starts with
    ``Gelöscht-`` or ``k_anonymized=True``) are skipped.

    Honors ``Settings.retention_use_k_anonymization`` (Refs #780): when active
    on the facility, a client is k-anonymized via :func:`k_anonymize_client`
    with ``k=settings.k_anonymity_threshold`` **only if** its equivalence class
    is already ``is_k_anonymous`` (bucket >= k). Under-populated buckets — and
    the default (setting off) — take the classical ``Client.anonymize()`` hard
    path (Security-Review N5). Mixed runs audit both categories separately
    (``client_k_anonymized`` / ``client_anonymized``).

    Returns ``{"count": N}`` (total, both paths combined).
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

        k_count = 0
        hard_count = 0
        for client in candidates.iterator():
            # Security N5: k wird erzwungen. Nur wenn die Aequivalenzklasse
            # (age_cluster, contact_stage) mindestens k Mitglieder hat, ist
            # "k-anonymisiert" eine ehrliche Zusicherung. Unterbesetzte
            # Buckets fallen fail-safe auf Hard-Anonymize zurueck — die
            # Retention-Zusage (PII weg) gilt in beiden Faellen.
            if use_k_anon and is_k_anonymous(client, k=k):
                # ``k_anonymize_client`` ist bewusst client-only (Vertrag der
                # Primitive). Der Retention-Bridge-Layer ergaenzt die Freitext-
                # Kaskade auf Faelle/Episoden/Aufgaben, damit der K-Anon-Modus
                # dieselbe PII-Tilgung leistet wie der Hard-Pfad — sonst bliebe
                # Klienten-PII in core_case/core_episode/core_workitem stehen
                # (Refs #1094). Die Primitive bleibt unangetastet.
                k_anonymize_client(client, k=k)
                _redact_cases_and_episodes(client)
                _redact_workitems(client)
                k_count += 1
            else:
                client.anonymize()
                hard_count += 1

        if k_count:
            audit_retention_decision(
                facility,
                target_type="Client",
                action=AuditLog.Action.DELETE,
                category="client_k_anonymized",
                command="enforce_retention",
                count=k_count,
            )
        if hard_count:
            audit_retention_decision(
                facility,
                target_type="Client",
                action=AuditLog.Action.DELETE,
                category="client_anonymized",
                command="enforce_retention",
                count=hard_count,
            )
    return {"count": count}
