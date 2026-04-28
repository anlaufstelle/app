"""Dashboard widget preferences per user."""

from uuid import uuid4

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class DashboardPreference(models.Model):
    """Stores per-user dashboard widget visibility preferences."""

    DEFAULT_WIDGETS = {
        "tasks": True,
        "activity": True,
        "recent_clients": True,
        "stats": True,
    }

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="dashboard_preference",
    )
    widgets = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Dashboard-Einstellung")
        verbose_name_plural = _("Dashboard-Einstellungen")

    def __str__(self):
        return f"DashboardPreference({self.user})"

    def get_widget_config(self):
        """Return widget config with defaults for missing keys."""
        config = dict(self.DEFAULT_WIDGETS)
        config.update(self.widgets)
        return config
