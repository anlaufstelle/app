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

    class Action(models.TextChoices):
        """Stable, PII-free renderer keys stored in :attr:`context`."""

        CLIENT_CREATED = "client_created", "client_created"
        CLIENT_QUALIFIED = "client_qualified", "client_qualified"
        CLIENT_UPDATED = "client_updated", "client_updated"
        CLIENT_TRASHED = "client_trashed", "client_trashed"
        CLIENT_MERGED = "client_merged", "client_merged"
        CLIENT_UNMERGED = "client_unmerged", "client_unmerged"
        EVENT_CREATED = "event_created", "event_created"
        EVENT_DELETED = "event_deleted", "event_deleted"
        WORKITEM_CREATED = "workitem_created", "workitem_created"
        WORKITEM_UPDATED = "workitem_updated", "workitem_updated"
        WORKITEM_COMPLETED = "workitem_completed", "workitem_completed"
        WORKITEM_REOPENED = "workitem_reopened", "workitem_reopened"
        WORKITEM_RECURRENCE_CREATED = "workitem_recurrence_created", "workitem_recurrence_created"
        CASE_CREATED = "case_created", "case_created"
        CASE_CLOSED = "case_closed", "case_closed"
        CASE_REOPENED = "case_reopened", "case_reopened"
        CASE_TRASHED = "case_trashed", "case_trashed"
        MILESTONE_DELETED = "milestone_deleted", "milestone_deleted"
        LEGACY_REDACTED = "legacy_redacted", "legacy_redacted"
        REDACTED = "redacted", "redacted"

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
    context = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Versionierter, personenbezugsfreier Kontext für die Anzeige."),
    )
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
        return f"{self.actor} {self.get_verb_display()} {self.target_type_id}:{self.target_id}"
