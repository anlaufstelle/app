"""Service layer for search logic."""

import logging

from django.contrib.postgres.search import TrigramSimilarity

from core.models import Client, DocumentTypeField, Event, FieldTemplate
from core.services.sensitivity import allowed_sensitivities_for_user, user_can_see_field

logger = logging.getLogger(__name__)

DEFAULT_TRIGRAM_THRESHOLD = 0.3


def search_clients_and_events(facility, user, query, max_clients=20, max_events=20):
    """Search clients and events within a facility.

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

    encrypted_field_slugs = set(
        FieldTemplate.objects.for_facility(facility).filter(is_encrypted=True).values_list("slug", flat=True)
    )

    # Build slug → field sensitivity mapping for each document type
    # so we can exclude fields the user may not see.
    _dt_field_sensitivity_cache: dict[str, dict[str, str]] = {}

    def _get_field_sensitivities(doc_type):
        dt_id = str(doc_type.pk)
        if dt_id not in _dt_field_sensitivity_cache:
            _dt_field_sensitivity_cache[dt_id] = dict(
                DocumentTypeField.objects.filter(document_type=doc_type)
                .select_related("field_template")
                .values_list("field_template__slug", "field_template__sensitivity")
            )
        return _dt_field_sensitivity_cache[dt_id]

    events_by_data = []
    candidates = list(
        Event.objects.filter(
            facility=facility,
            is_deleted=False,
            data_json__icontains=query,
            document_type__sensitivity__in=allowed_sens,
        ).select_related("document_type", "client")[: max_events * 3]
    )
    q_lower = query.lower()
    for event in candidates:
        data = event.data_json or {}
        field_sensitivities = _get_field_sensitivities(event.document_type)
        for key, value in data.items():
            if key in encrypted_field_slugs:
                continue
            # Check field-level sensitivity: skip fields the user may not see
            field_sens = field_sensitivities.get(key, "")
            if not user_can_see_field(user, event.document_type.sensitivity, field_sens):
                continue
            if isinstance(value, dict):
                continue
            text = ", ".join(str(v) for v in value) if isinstance(value, list) else str(value)
            if q_lower in text.lower():
                events_by_data.append(event)
                break
        if len(events_by_data) >= max_events:
            break

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
