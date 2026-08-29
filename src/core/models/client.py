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

    class RestrictionReason(models.TextChoices):
        """Die vier Tatbestaende des Art. 18 Abs. 1 DSGVO — bewusst als
        Werteliste statt als Freitext (Refs #2045).

        Der Grund der Einschraenkung ist rechtlich abschliessend aufgezaehlt
        (lit. a–d). Eine Werteliste haelt das Feld frei von Personenbezug (die
        strittige *Sache* steht verschluesselt am Berichtigungsantrag, nicht
        hier), macht die Einschraenkung filterbar und erspart dem Banner die
        Entscheidung, wie viel Freitext es einer nur lesenden Rolle zeigen
        darf. ``notes`` zeigt, wohin ein unverschluesseltes Freitextfeld an
        diesem Modell fuehrt — dort steht deshalb eine ausdrueckliche Warnung.
        """

        ACCURACY_DISPUTED = "accuracy_disputed", _("Richtigkeit bestritten (Art. 18 Abs. 1 lit. a)")
        UNLAWFUL_PROCESSING = "unlawful_processing", _("Verarbeitung unrechtmäßig, Löschung abgelehnt (lit. b)")
        LEGAL_CLAIMS = "legal_claims", _("Für Rechtsansprüche weiter benötigt (lit. c)")
        OBJECTION_PENDING = "objection_pending", _("Widerspruch in Prüfung (Art. 18 Abs. 1 lit. d)")

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
        # Refs #1862: Hier stand "jeder Zugriff darauf wird protokolliert" —
        # unzutreffend. ``VIEW_QUALIFIED`` entsteht projektweit nur im GET der
        # Personen-Detailseite (``views/clients.py``); das Bearbeiten-Formular
        # derselben Person zeigt dieselben Zusatzangaben entschluesselt und
        # schreibt nichts, Liste, Ereignis, Fall und Suchtreffer ebenfalls nicht.
        help_text=_(
            "Wie viel über die Person bekannt ist. Nur Pseudonym: es ist nur "
            "das Pseudonym hinterlegt. Mit Zusatzangaben: zusätzliche "
            "persönliche Angaben sind hinterlegt; das Öffnen der Detailseite "
            "wird protokolliert."
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
    # Refs #1876 (ADR-045): Tombstone-Verweis des Duplikat-Merge. Gesetzt nur
    # auf der QUELLE einer Zusammenfuehrung, in derselben Transaktion wie ihr
    # Soft-Delete. PROTECT statt SET_NULL, begruendet am Retention-Verhalten:
    # der Retention-Lauf REDIGIERT Personen (``anonymize_client``), er loescht
    # keine Zeilen — PROTECT kostet dort also nie etwas, verhindert aber, dass
    # ein kuenftiger Hard-Delete-Pfad den einzigen Nachweis "wohin sind die
    # Daten dieser Person gewandert" (Art. 15) still kappt. Der Verweis
    # ueberlebt die Redaktion des Tombstones bewusst.
    merged_into = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="merged_sources",
        verbose_name=_("Zusammengeführt in"),
    )
    is_active = models.BooleanField(default=True, verbose_name=_("Aktiv"))
    # Refs #2013: Terminal-Marker der Art.-17-Tilgung — Paritaet zu
    # ``Case.anonymized_at``. ``anonymize_client`` aendert ``deleted_at``
    # nicht, ein Tombstone erfuellt ``is_deleted=True, deleted_at<cutoff``
    # also dauerhaft weiter. Ohne diesen Stempel selektierte der naechtliche
    # ``anonymize_eligible_soft_deleted_clients`` alle jemals Anonymisierten in
    # JEDEM Lauf erneut: pro Tombstone und Nacht eine weitere, HMAC-verkettete
    # ``CLIENT_ANONYMIZED``-Zeile im append-only AuditLog (unbegrenztes
    # Wachstum), ein dauerhaft von Null verschiedenes „Anonymisiert N"-Signal
    # im Ops-Log und Ban-Revision-Churn. ``is_active=False`` taugt nicht als
    # Ersatz: die K-Anon-Bridge setzt es OHNE Papierkorb, und es datiert nichts.
    anonymized_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Anonymisiert am"),
    )
    # Refs #2045: Art.-18-Sperrvermerk. NICHT der Legal Hold — der blockiert die
    # Loeschung (Art. 17 Abs. 3), dieses Flag blockiert die *Nutzung*: nach
    # Art. 18 Abs. 2 duerfen eingeschraenkte Daten weiter GESPEICHERT werden,
    # aber nicht mehr inhaltlich verarbeitet. Das Gate ist deshalb ein
    # Schreib-, kein Loeschgate (:mod:`core.services.client.restriction`) und
    # spiegelt ``resolve_writable_client``; einzige Ausnahme ist der
    # Berichtigungsworkflow (ADR-041), ueber den die bestrittene Richtigkeit
    # ueberhaupt erst geklaert wird.
    processing_restricted = models.BooleanField(
        default=False,
        verbose_name=_("Verarbeitung eingeschränkt"),
        help_text=_(
            "Art. 18 DSGVO: Die Daten bleiben gespeichert, dürfen aber außerhalb "
            "des Berichtigungsverfahrens nicht mehr geändert oder verwendet werden."
        ),
    )
    processing_restriction_reason = models.CharField(
        max_length=30,
        choices=RestrictionReason.choices,
        blank=True,
        default="",
        verbose_name=_("Grund der Einschränkung"),
    )
    processing_restricted_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Eingeschränkt am"),
    )
    processing_restricted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        # SET_NULL wie ``created_by``: die Provenienz ist ein Nachweis, kein
        # Grund, eine Nutzer-Loeschung zu blockieren. Faellt sie weg, greift die
        # Vier-Augen-Regel der Aufhebung konservativ weiter — ``None`` ist nie
        # gleich der aufhebenden Person, die Aufhebung durch eine einzelne
        # Fachkraft bleibt also moeglich, und die Leitung kann ohnehin.
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="restricted_clients",
        verbose_name=_("Eingeschränkt durch"),
    )
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
