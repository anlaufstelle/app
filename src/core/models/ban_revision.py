"""Sensitivity-stratifizierte Hausverbots-Revision pro Einrichtung (Refs #1917).

Der Zeitstrom-Watcher (#1916) braucht ein Signal „am Hausverbots-Banner hat
sich etwas geändert", das er mit **fester kleiner Arbeit** abfragen kann —
ohne die Hausverbots-Historie zu laden oder zu entschlüsseln. Diese Tabelle
ist dieses Signal: genau eine Zeile pro Einrichtung mit drei monotonen
Zählern, je einem pro Sichtbarkeits-Stratum des Rollenmodells
(:data:`core.services.compliance.sensitivity.ROLE_MAX_SENSITIVITY`):

- ``revision_normal``   — Änderungen, die ab Rang 0 (Assistenz) sichtbar sind
  (Existenz/Aktiv-Fenster/Bis/Pseudonym eines Hausverbots; das Banner zeigt
  diese Fakten allen Rollen).
- ``revision_elevated`` — Änderungen, die erst ab Rang 1 (Fachkraft) sichtbar
  sind (Grund eines ELEVATED-Typs).
- ``revision_high``     — Änderungen, die erst ab Rang 2 (Leitung/Admin)
  sichtbar sind (Grund eines HIGH-Typs).

**Seitenkanal-Kern:** Der Watch-Validator einer Rolle wird ausschließlich über
das Zähler-**Präfix** bis zum eigenen Rang gebildet. Ein Bump in einem höheren
Stratum verändert für niedrigere Rollen weder Validator noch ETag noch
Antwortarbeit — Änderungen an unsichtbaren Hausverbots-Daten bleiben dadurch
vollständig unbeobachtbar. Nur die Schreibpfade
(:mod:`core.services.system.ban_revision`) erhöhen die Zähler; der Poll liest
eine Zeile über den Unique-Index auf ``facility_id``.
"""

from uuid import uuid4

from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models.managers import FacilityScopedManager


class BanRevision(models.Model):
    """Opake Banner-Revision: „ab Rang N hat sich etwas geändert"."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    facility = models.OneToOneField(
        "core.Facility",
        on_delete=models.CASCADE,
        related_name="ban_revision",
        verbose_name=_("Einrichtung"),
    )
    revision_normal = models.BigIntegerField(default=0, verbose_name=_("Revision (normal)"))
    revision_elevated = models.BigIntegerField(default=0, verbose_name=_("Revision (erhöht)"))
    revision_high = models.BigIntegerField(default=0, verbose_name=_("Revision (hoch)"))

    objects = FacilityScopedManager()

    class Meta:
        verbose_name = _("Hausverbots-Revision")
        verbose_name_plural = _("Hausverbots-Revisionen")

    def __str__(self):
        return f"BanRevision({self.revision_normal}/{self.revision_elevated}/{self.revision_high})"
