"""OutcomeGoal and Milestone models for case goal tracking."""

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class OutcomeGoal(models.Model):
    """Wirkungsziel — was durch die Arbeit erreicht werden soll."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey("core.Case", on_delete=models.CASCADE, related_name="goals")
    title = models.CharField(max_length=200, verbose_name=_("Titel"))
    description = models.TextField(blank=True, verbose_name=_("Beschreibung"))
    is_achieved = models.BooleanField(default=False, verbose_name=_("Erreicht"))
    achieved_at = models.DateField(null=True, blank=True, verbose_name=_("Erreicht am"))
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_goals",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Wirkungsziel")
        verbose_name_plural = _("Wirkungsziele")
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class Milestone(models.Model):
    """Meilenstein — konkreter Schritt zum Wirkungsziel."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    goal = models.ForeignKey(OutcomeGoal, on_delete=models.CASCADE, related_name="milestones")
    title = models.CharField(max_length=200, verbose_name=_("Titel"))
    is_completed = models.BooleanField(default=False, verbose_name=_("Abgeschlossen"))
    completed_at = models.DateField(null=True, blank=True, verbose_name=_("Abgeschlossen am"))
    sort_order = models.PositiveIntegerField(default=0, verbose_name=_("Reihenfolge"))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Meilenstein")
        verbose_name_plural = _("Meilensteine")
        ordering = ["sort_order"]

    def __str__(self):
        return self.title
