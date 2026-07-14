"""Loeschschutz fuer ``Facility`` gegen aktive Legal Holds (L1, Refs #1375).

``Facility`` -> ``LegalHold`` und ``Facility`` -> ``Event`` sind beide
``on_delete=CASCADE``. Ohne diesen Guard risse eine harte Facility-Loeschung
(Django-Admin/Shell/ORM) einen AKTIVEN Legal Hold — den Nachweis einer
Aufbewahrungspflicht (Spoliationsschutz) — samt der davon gehaltenen Events
mit. Der ``pre_delete``-Guard blockt die Loeschung auf Modellebene fuer JEDEN
Loeschpfad, solange die Facility noch aktive Holds traegt.

Analog zu ``LegalHold.created_by = PROTECT`` (DAT-04, Refs #1347) wird der
Loeschversuch mit ``ProtectedError`` abgebrochen — dieselbe Semantik, die
Django fuer geschuetzte FKs verwendet.
"""

from __future__ import annotations

from django.db.models import ProtectedError
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils import timezone

from core.models import Facility, LegalHold


@receiver(pre_delete, sender=Facility, dispatch_uid="facility_active_legal_hold_guard")
def block_facility_delete_with_active_holds(sender, instance, **kwargs):
    """Bricht die Loeschung ab, wenn die Facility aktive Legal Holds hat.

    "Aktiv" = nicht aufgehoben (``dismissed_at IS NULL``) und nicht abgelaufen
    (``expires_at`` in der Zukunft oder leer) — dieselbe Definition wie
    ``LegalHold.is_active`` / ``get_active_hold_target_ids`` (``timezone.localdate``,
    Europe/Berlin; Refs #1192).
    """
    active_holds = LegalHold.objects.filter(
        facility=instance,
        dismissed_at__isnull=True,
    ).exclude(expires_at__lt=timezone.localdate())
    if active_holds.exists():
        raise ProtectedError(
            "Einrichtung mit aktiven Legal Holds kann nicht geloescht werden "
            "(Aufbewahrungspflicht/Spoliationsschutz). Holds zuerst aufheben.",
            set(active_holds),
        )
