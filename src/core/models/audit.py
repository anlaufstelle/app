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
        LOGIN_UNLOCK = "login_unlock", _("Account-Sperre aufgehoben")
        VIEW_QUALIFIED = "view_qualified", _("Qualifizierte Daten eingesehen")
        EXPORT = "export", _("Export")
        DELETE = "delete", _("Löschung")
        STAGE_CHANGE = "stage_change", _("Stufenwechsel")
        SETTINGS_CHANGE = "settings_change", _("Einstellungen geändert")
        DOWNLOAD = "download", _("Download")
        LEGAL_HOLD = "legal_hold", _("Legal Hold")
        OFFLINE_KEY_FETCH = "offline_key_fetch", _("Offline-Schlüssel abgerufen")
        CLIENT_CREATE = "client_create", _("Klientel angelegt")
        CLIENT_UPDATE = "client_update", _("Klientel aktualisiert")
        CASE_CREATE = "case_create", _("Fall angelegt")
        CASE_UPDATE = "case_update", _("Fall aktualisiert")
        CASE_CLOSE = "case_close", _("Fall geschlossen")
        CASE_REOPEN = "case_reopen", _("Fall wiedereröffnet")
        MILESTONE_DELETE = "milestone_delete", _("Meilenstein gelöscht")
        EVENT_CREATE = "event_create", _("Ereignis angelegt")
        WORKITEM_CREATE = "workitem_create", _("Aufgabe angelegt")
        WORKITEM_UPDATE = "workitem_update", _("Aufgabe aktualisiert")
        USER_ROLE_CHANGED = "user_role_changed", _("Benutzerrolle geändert")
        USER_DEACTIVATED = "user_deactivated", _("Benutzer deaktiviert")
        PASSWORD_RESET_REQUESTED = "password_reset_requested", _("Passwort-Reset angefordert")
        SECURITY_VIOLATION = "security_violation", _("Sicherheitsverletzung")
        MFA_ENABLED = "mfa_enabled", _("2FA aktiviert")
        MFA_DISABLED = "mfa_disabled", _("2FA deaktiviert")
        MFA_FAILED = "mfa_failed", _("2FA-Verifikation fehlgeschlagen")
        BACKUP_CODES_GENERATED = "backup_codes_generated", _("2FA Backup-Codes generiert")
        BACKUP_CODES_USED = "backup_codes_used", _("2FA Backup-Code verwendet")
        BACKUP_CODES_REGENERATED = "backup_codes_regenerated", _("2FA Backup-Codes neu generiert")
        SUDO_MODE_ENTERED = "sudo_mode_entered", _("Sudo-Mode aktiviert (Re-Auth)")

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
        # Composite-Indexe für AuditLogListView-Filter (facility × timestamp)
        # und die häufigsten Zusatz-Filter action/user. Tabelle wächst
        # append-only, Index-Wartung ist günstig. Refs #638.
        indexes = [
            models.Index(fields=["facility", "-timestamp"], name="auditlog_facility_ts_idx"),
            models.Index(fields=["action", "-timestamp"], name="auditlog_action_ts_idx"),
            models.Index(fields=["user", "-timestamp"], name="auditlog_user_ts_idx"),
        ]

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
