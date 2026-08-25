"""Gemeinsame Ziel-Queries fuer Art.-15-Auskunft und Art.-17-Redaktion."""

from collections.abc import Collection
from uuid import UUID

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q

from core.models.case import Case
from core.models.client import Client
from core.models.event import Event
from core.models.workitem import WorkItem


def client_and_event_targets_q(client: Client, event_ids: Collection[UUID]) -> Q:
    """Ziele einer Person und der explizit uebergebenen Ereignisse."""
    targets = Q(target_type="Client", target_id=client.pk)
    if event_ids:
        targets |= Q(target_type="Event", target_id__in=event_ids)
    return targets


def client_activity_targets_q(client: Client, event_ids: Collection[UUID]) -> Q:
    """Activity-Ziele einer Person samt Ereignissen, Aufgaben und Faellen."""
    targets = Q(
        target_type=ContentType.objects.get_for_model(Client),
        target_id=client.pk,
    )
    if event_ids:
        targets |= Q(
            target_type=ContentType.objects.get_for_model(Event),
            target_id__in=event_ids,
        )

    workitem_ids: list[UUID] = list(client.work_items.values_list("pk", flat=True))
    if workitem_ids:
        targets |= Q(
            target_type=ContentType.objects.get_for_model(WorkItem),
            target_id__in=workitem_ids,
        )

    case_ids: list[UUID] = list(client.cases.values_list("pk", flat=True))
    if case_ids:
        targets |= Q(
            target_type=ContentType.objects.get_for_model(Case),
            target_id__in=case_ids,
        )
    return targets


__all__ = ["client_activity_targets_q", "client_and_event_targets_q"]
