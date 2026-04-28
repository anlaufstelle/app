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
    allowed_file_types = models.CharField(
        max_length=500,
        blank=True,
        default="pdf,jpg,jpeg,png,docx",
        verbose_name=_("Erlaubte Dateitypen"),
        help_text=_("Kommagetrennte Dateiendungen (z.B. pdf,jpg,png,docx)"),
    )
    max_file_size_mb = models.PositiveIntegerField(
        default=10,
        verbose_name=_("Max. Dateigröße (MB)"),
    )
    mfa_enforced_facility_wide = models.BooleanField(
        default=False,
        verbose_name=_("2FA-Pflicht für alle Nutzer"),
        help_text=_(
            "Wenn aktiv, gilt MFA-Zwang (TOTP) automatisch für alle Nutzer dieser "
            "Einrichtung — unabhängig vom einzelnen Benutzerfeld mfa_required."
        ),
    )
    retention_auto_approve_after_defer = models.BooleanField(
        default=False,
        verbose_name=_("Auto-Freigabe nach mehrfacher Zurückstellung"),
        help_text=_(
            "Wenn aktiv, werden Löschvorschläge nach Überschreiten der maximalen "
            "Zurückstellungszahl automatisch freigegeben, statt manuelle Entscheidung "
            "zu erzwingen."
        ),
    )
    retention_max_defer_count = models.PositiveIntegerField(
        default=2,
        verbose_name=_("Maximale Zurückstellungen"),
        help_text=_(
            "Nach wie vielen Zurückstellungen eine endgültige Entscheidung "
            "erzwungen oder (bei Auto-Freigabe) automatisch freigegeben wird."
        ),
    )
    retention_use_k_anonymization = models.BooleanField(
        default=False,
        verbose_name=_("K-Anonymisierung statt Hard-Delete"),
        help_text=_(
            "Wenn aktiv, wird bei DSGVO-Löschung statt Hard-Delete eine "
            "K-Anonymisierung durchgeführt. Der Datensatz bleibt statistisch "
            "auswertbar, ist aber nicht mehr re-identifizierbar."
        ),
    )
    k_anonymity_threshold = models.PositiveIntegerField(
        default=5,
        verbose_name=_("K-Anonymitäts-Schwelle"),
        help_text=_(
            "Mindest-Gruppengröße für K-Anonymität (z.B. k=5 → mindestens 5 "
            "Datensätze pro Bucket). Je höher der Wert, desto stärker die "
            "Anonymisierung."
        ),
    )

    class Meta:
        verbose_name = _("Einstellungen")
        verbose_name_plural = _("Einstellungen")

    def __str__(self):
        return f"Einstellungen: {self.facility.name}"
