"""Tracks recent client page visits per user for dashboard widget."""

from uuid import uuid4

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class RecentClientVisit(models.Model):
    """Records when a user last visited a client detail page."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="recent_client_visits",
    )
    client = models.ForeignKey(
        "core.Client",
        on_delete=models.CASCADE,
        related_name="recent_visits",
    )
    facility = models.ForeignKey(
        "core.Facility",
        on_delete=models.CASCADE,
    )
    visited_at = models.DateTimeField(auto_now=True)
    is_favorite = models.BooleanField(default=False)

    class Meta:
        unique_together = [("user", "client")]
        ordering = ["-visited_at"]
        indexes = [
            models.Index(fields=["user", "facility", "-visited_at"]),
        ]
        verbose_name = _("Klientel-Besuch")
        verbose_name_plural = _("Klientel-Besuche")

    def __str__(self):
        return f"RecentClientVisit({self.user}, {self.client})"
