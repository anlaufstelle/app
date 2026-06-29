"""GDPR-compliant audit logging."""

import uuid

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone
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
        CLIENT_CREATE = "client_create", _("Person angelegt")
        CLIENT_UPDATE = "client_update", _("Person aktualisiert")
        CLIENT_SOFT_DELETED = "client_soft_deleted", _("Person in Papierkorb")
        CLIENT_RESTORED = "client_restored", _("Person wiederhergestellt")
        CLIENT_ANONYMIZED = "client_anonymized", _("Person anonymisiert")
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
        DELETION_CONFIRMER_CHANGED = "deletion_confirmer_changed", _("Recht Löschbestätigung geändert")
        PASSWORD_RESET_REQUESTED = "password_reset_requested", _("Passwort-Reset angefordert")
        SECURITY_VIOLATION = "security_violation", _("Sicherheitsverletzung")
        MFA_ENABLED = "mfa_enabled", _("2FA aktiviert")
        MFA_DISABLED = "mfa_disabled", _("2FA deaktiviert")
        MFA_FAILED = "mfa_failed", _("2FA-Verifikation fehlgeschlagen")
        BACKUP_CODES_GENERATED = "backup_codes_generated", _("2FA Backup-Codes generiert")
        BACKUP_CODES_USED = "backup_codes_used", _("2FA Backup-Code verwendet")
        BACKUP_CODES_REGENERATED = "backup_codes_regenerated", _("2FA Backup-Codes neu generiert")
        SUDO_MODE_ENTERED = "sudo_mode_entered", _("Sudo-Mode aktiviert (Re-Auth)")
        # Refs #1084 (S2): fehlgeschlagene Sudo-Re-Auth auditieren —
        # asymmetrisch zu LOGIN_FAILED/MFA_FAILED blieben Brute-Force-
        # Versuche ueber eine gestohlene Session bisher unsichtbar.
        SUDO_MODE_FAILED = "sudo_mode_failed", _("Sudo-Mode Re-Auth fehlgeschlagen")
        # Refs #867: protokolliert facility-uebergreifende Lese-Zugriffe
        # durch super_admin im /system/-Bereich (DSGVO-Rechenschaftspflicht).
        SYSTEM_VIEW = "system_view", _("Systembereich aufgerufen")
        # Refs #873: AuditLog-Export im /system/-Bereich (DSGVO-Spur, da
        # potentiell qualifizierte Daten in Massen exportiert werden).
        AUDIT_EXPORT = "audit_export", _("Audit-Log exportiert")
        # Refs #874: Wartungsmodus-Toggle ueber den Systembereich.
        MAINTENANCE_ENABLED = "maintenance_enabled", _("Wartungsmodus aktiviert")
        MAINTENANCE_DISABLED = "maintenance_disabled", _("Wartungsmodus deaktiviert")
        # Refs #919: persistenter LastRun-Marker fuer enforce_retention.
        # Compliance-Dashboard liest den juengsten Eintrag, um zu zeigen,
        # ob der Cron-Job wirklich laeuft.
        RETENTION_RUN_COMPLETED = "retention_run_completed", _("Retention-Lauf abgeschlossen")
        # Refs #919: persistenter Marker fuer einen erfolgreichen
        # Restore-Test. Wird per ``manage.py mark_restore_verified``
        # vom Operator manuell gesetzt, nachdem ein Restore gegen eine
        # frische DB verifiziert wurde.
        RESTORE_VERIFIED = "restore_verified", _("Restore-Test verifiziert")
        # Refs #932: 4-Augen-Lösch-Workflow — pro Workflow-Stufe ein
        # dedizierter AuditLog-Eintrag (DSGVO Art. 5(2) Rechenschaftspflicht).
        # Generisch über target_type="DeletionRequest" — funktioniert sowohl
        # für Event- als auch Client-Lösch-Anträge.
        DELETION_REQUESTED = "deletion_requested", _("Löschung beantragt")
        DELETION_APPROVED = "deletion_approved", _("Löschung genehmigt")
        DELETION_REJECTED = "deletion_rejected", _("Löschung abgelehnt")
        # Refs #794 / #919: Last-Run-Marker für die per systemd-Timer
        # laufenden Cron-Jobs. Das Compliance-Dashboard liest den jüngsten
        # Eintrag je Action, um zu zeigen, ob der Job wirklich läuft.
        SNAPSHOT_RUN_COMPLETED = "snapshot_run_completed", _("Statistik-Snapshot-Lauf abgeschlossen")
        BREACH_SCAN_COMPLETED = "breach_scan_completed", _("Breach-Detection-Scan abgeschlossen")
        MV_REFRESH_COMPLETED = "mv_refresh_completed", _("Statistik-View-Refresh abgeschlossen")
        # Refs #1070: Tombstone-Marker, der beim Pruning den entry_hash der
        # juengsten geloeschten Zeile (``boundary_hash``) festhaelt. Die
        # HMAC-Kette wird nie umgeschrieben — der Checkpoint legitimiert die
        # Diskontinuitaet, die das Loeschen alter Zeilen hinterlaesst.
        AUDIT_PRUNE_CHECKPOINT = "audit_prune_checkpoint", _("Audit-Pruning-Checkpoint")

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
    # Refs #1070: ``default=timezone.now`` statt ``auto_now_add`` — der
    # Zeitstempel muss VOR dem INSERT feststehen, damit er in den HMAC-
    # Kettenhash eingeht und beim Verify exakt reproduzierbar bleibt
    # (``auto_now_add`` ueberschreibt den Wert erst im INSERT). Append-only
    # bleibt durch ``save()``/``delete()`` + den DB-Trigger geschuetzt.
    timestamp = models.DateTimeField(default=timezone.now, verbose_name=_("Zeitstempel"))

    # Refs #1070: per-Zeile HMAC-Kette (Tamper-Evidenz). ``prev_hash`` ist der
    # entry_hash der vorherigen Zeile derselben Facility-Kette (""=Kettenstart);
    # ``entry_hash = HMAC(key, prev_hash || canonical(row))``. Beide nullable,
    # damit Bestandszeilen per ``backfill_audit_chain`` nachgezogen werden
    # koennen (NULL = noch nicht verkettet) und Raw-Inserts gueltig bleiben.
    prev_hash = models.CharField(max_length=64, null=True, blank=True, verbose_name=_("Vorheriger Hash"))
    entry_hash = models.CharField(max_length=64, null=True, blank=True, db_index=True, verbose_name=_("Eintrags-Hash"))

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
        """Prevent updates — only inserts are allowed — and seal the row into
        the per-facility HMAC hash chain on insert (Refs #1070).

        The chain assignment (advisory lock + read of the predecessor hash +
        compute) and the INSERT run inside one ``transaction.atomic()`` so the
        per-facility advisory lock is held across read-and-write and writers to
        the same chain serialize. The logic itself lives in
        ``core.services.audit.chain`` (services-for-logic convention).
        """
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("AuditLog entries are append-only and cannot be updated.")

        # Lazy import to avoid a models<->services import cycle.
        from core.services.audit.chain import assign_chain_fields

        with transaction.atomic():
            assign_chain_fields(self)
            super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Prevent deletion of audit log entries."""
        raise ValueError("AuditLog entries are append-only and cannot be deleted.")

    def __str__(self):
        return f"{self.get_action_display()} – {self.user} ({self.timestamp:%d.%m.%Y %H:%M})"
