"""Case -- groups events for a client."""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models.managers import FacilityScopedManager
from core.models.mixins import DynamicDataEncryptionMixin, SoftDeletableModel


class Case(DynamicDataEncryptionMixin, SoftDeletableModel):
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
        verbose_name=_("Person"),
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
            "Frei-Text zum Fall. Dieses Feld wird nicht verschlüsselt "
            "gespeichert — bitte keine Klarnamen und keine besonders "
            "schützenswerten Angaben zu Gesundheit, Sucht, Herkunft, Religion "
            "oder Sexualität (besondere Kategorien personenbezogener Daten, "
            "Art. 9 DSGVO). Solche Angaben gehören in ein Zusatzfeld eines "
            "Kontakts mit der Sensibilität „Hoch“."
        ),
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        verbose_name=_("Status"),
        help_text=_("Offen = aktiver Fall, Geschlossen = abgeschlossen"),
    )
    # M2/P5 (Refs #1058, Entscheid E4): Faelle bekommen dieselbe dynamische
    # Feldmechanik wie Personen/Ereignisse. ``null=True`` weil der Bestand (und
    # jeder Fall ohne Dokumentationstyp) typlos bleibt; ``PROTECT`` schuetzt
    # befuellte ``data_json``-Werte davor, dass ein Loeschen des Typs ihre
    # Feld-Semantik (Slug -> Label/Sensitivity/Verschluesselung) unbemerkt
    # entwertet — der Anonymisierungs-Pfad (``_redact_cases_and_episodes``)
    # loest den Verweis daher aktiv auf, statt anonymisierte Bestaende
    # dauerhaft an den Typ zu ketten.
    document_type = models.ForeignKey(
        "core.DocumentType",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cases",
        verbose_name=_("Dokumentationstyp"),
    )
    data_json = models.JSONField(default=dict, blank=True, verbose_name=_("Daten (JSON)"))
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
    # Refs #2524: business activity must survive feed pruning and key rotation.
    last_activity_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Letzte Fallaktivität"),
    )
    closed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Geschlossen am"),
    )
    # Refs #1818 (ADR-044 §3): Zustandsmerkmal der endgueltigen Tilgung. Der
    # Papierkorb-Fall traegt ``is_deleted=True``; nach der Redaktion traegt er
    # zusaetzlich diesen Zeitstempel. Ohne ihn waere „im Papierkorb" von
    # „endgueltig getilgt" nur am Titel-Praefix zu ahnen — die Person hat dafuer
    # ihr ``is_active=False``, der Fall hatte nichts. Drei Stellen lesen ihn:
    # der naechtliche Fristablauf (sonst redigiert er denselben Fall in JEDEM
    # Lauf erneut und schreibt jedes Mal eine neue ``CASE_ANONYMIZED``-Zeile in
    # das append-only AuditLog), ``restore_case`` (eine inhaltsleere Huelle darf
    # nicht in Liste, Detail und Statistik zurueckkehren) und die
    # Papierkorb-Liste, die beide Zustaende unterscheiden muss.
    anonymized_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Anonymisiert am"),
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

    def save(self, *args, **kwargs):
        from core.services.dynamic_protection import dynamic_data_write

        with dynamic_data_write(self, kwargs.get("update_fields")):
            if kwargs.get("update_fields") is None or "data_json" in kwargs["update_fields"]:
                self._encrypt_sensitive_fields()
            super().save(*args, **kwargs)

    def create_episode(self, *, user, title, description="", started_at=None, ended_at=None):
        """Create an episode for an open case, including its optional end date."""
        from core.models.episode import Episode

        if self.status != self.Status.OPEN:
            raise ValueError(_("Episoden können nur für offene Fälle erstellt werden."))
        return Episode.objects.create(
            case=self,
            title=title,
            description=description,
            started_at=started_at or timezone.now().date(),
            ended_at=ended_at,
            created_by=user,
        )
