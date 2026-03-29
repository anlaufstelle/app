"""Facility settings (singleton per facility)."""

from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models.managers import FacilityScopedManager


class Settings(models.Model):
    """Configuration for a facility (1:1 with Facility)."""

    objects = FacilityScopedManager()

    facility = models.OneToOneField(
        "core.Facility",
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="settings",
        verbose_name=_("Einrichtung"),
    )
    facility_full_name = models.CharField(
        max_length=300,
        blank=True,
        verbose_name=_("Vollständiger Name"),
    )
    default_document_type = models.ForeignKey(
        "core.DocumentType",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("Standard-Dokumentationstyp"),
    )
    session_timeout_minutes = models.IntegerField(
        default=30,
        verbose_name=_("Session-Timeout (Minuten)"),
    )
    retention_anonymous_days = models.IntegerField(
        default=90,
        verbose_name=_("Aufbewahrung anonym (Tage)"),
    )
    retention_identified_days = models.IntegerField(
        default=365,
        verbose_name=_("Aufbewahrung identifiziert (Tage)"),
    )
    retention_qualified_days = models.IntegerField(
        default=3650,
        verbose_name=_("Aufbewahrung qualifiziert (Tage)"),
    )
    retention_activities_days = models.IntegerField(
        default=365,
        verbose_name=_("Aufbewahrung Aktivitäten (Tage)"),
    )

    class Meta:
        verbose_name = _("Einstellungen")
        verbose_name_plural = _("Einstellungen")

    def __str__(self):
        return f"Einstellungen: {self.facility.name}"
