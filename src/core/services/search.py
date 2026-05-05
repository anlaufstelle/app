"""Service layer for search logic."""

import logging

from django.contrib.postgres.search import TrigramSimilarity

from core.models import Client, Event
from core.services.sensitivity import allowed_sensitivities_for_user

logger = logging.getLogger(__name__)

DEFAULT_TRIGRAM_THRESHOLD = 0.3


def search_clients_and_events(facility, user, query, max_clients=20, max_events=20):
    """Search clients and events within a facility.

    Refs #827 (C-60): Volltextsuche laeuft gegen die ``Event.search_text``-
    Spalte (im create/update-Pfad gepflegt, GIN-trgm-Index), nicht mehr
    gegen ``data_json__icontains``. Verschluesselte und ELEVATED/HIGH-
    Felder sind im Suchindex bewusst nicht enthalten — der Sensitivity-
    Filter geschieht damit beim Schreiben, nicht beim Lesen.

    Returns (clients, events) tuple.
    """
    if not query:
        return [], []

    clients = list(
        Client.objects.filter(
            facility=facility,
            pseudonym__icontains=query,
            is_active=True,
        )[:max_clients]
    )

    allowed_sens = allowed_sensitivities_for_user(user)

    events_by_client = Event.objects.filter(
        facility=facility,
        is_deleted=False,
        client__pseudonym__icontains=query,
        document_type__sensitivity__in=allowed_sens,
    ).select_related("document_type", "client")[:max_events]

    events_by_data = list(
        Event.objects.filter(
            facility=facility,
            is_deleted=False,
            search_text__icontains=query,
            document_type__sensitivity__in=allowed_sens,
        ).select_related("document_type", "client")[:max_events]
    )

    seen_ids = set()
    combined = []
    for event in list(events_by_client) + events_by_data:
        if event.pk not in seen_ids:
            seen_ids.add(event.pk)
            combined.append(event)
    events = combined[:max_events]

    return clients, events


def search_similar_clients(facility, query, exclude_pks=None, max_results=10, threshold=None):
    """Return active clients whose pseudonym is similar to ``query`` (pg_trgm).

    Uses ``TrigramSimilarity`` on ``Client.pseudonym`` — tolerant to typos
    and phonetic variants ("Schmidt" vs. "Schmitt").

    Exact substring matches (``pseudonym__icontains=query``) are always excluded,
    including those that overflow the caller's display cap — otherwise excess
    icontains hits would be silently relabeled as fuzzy and displace real
    similar results (Refs #580). ``exclude_pks`` is still honored for
    additional caller-specific dedup.

    ``threshold`` defaults to ``facility.settings.search_trigram_threshold``.
    """
    if not query or len(query) < 2:
        return []

    if threshold is None:
        settings_obj = getattr(facility, "settings", None)
        threshold = getattr(settings_obj, "search_trigram_threshold", DEFAULT_TRIGRAM_THRESHOLD)

    qs = (
        Client.objects.filter(facility=facility, is_active=True)
        .annotate(similarity=TrigramSimilarity("pseudonym", query))
        .filter(similarity__gte=threshold)
        .exclude(pseudonym__icontains=query)
        .order_by("-similarity")
    )
    if exclude_pks:
        qs = qs.exclude(pk__in=exclude_pks)
    return list(qs[:max_results])
