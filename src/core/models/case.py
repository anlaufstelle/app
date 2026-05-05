"""Case -- groups events for a client."""

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models.managers import FacilityScopedManager
from core.models.mixins import SoftDeletableModel


class Case(SoftDeletableModel):
    """Case/file for a client."""

    class Status(models.TextChoices):
        OPEN = "open", _("Offen")
        CLOSED = "closed", _("Geschlossen")

    objects = FacilityScopedManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    facility = models.ForeignKey(
        "core.Facility",
        on_delete=models.CASCADE,
        related_name="cases",
        verbose_name=_("Einrichtung"),
    )
    client = models.ForeignKey(
        "core.Client",
        on_delete=models.PROTECT,
        related_name="cases",
        verbose_name=_("Klientel"),
        help_text=_(
            "Pflichtfeld: Jeder Fall ist einer Person zugeordnet. "
            "PROTECT verhindert versehentliches Löschen einer Person mit aktiven Fällen."
        ),
    )
    title = models.CharField(max_length=200, verbose_name=_("Titel"))
    description = models.TextField(
        blank=True,
        verbose_name=_("Beschreibung"),
        help_text=_(
            "Frei-Text-Beschreibung des Falls. Nicht feldverschlüsselt — "
            "keine Klarnamen oder Art-9-Daten hier vermerken; "
            "sensible Inhalte gehören in ein Event-FieldTemplate "
            "mit Sensitivity=HOCH."
        ),
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        verbose_name=_("Status"),
        help_text=_("Offen = aktiver Fall, Geschlossen = abgeschlossen"),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_cases",
        verbose_name=_("Erstellt von"),
    )
    lead_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="led_cases",
        verbose_name=_("Fallverantwortlich"),
        help_text=_("Hauptverantwortliche Fachkraft für diesen Fall"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Erstellt am"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Aktualisiert am"))
    closed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Geschlossen am"),
    )

    class Meta:
        verbose_name = _("Fall")
        verbose_name_plural = _("Fälle")
        ordering = ["-created_at"]
        # CaseListView filtert facility + optional status, sortiert -created_at.
        # Refs #638.
        indexes = [
            models.Index(fields=["facility", "status", "-created_at"], name="case_facility_status_ca_idx"),
        ]

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"
