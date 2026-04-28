"""Client -- pseudonymized contact person."""

import uuid

from django.conf import settings
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
        help_text=_("Interne Notizen, nur für Fachkräfte sichtbar"),
    )
    is_active = models.BooleanField(default=True, verbose_name=_("Aktiv"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Erstellt am"))
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

    def __str__(self):
        return self.pseudonym

    def anonymize(self):
        """Anonymize the client (GDPR-compliant): personal data is deleted, the record is kept
        for statistical purposes. Events and cases remain linked via SET_NULL.

        Anonymization covers (Refs #529):
        - Client fields (pseudonym, notes, age_cluster, is_active)
        - Case.title / Case.description of all linked cases
        - Episode.title / Episode.description of all episodes of those cases
        - Workitems in every status (not only open/in_progress), title and description
        """
        from django.db import transaction

        from core.models.case import Case
        from core.models.episode import Episode

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
