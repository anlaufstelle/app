"""Service layer for search logic."""

import logging

from core.models import Client, Event, FieldTemplate
from core.services.sensitivity import allowed_sensitivities_for_user

logger = logging.getLogger(__name__)


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
        for key, value in data.items():
            if key in encrypted_field_slugs:
                continue
            if isinstance(value, dict):
                continue
            if isinstance(value, list):
                text = ", ".join(str(v) for v in value)
            else:
                text = str(value)
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
