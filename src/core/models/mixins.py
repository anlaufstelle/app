"""Reusable abstract model mixins."""

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class SoftDeletableModel(models.Model):
    """Abstract base for aggregates that support reversible soft-delete.

    Provides ``is_deleted``, ``deleted_at``, ``deleted_by`` plus
    ``soft_delete()`` and ``restore()`` convenience methods. Combine with
    :class:`core.models.managers.SoftDeletableFacilityScopedManager` to
    filter soft-deleted rows out of the default queryset.
    """

    is_deleted = models.BooleanField(default=False, verbose_name=_("Gelöscht"))
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Gelöscht am"),
    )
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("Gelöscht von"),
    )

    class Meta:
        abstract = True

    def soft_delete(self, user=None):
        """Mark this object as deleted; preserve in DB for audit/restore."""
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.deleted_by = user
        self.save(update_fields=["is_deleted", "deleted_at", "deleted_by"])

    def restore(self):
        """Reverse a soft-delete."""
        self.is_deleted = False
        self.deleted_at = None
        self.deleted_by = None
        self.save(update_fields=["is_deleted", "deleted_at", "deleted_by"])
