"""Client -- pseudonymized contact person."""

import uuid

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models.managers import FacilityScopedManager
from core.models.mixins import DynamicDataEncryptionMixin, SoftDeletableModel


class Client(DynamicDataEncryptionMixin, SoftDeletableModel):
    """Pseudonymized client of a facility."""

    class ContactStage(models.TextChoices):
        # Refs #1805: lebensweltliche Labels statt der Verwaltungsvokabel
        # "Identifiziert"/"Qualifiziert" (Leitprinzip 3 der Sprachleitlinie:
        # Datenstufe != Personenmerkmal). Die WERTE bleiben unveraendert —
        # Art.-15-/Art.-20-Export, CSV-Import und API sprechen weiter
        # "identified"/"qualified". Guard: tests/test_terminology_guard.py.
        IDENTIFIED = "identified", _("Nur Pseudonym")
        QUALIFIED = "qualified", _("Mit Zusatzangaben")

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
        help_text=_(
            "Wie viel über die Person bekannt ist. Nur Pseudonym: es ist nur "
            "das Pseudonym hinterlegt. Mit Zusatzangaben: zusätzliche "
            "persönliche Angaben sind hinterlegt; jeder Zugriff darauf wird "
            "protokolliert."
        ),
    )
    age_cluster = models.CharField(
        max_length=20,
        choices=AgeCluster.choices,
        default=AgeCluster.UNKNOWN,
        verbose_name=_("Altersgruppe"),
        help_text=_("Altersgruppe der Person zum Zeitpunkt der Erfassung"),
    )
    notes = models.TextField(
        blank=True,
        verbose_name=_("Notizen"),
        help_text=_(
            "Interne Notizen, nur für Fachkräfte sichtbar. Dieses Feld wird "
            "nicht verschlüsselt gespeichert — bitte hier keine Klarnamen "
            "notieren und keine besonders schützenswerten Angaben zu "
            "Gesundheit, Sucht, Herkunft, Religion oder Sexualität (besondere "
            "Kategorien personenbezogener Daten, Art. 9 DSGVO). Solche Angaben "
            "gehören in ein Zusatzfeld mit der Sensibilität „Hoch“; das wird "
            "verschlüsselt gespeichert. Einrichten kann das die "
            "Anwendungsbetreuung."
        ),
    )
    # M2/P4b (Refs #1058, Entscheid E4): Personen bekommen dieselbe dynamische
    # Feldmechanik wie Ereignisse. ``null=True`` weil der Bestand (und jede
    # Person ohne Profil-Typ) typlos bleibt; ``PROTECT`` schuetzt befuellte
    # ``data_json``-Werte davor, dass ein Loeschen des Typs ihre Feld-Semantik
    # (Slug -> Label/Sensitivity/Verschluesselung) unbemerkt entwertet — der
    # Anonymisierungs-Pfad loest den Verweis daher aktiv auf, statt
    # anonymisierte Bestaende dauerhaft an den Typ zu ketten.
    document_type = models.ForeignKey(
        "core.DocumentType",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="clients",
        verbose_name=_("Dokumentationstyp"),
    )
    data_json = models.JSONField(default=dict, blank=True, verbose_name=_("Daten (JSON)"))
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
        verbose_name = _("Person")
        verbose_name_plural = _("Personen")
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

    def save(self, *args, **kwargs):
        # E5: Der Mixin ueberschreibt ``save()`` bewusst NICHT (MRO-Kollision mit
        # ``SoftDeletableModel``) — jedes Modell ruft die Verschluesselung explizit
        # auf, direkt vor dem Persistieren. Muster: ``Event.save()``.
        self._encrypt_sensitive_fields()
        super().save(*args, **kwargs)

    def anonymize(self, user=None):
        """DSGVO Art. 17 Aggregat-Anonymisierung — Wrapper auf Service.

        Logik liegt in :func:`core.services.client.main.anonymize_client`
        (Refs #743 — Service-Layer-Trennung).
        """
        from core.services.client import anonymize_client

        anonymize_client(self, user=user)

    def k_anonymize(self, k=5):
        """K-anonymize this client as an alternative to hard-delete (Refs #535).

        Generalizes identifying fields so the record stays statistically usable
        (age_cluster, contact_stage) while no longer being re-identifiable.
        This is additive to ``anonymize()`` and non-destructive to linked data.
        """
        from core.services.compliance import k_anonymize_client

        k_anonymize_client(self, k=k)
