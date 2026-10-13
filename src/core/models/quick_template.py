"""Quick-Templates: Vorbefüllte Dokumentvorlagen für Schnelleinträge.

Refs #494.
"""

import uuid

from django.conf import settings
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _

from core.models.managers import FacilityScopedManager


class QuickTemplate(models.Model):
    """Vorlage mit vorbefüllten Feldwerten für einen DocumentType.

    Verwendet im Event-Create-Formular zur Beschleunigung wiederkehrender
    Dokumentationen (z.B. "Beratungsgespräch 30 Min", "Standard-Check-in").

    Sensitivitäts-Filter: Templates sind nur sichtbar, wenn
    :func:`core.services.compliance.sensitivity.user_can_see_document_type` für den
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
        # Refs #2068 (b): PROTECT statt CASCADE. Alle uebrigen
        # Konfigurations-Referenzen auf ``DocumentType`` (Event, Client, Case)
        # sind PROTECT und erscheinen im Loeschdialog als Blocker mit Zaehler.
        # Die Schnellvorlagen fielen als einzige still mit — inklusive ihrer
        # von Hand gepflegten ``prefilled_data``, die sich aus dem
        # Dokumentationstyp allein nicht rekonstruieren lassen.
        # ``delete_document_type`` weist sie jetzt wie die anderen als Blocker
        # aus; das Loeschen der Vorlage selbst bleibt ein Ein-Klick-Vorgang.
        on_delete=models.PROTECT,
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

    def clean(self):
        """Reject a document type from another facility with a form error instead of a server error."""
        super().clean()
        if self.facility_id is None or self.document_type_id is None:
            return
        from django.core.exceptions import ValidationError

        from core.models.document_type import DocumentType

        type_facility_id = (
            DocumentType.objects.filter(pk=self.document_type_id).values_list("facility_id", flat=True).first()
        )
        if type_facility_id is not None and type_facility_id != self.facility_id:
            raise ValidationError({"document_type": _("Der Dokumentationstyp gehört nicht zu dieser Einrichtung.")})

    def __str__(self):
        return f"{self.name} ({self.document_type.name})"

    def save(self, *args, **kwargs):
        """Take the shared gate before persistence and FK checks; recheck new option references under it."""
        from core.services.dynamic_protection import lock_dynamic_protection, validate_configuration_save
        from core.services.option_usage import validate_option_references

        validate_configuration_save(self, kwargs.get("update_fields"))
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = kwargs["update_fields"] = frozenset(update_fields)
        with transaction.atomic():
            lock_dynamic_protection(self.facility_id)
            if update_fields is not None and "prefilled_data" not in update_fields:
                return super().save(*args, **kwargs)
            previous = (
                QuickTemplate.objects.for_facility(self.facility_id)
                .filter(pk=self.pk)
                .select_for_update(of=("self",))
                .values_list("prefilled_data", flat=True)
                .first()
            )
            validate_option_references(self.facility_id, self.prefilled_data, previous_data=previous)
            return super().save(*args, **kwargs)
