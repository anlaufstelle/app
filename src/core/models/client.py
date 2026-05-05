"""Client -- pseudonymized contact person."""

import uuid

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models.managers import FacilityScopedManager


class Client(models.Model):
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

    def anonymize(self):
        """Anonymize the client (GDPR-compliant): personal data is deleted, the record is kept
        for statistical purposes. Events and cases remain linked via SET_NULL.

        Anonymization covers (Refs #529, #715):
        - Client fields (pseudonym, notes, age_cluster, is_active)
        - Case.title / Case.description of all linked cases
        - Episode.title / Episode.description of all episodes of those cases
        - Workitems in every status (not only open/in_progress), title and description
        - **EventHistory** für alle Events dieses Klienten: ``data_before``/
          ``data_after`` auf den redaktierten Marker zurueckgesetzt — die
          append-only-Trigger werden transaktional umgangen (analog T714).
          Sonst lebt der Klartext der Aenderungs-History weiter und macht
          Re-Identifikation moeglich.
        - **EventAttachment** für alle Events dieses Klienten: physisch
          loeschen (Disk-Files + DB-Zeilen) via ``delete_event_attachments``.
          Verschluesselte Anhaenge bleiben sonst auf dem Volume liegen.
        - **DeletionRequest** für alle Events dieses Klienten: Freitext
          ``reason`` redaktieren, Antrags-Meta (status/requested_by/etc.)
          fuer den Audit-Trail erhalten.
        """
        from django.db import connection, transaction

        from core.models.case import Case
        from core.models.episode import Episode
        from core.models.event import Event
        from core.models.event_history import EventHistory
        from core.models.workitem import DeletionRequest

        with transaction.atomic():
            self.pseudonym = f"Gelöscht-{str(self.pk)[:8]}"
            self.notes = ""
            self.age_cluster = self.AgeCluster.UNKNOWN
            self.is_active = False
            self.save(update_fields=["pseudonym", "notes", "age_cluster", "is_active"])

            # Anonymize cases of this client — keep created_at for chronological context.
            cases = Case.objects.filter(client=self)
            case_ids = list(cases.values_list("pk", flat=True))
            for case in cases:
                case.title = f"[Anonymisiert {case.created_at:%Y-%m-%d}]"
                case.description = ""
                case.save(update_fields=["title", "description"])

            # Anonymize episodes of those cases.
            Episode.objects.filter(case_id__in=case_ids).update(
                title="Episode (anonymisiert)",
                description="",
            )

            # Anonymize ALL work items (also DONE/DISMISSED), not only open/in_progress.
            self.work_items.all().update(
                title="Aufgabe (anonymisiert)",
                description="",
            )

            # Refs #715: Aggregat-Anonymisierung der Event-abhaengigen
            # Tabellen. Events selbst bleiben (statistik-relevant) — nur
            # die *Spuren* werden saniert.
            event_ids = list(Event.objects.filter(client=self).values_list("pk", flat=True))
            if event_ids:
                # 1. EventAttachments (Disk + DB)
                for event in Event.objects.filter(pk__in=event_ids).iterator():
                    from core.services.file_vault import delete_event_attachments

                    delete_event_attachments(event)

                # 2. EventHistory redaktieren — append-only-Trigger transaktional
                # umgehen. ``ALTER TABLE DISABLE TRIGGER`` waere blockiert,
                # weil die vorigen Writes (Case/Episode/Workitem/Attachment-
                # Deletes) Trigger-Events queue'n. ``session_replication_role
                # = replica`` ist session-lokal + atomar mit der laufenden
                # Transaktion — Reset auf 'origin' im finally garantiert,
                # dass der Schutz auch nach Fehlern wieder greift.
                redacted_marker = {"_redacted": True, "anonymized": True}
                history_qs = EventHistory.objects.filter(event_id__in=event_ids)
                if history_qs.exists():
                    if connection.vendor == "postgresql":
                        with connection.cursor() as cur:
                            cur.execute("SET LOCAL session_replication_role = replica")
                            try:
                                history_qs.update(
                                    data_before=redacted_marker,
                                    data_after=redacted_marker,
                                )
                            finally:
                                cur.execute("SET LOCAL session_replication_role = origin")
                    else:
                        history_qs.update(
                            data_before=redacted_marker,
                            data_after=redacted_marker,
                        )

                # 3. DeletionRequest.reason redaktieren — Antrag bleibt
                # fuer den 4-Augen-Audit-Trail erhalten.
                DeletionRequest.objects.filter(
                    target_type="Event",
                    target_id__in=event_ids,
                ).update(reason="[Anonymisiert]")

    def k_anonymize(self, k=5):
        """K-anonymize this client as an alternative to hard-delete (Refs #535).

        Generalizes identifying fields so the record stays statistically usable
        (age_cluster, contact_stage) while no longer being re-identifiable.
        This is additive to ``anonymize()`` and non-destructive to linked data.
        """
        from core.services.k_anonymization import k_anonymize_client

        k_anonymize_client(self, k=k)
