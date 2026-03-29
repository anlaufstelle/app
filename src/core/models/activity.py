"""Activity feed -- operational log of system actions."""

import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models.managers import FacilityScopedManager


class Activity(models.Model):
    """An operational activity entry (client created, task completed, etc.)."""

    objects = FacilityScopedManager()

    class Verb(models.TextChoices):
        CREATED = "created", _("erstellt")
        UPDATED = "updated", _("aktualisiert")
        DELETED = "deleted", _("gelöscht")
        QUALIFIED = "qualified", _("qualifiziert")
        COMPLETED = "completed", _("erledigt")
        REOPENED = "reopened", _("wiedereröffnet")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    facility = models.ForeignKey(
        "core.Facility",
        on_delete=models.CASCADE,
        related_name="activities",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activities",
    )
    verb = models.CharField(max_length=32, choices=Verb.choices)
    target_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
    )
    target_id = models.UUIDField()
    target = GenericForeignKey("target_type", "target_id")
    summary = models.CharField(max_length=255, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["facility", "-occurred_at"]),
            models.Index(fields=["target_type", "target_id"]),
        ]
        verbose_name = _("Aktivität")
        verbose_name_plural = _("Aktivitäten")

    def __str__(self):
        return f"{self.actor} {self.get_verb_display()} {self.summary}"
