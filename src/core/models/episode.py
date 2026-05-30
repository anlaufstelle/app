"""Episode -- distinct phase within a case."""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models.mixins import SoftDeletableModel


class Episode(SoftDeletableModel):
    """Abgrenzbare Phase innerhalb eines Falls."""

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
            "Frei-Text-Beschreibung der Episode. Nicht feldverschlüsselt — "
            "keine Klarnamen oder Art-9-Daten hier vermerken."
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

    class Meta:
        verbose_name = _("Episode")
        verbose_name_plural = _("Episoden")
        ordering = ["-started_at"]

    def __str__(self):
        return self.title

    def close(self, ended_at=None):
        """Set ``ended_at`` and persist. Idempotent — no-op if already closed.

        Refs #958 — ersetzt den frueheren ``services/episodes.close_episode``-
        Service-Aufruf.
        """
        if self.ended_at is not None:
            return self
        self.ended_at = ended_at or timezone.now().date()
        self.save(update_fields=["ended_at"])
        return self
