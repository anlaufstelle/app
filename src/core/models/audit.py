"""GDPR-compliant audit logging."""

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models.managers import FacilityScopedManager


class AuditLog(models.Model):
    """Log entry for security-relevant actions."""

    class Action(models.TextChoices):
        LOGIN = "login", _("Anmeldung")
        LOGOUT = "logout", _("Abmeldung")
        LOGIN_FAILED = "login_failed", _("Anmeldung fehlgeschlagen")
        VIEW_QUALIFIED = "view_qualified", _("Qualifizierte Daten eingesehen")
        EXPORT = "export", _("Export")
        DELETE = "delete", _("Löschung")
        STAGE_CHANGE = "stage_change", _("Stufenwechsel")
        SETTINGS_CHANGE = "settings_change", _("Einstellungen geändert")
        DOWNLOAD = "download", _("Download")
        LEGAL_HOLD = "legal_hold", _("Legal Hold")
        OFFLINE_KEY_FETCH = "offline_key_fetch", _("Offline-Schlüssel abgerufen")
        CLIENT_UPDATE = "client_update", _("Klientel aktualisiert")
        CASE_UPDATE = "case_update", _("Fall aktualisiert")
        WORKITEM_UPDATE = "workitem_update", _("Aufgabe aktualisiert")

    objects = FacilityScopedManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    facility = models.ForeignKey(
        "core.Facility",
        on_delete=models.CASCADE,
        related_name="audit_logs",
        null=True,
        blank=True,
        verbose_name=_("Einrichtung"),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
        verbose_name=_("Benutzer"),
    )
    action = models.CharField(
        max_length=30,
        choices=Action.choices,
        verbose_name=_("Aktion"),
    )
    target_type = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("Zieltyp"),
    )
    target_id = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("Ziel-ID"),
    )
    detail = models.JSONField(default=dict, blank=True, verbose_name=_("Details"))
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        verbose_name=_("IP-Adresse"),
    )
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name=_("Zeitstempel"))

    class Meta:
        verbose_name = _("Audit-Log")
        verbose_name_plural = _("Audit-Logs")
        ordering = ["-timestamp"]

    def save(self, *args, **kwargs):
        """Prevent updates — only inserts are allowed."""
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("AuditLog entries are append-only and cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Prevent deletion of audit log entries."""
        raise ValueError("AuditLog entries are append-only and cannot be deleted.")

    def __str__(self):
        return f"{self.get_action_display()} – {self.user} ({self.timestamp:%d.%m.%Y %H:%M})"
