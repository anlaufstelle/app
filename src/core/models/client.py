"""Client -- pseudonymized contact person."""

import uuid

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models.managers import FacilityScopedManager
from core.models.mixins import SoftDeletableModel


class Client(SoftDeletableModel):
    """Pseudonymized client of a facility."""

    class ContactStage(models.TextChoices):
        IDENTIFIED = "identified", _("Identifiziert")
        QUALIFIED = "qualified", _("Qualifiziert")

    class AgeCluster(models.TextChoices):
        U18 = "u18", _("Unter 18")
        AGE_18_26 = "18_26", _("18–26")
        AGE_27_PLUS = "27_plus", _("27+")
        UNKNOWN = "unknown", _("Unbekannt")

    objects = FacilityScopedManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    facility = models.ForeignKey(
        "core.Facility",
        on_delete=models.CASCADE,
        related_name="clients",
        verbose_name=_("Einrichtung"),
    )
    pseudonym = models.CharField(
        max_length=100,
        db_index=True,
        verbose_name=_("Pseudonym"),
    )
    contact_stage = models.CharField(
        max_length=20,
        choices=ContactStage.choices,
        default=ContactStage.IDENTIFIED,
        verbose_name=_("Kontaktstufe"),
        help_text=_("Identifiziert = Pseudonym bekannt, Qualifiziert = vollständige Identität bekannt"),
    )
    age_cluster = models.CharField(
        max_length=20,
        choices=AgeCluster.choices,
        default=AgeCluster.UNKNOWN,
        verbose_name=_("Altersgruppe"),
        help_text=_("Altersgruppe des Klientel zum Zeitpunkt der Erfassung"),
    )
    notes = models.TextField(
        blank=True,
        verbose_name=_("Notizen"),
        help_text=_(
            "Interne Notizen, nur für Fachkräfte sichtbar. "
            "**Nicht feldverschlüsselt** — keine Klarnamen / Art-9-Daten "
            "(Gesundheit, Suchtdiagnosen, Klarname-Adresse) hier vermerken. "
            "Sensible Inhalte gehören in ein FieldTemplate mit Sensitivity=HOCH."
        ),
    )
    is_active = models.BooleanField(default=True, verbose_name=_("Aktiv"))
    k_anonymized = models.BooleanField(
        default=False,
        verbose_name=_("K-anonymisiert"),
        help_text=_(
            "Kennzeichnet, ob der Datensatz per K-Anonymisierung generalisiert wurde "
            "(Alternative zu Hard-Delete für Langzeit-Statistik)."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Erstellt am"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Aktualisiert am"))
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_clients",
        verbose_name=_("Erstellt von"),
    )

    class Meta:
        verbose_name = _("Klientel")
        verbose_name_plural = _("Klientel")
        ordering = ["pseudonym"]
        constraints = [
            models.UniqueConstraint(
                fields=["facility", "pseudonym"],
                name="unique_facility_pseudonym",
            ),
        ]
        indexes = [
            GinIndex(
                name="client_pseudonym_trgm_idx",
                fields=["pseudonym"],
                opclasses=["gin_trgm_ops"],
            ),
        ]

    def __str__(self):
        return self.pseudonym

    def anonymize(self, user=None):
        """DSGVO Art. 17 Aggregat-Anonymisierung — Wrapper auf Service.

        Logik liegt in :func:`core.services.clients.anonymize_client`
        (Refs #743 — Service-Layer-Trennung).
        """
        from core.services.clients import anonymize_client

        anonymize_client(self, user=user)

    def k_anonymize(self, k=5):
        """K-anonymize this client as an alternative to hard-delete (Refs #535).

        Generalizes identifying fields so the record stays statistically usable
        (age_cluster, contact_stage) while no longer being re-identifiable.
        This is additive to ``anonymize()`` and non-destructive to linked data.
        """
        from core.services.k_anonymization import k_anonymize_client

        k_anonymize_client(self, k=k)
