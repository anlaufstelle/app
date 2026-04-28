"""Quick-Templates: Vorbefüllte Dokumentvorlagen für Schnelleinträge.

Refs #494.
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models.managers import FacilityScopedManager


class QuickTemplate(models.Model):
    """Vorlage mit vorbefüllten Feldwerten für einen DocumentType.

    Verwendet im Event-Create-Formular zur Beschleunigung wiederkehrender
    Dokumentationen (z.B. "Beratungsgespräch 30 Min", "Standard-Check-in").

    Sensitivitäts-Filter: Templates sind nur sichtbar, wenn
    :func:`core.services.sensitivity.user_can_see_document_type` für den
    zugehörigen DocumentType True liefert. Gespeicherte ``prefilled_data``
    enthalten daher per Service-Layer-Whitelist ausschließlich Werte von
    Feldern mit effektiver Sensitivität = NORMAL.
    """

    objects = FacilityScopedManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    facility = models.ForeignKey(
        "core.Facility",
        on_delete=models.CASCADE,
        related_name="quick_templates",
        verbose_name=_("Einrichtung"),
    )
    document_type = models.ForeignKey(
        "core.DocumentType",
        on_delete=models.CASCADE,
        related_name="quick_templates",
        verbose_name=_("Dokumentationstyp"),
    )
    name = models.CharField(
        max_length=200,
        verbose_name=_("Anzeigename"),
        help_text=_("Beispiel: 'Beratungsgespräch 30 Min'."),
    )
    prefilled_data = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("Vorbefüllte Werte"),
        help_text=_("Slug → Wert. Wird vor Speicherung auf NORMAL-Felder gefiltert."),
    )
    sort_order = models.IntegerField(
        default=0,
        verbose_name=_("Sortierung"),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("Aktiv"),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quick_templates_created",
        verbose_name=_("Erstellt von"),
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("Erstellt am"),
    )

    class Meta:
        verbose_name = _("Quick-Template")
        verbose_name_plural = _("Quick-Templates")
        ordering = ["sort_order", "name"]
        indexes = [
            models.Index(fields=["facility", "document_type", "is_active"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.document_type.name})"
