"""Episode -- distinct phase within a case."""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models.managers import FacilityScopedQuerySet
from core.models.mixins import DynamicDataEncryptionMixin, SoftDeletableModel


class EpisodeQuerySet(FacilityScopedQuerySet):
    """Scope episodes through their owning case without duplicating the facility."""

    def for_facility(self, facility):
        return self.filter(case__facility=facility)


class Episode(DynamicDataEncryptionMixin, SoftDeletableModel):
    """Abgrenzbare Phase innerhalb eines Falls."""

    objects = EpisodeQuerySet.as_manager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(
        "core.Case",
        on_delete=models.CASCADE,
        related_name="episodes",
        verbose_name=_("Fall"),
    )
    title = models.CharField(max_length=200, verbose_name=_("Titel"))
    description = models.TextField(
        blank=True,
        verbose_name=_("Beschreibung"),
        help_text=_(
            "Frei-Text zur Episode. Dieses Feld wird nicht verschlüsselt "
            "gespeichert — bitte keine Klarnamen und keine besonders "
            "schützenswerten Angaben zu Gesundheit, Sucht, Herkunft, Religion "
            "oder Sexualität (besondere Kategorien personenbezogener Daten, "
            "Art. 9 DSGVO)."
        ),
    )
    started_at = models.DateField(verbose_name=_("Beginn"))
    ended_at = models.DateField(null=True, blank=True, verbose_name=_("Ende"))
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_episodes",
        verbose_name=_("Erstellt von"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Erstellt am"))

    dynamic_entity_type = "episode"

    document_type = models.ForeignKey(
        "core.DocumentType",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="episodes",
        verbose_name=_("Dokumentationstyp"),
    )
    data_json = models.JSONField(default=dict, blank=True, verbose_name=_("Daten (JSON)"))

    class Meta:
        verbose_name = _("Episode")
        verbose_name_plural = _("Episoden")
        ordering = ["-started_at"]

    def save(self, *args, **kwargs):
        from core.services.dynamic_protection import dynamic_data_write

        with dynamic_data_write(self, kwargs.get("update_fields")):
            self._validate_dynamic_document_type()
            if kwargs.get("update_fields") is None or "data_json" in kwargs["update_fields"]:
                self._encrypt_sensitive_fields()
            super().save(*args, **kwargs)

    @property
    def facility_id(self):
        return self.case.facility_id

    @property
    def facility(self):
        return self.case.facility

    def __str__(self):
        return self.title

    def close(self, ended_at=None):
        """Close once, rejecting an end before the existing start."""
        from core.services.case.episodes import validate_episode_dates

        if self.ended_at is not None:
            return self
        end = ended_at or timezone.localdate()
        validate_episode_dates(self.started_at, end)
        self.ended_at = end
        self.save(update_fields=["ended_at"])
        return self
