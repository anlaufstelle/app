"""Episode -- distinct phase within a case."""

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class Episode(models.Model):
    """Abgrenzbare Phase innerhalb eines Falls."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(
        "core.Case",
        on_delete=models.CASCADE,
        related_name="episodes",
        verbose_name=_("Fall"),
    )
    title = models.CharField(max_length=200, verbose_name=_("Titel"))
    description = models.TextField(blank=True, verbose_name=_("Beschreibung"))
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
