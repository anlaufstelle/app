"""Work items and deletion requests."""

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models.managers import FacilityScopedManager


class WorkItem(models.Model):
    """Work item or hint for the team."""

    class ItemType(models.TextChoices):
        HINT = "hint", _("Hinweis")
        TASK = "task", _("Aufgabe")

    class Status(models.TextChoices):
        OPEN = "open", _("Offen")
        IN_PROGRESS = "in_progress", _("In Bearbeitung")
        DONE = "done", _("Erledigt")
        DISMISSED = "dismissed", _("Verworfen")

    class Priority(models.TextChoices):
        NORMAL = "normal", _("Normal")
        IMPORTANT = "important", _("Wichtig")
        URGENT = "urgent", _("Dringend")

    class Recurrence(models.TextChoices):
        NONE = "none", _("Keine")
        WEEKLY = "weekly", _("Wöchentlich")
        MONTHLY = "monthly", _("Monatlich")
        QUARTERLY = "quarterly", _("Vierteljährlich")
        YEARLY = "yearly", _("Jährlich")

    objects = FacilityScopedManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    facility = models.ForeignKey(
        "core.Facility",
        on_delete=models.CASCADE,
        related_name="work_items",
        verbose_name=_("Einrichtung"),
    )
    client = models.ForeignKey(
        "core.Client",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_items",
        verbose_name=_("Klientel"),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="created_work_items",
        verbose_name=_("Erstellt von"),
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_work_items",
        verbose_name=_("Zugewiesen an"),
    )
    item_type = models.CharField(
        max_length=10,
        choices=ItemType.choices,
        default=ItemType.TASK,
        verbose_name=_("Typ"),
        help_text=_("Kategorisierung der Aufgabe (Aufgabe, Telefonat, Termin etc.)"),
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        verbose_name=_("Status"),
        help_text=_("Offen = noch nicht begonnen, In Bearbeitung = aktiv, Erledigt = abgeschlossen"),
    )
    priority = models.CharField(
        max_length=20,
        choices=Priority.choices,
        default=Priority.NORMAL,
        verbose_name=_("Priorität"),
        help_text=_("Dringend = sofortige Bearbeitung, Wichtig = zeitnah, Normal = regulär"),
    )
    title = models.CharField(max_length=200, verbose_name=_("Titel"))
    description = models.TextField(blank=True, verbose_name=_("Beschreibung"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Erstellt am"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Aktualisiert am"))
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Abgeschlossen am"),
    )
    due_date = models.DateField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("Zu erledigen bis"),
    )
    remind_at = models.DateField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("Wiedervorlage am"),
        help_text=_("Optional. Wann soll das Workitem wieder aufpoppen?"),
    )
    recurrence = models.CharField(
        max_length=20,
        choices=Recurrence.choices,
        default=Recurrence.NONE,
        verbose_name=_("Wiederholung"),
        help_text=_("Bei Erledigung wird automatisch eine Folgeaufgabe mit neuer Frist erstellt."),
    )

    class Meta:
        verbose_name = _("Arbeitsauftrag")
        verbose_name_plural = _("Arbeitsaufträge")
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class DeletionRequest(models.Model):
    """Deletion request (four-eyes principle)."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Ausstehend")
        APPROVED = "approved", _("Genehmigt")
        REJECTED = "rejected", _("Abgelehnt")

    class TargetType(models.TextChoices):
        EVENT = "Event", _("Event")

    objects = FacilityScopedManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    facility = models.ForeignKey(
        "core.Facility",
        on_delete=models.CASCADE,
        related_name="deletion_requests",
        verbose_name=_("Einrichtung"),
    )
    target_type = models.CharField(max_length=100, choices=TargetType.choices, verbose_name=_("Zieltyp"))
    target_id = models.UUIDField(verbose_name=_("Ziel-ID"))
    reason = models.TextField(verbose_name=_("Begründung"))
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name=_("Status"),
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="deletion_requests",
        verbose_name=_("Beantragt von"),
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_deletion_requests",
        verbose_name=_("Geprüft von"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Erstellt am"))
    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Geprüft am"),
    )

    class Meta:
        verbose_name = _("Löschantrag")
        verbose_name_plural = _("Löschanträge")
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(requested_by=models.F("reviewed_by")),
                name="deletion_request_different_reviewer",
            ),
            # #530: prevent duplicate PENDING requests for the same target.
            # Partial unique index — closed (APPROVED/REJECTED) records
            # remain unconstrained so re-requests after rejection are allowed.
            models.UniqueConstraint(
                fields=["facility", "target_type", "target_id"],
                condition=models.Q(status="pending"),
                name="unique_pending_deletion_request",
            ),
        ]

    def __str__(self):
        return f"Löschantrag {self.target_type} ({self.get_status_display()})"
