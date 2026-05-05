"""Refs #827 (C-60): pre_save-Signal pflegt Event.search_text.

Der create_event/update_event-Pfad (services/events/crud.py) setzt
``search_text`` zwar selbst, aber Event.objects.create(...)-Aufrufer in
Tests, Seeds und Bulk-Imports umgehen das. Damit das Feld nie veraltet,
faellt das Signal als Sicherheitsnetz fuer alle ueblichen Save-Pfade.
"""

from __future__ import annotations

from django.db.models.signals import pre_save
from django.dispatch import receiver

from core.models import Event
from core.services.events.fields import compute_event_search_text


@receiver(pre_save, sender=Event)
def _refresh_event_search_text(sender, instance, **kwargs):
    if instance.document_type_id is None:
        return
    instance.search_text = compute_event_search_text(instance.data_json, instance.document_type)
