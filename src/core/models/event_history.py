"""Change history for events."""

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class EventHistory(models.Model):
    """Append-only change log for an event.

    Records are immutable once created. Updates and deletions are prevented
    at the application level via overridden save() and delete() methods.
    """

    class Action(models.TextChoices):
        CREATE = "create", _("Erstellt")
        UPDATE = "update", _("Aktualisiert")
        DELETE = "delete", _("Gelöscht")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(
        "core.Event",
        on_delete=models.CASCADE,
        related_name="history",
        verbose_name=_("Ereignis"),
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="event_changes",
        verbose_name=_("Geändert von"),
    )
    changed_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Geändert am"))
    action = models.CharField(
        max_length=10,
        choices=Action.choices,
        verbose_name=_("Aktion"),
    )
    data_before = models.JSONField(null=True, blank=True, verbose_name=_("Daten vorher"))
    data_after = models.JSONField(null=True, blank=True, verbose_name=_("Daten nachher"))

    class Meta:
        verbose_name = _("Ereignis-Historie")
        verbose_name_plural = _("Ereignis-Historien")
        ordering = ["-changed_at"]

    def save(self, *args, **kwargs):
        """Prevent updates — only inserts are allowed."""
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("EventHistory records are append-only and cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Prevent deletion of history records."""
        raise ValueError("EventHistory records are append-only and cannot be deleted.")

    def __str__(self):
        return f"{self.event} – {self.get_action_display()} ({self.changed_at:%d.%m.%Y %H:%M})"
