"""Speicherkontingent je Einrichtung (Refs #2129, Befund N-5).

Zwei Operationen, eine Migration — beide gehoeren zum selben Feature und
duerfen nicht auseinanderlaufen:

* ``AddField Settings.max_storage_mb`` — das einrichtungseigene Kontingent
  (``NULL`` = keines, dann greift der ENV-Backstop
  ``FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB``).
* ``AlterField AuditLog.action`` — der neue Choice-Wert
  ``attachment_quota_exceeded``. Reine Choices-Aenderung ohne DDL-Wirkung
  (Django speichert ``choices`` nicht in Postgres); die Operation haelt nur
  den Migrationsstatus deckungsgleich mit dem Modell.

Kein ``RunPython`` — bestehende Zeilen brauchen keinen Backfill: ``NULL``
bedeutet exakt den Zustand vor dieser Migration (kein eigenes Kontingent), und
der Backstop-Default wirkt ab dem Deploy fuer alle Einrichtungen gleich. Damit
auch kein RLS-Rollen-Guard noetig (#2114-Muster gilt nur fuer ``RunPython``).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0175_withdrawn_deletion_request'),
    ]

    operations = [
        migrations.AddField(
            model_name='settings',
            name='max_storage_mb',
            field=models.PositiveIntegerField(blank=True, help_text='Gesamtgröße aller Dateianhänge dieser Einrichtung, inklusive Papierkorb. Leer = kein einrichtungseigenes Kontingent; es gilt dann der vom Betrieb gesetzte Standardwert.', null=True, verbose_name='Speicherkontingent (MB)'),
        ),
        migrations.AlterField(
            model_name='auditlog',
            name='action',
            field=models.CharField(choices=[('login', 'Anmeldung'), ('logout', 'Abmeldung'), ('login_failed', 'Anmeldung fehlgeschlagen'), ('login_unlock', 'Account-Sperre aufgehoben'), ('view_qualified', 'Daten der Stufe „Mit Zusatzangaben“ eingesehen'), ('export', 'Export'), ('delete', 'Löschung'), ('stage_change', 'Stufenwechsel'), ('settings_change', 'Einstellungen geändert'), ('download', 'Download'), ('legal_hold', 'Legal Hold'), ('offline_key_fetch', 'Offline-Schlüssel abgerufen'), ('offline_bundle_read', 'Offline-Bundle gelesen'), ('offline_facility_bundle_read', 'Offline-Facility-Bundle gelesen'), ('client_create', 'Person angelegt'), ('client_update', 'Person aktualisiert'), ('client_soft_deleted', 'Person in Papierkorb'), ('client_restored', 'Person wiederhergestellt'), ('client_anonymized', 'Person anonymisiert'), ('client_merged', 'Person zusammengeführt'), ('case_create', 'Fall angelegt'), ('case_update', 'Fall aktualisiert'), ('case_close', 'Fall geschlossen'), ('case_reopen', 'Fall wiedereröffnet'), ('case_soft_deleted', 'Fall in Papierkorb'), ('case_restored', 'Fall wiederhergestellt'), ('case_anonymized', 'Fall anonymisiert'), ('case_event_assigned', 'Ereignis dem Fall zugeordnet'), ('case_event_removed', 'Ereignis vom Fall gelöst'), ('episode_update', 'Episode aktualisiert'), ('goal_update', 'Wirkungsziel aktualisiert'), ('milestone_delete', 'Meilenstein gelöscht'), ('event_create', 'Ereignis angelegt'), ('event_update', 'Ereignis aktualisiert'), ('attachment_upload', 'Anhang hochgeladen'), ('attachment_delete', 'Anhang gelöscht'), ('attachment_quota_exceeded', 'Speicherkontingent überschritten'), ('workitem_create', 'Aufgabe angelegt'), ('workitem_update', 'Aufgabe aktualisiert'), ('user_created', 'Benutzer angelegt'), ('user_access_updated', 'Benutzerzugang geändert'), ('user_invite_sent', 'Einladung versendet'), ('user_role_changed', 'Benutzerrolle geändert'), ('user_deactivated', 'Benutzer deaktiviert'), ('deletion_confirmer_changed', 'Recht Löschbestätigung geändert'), ('password_reset_requested', 'Passwort-Reset angefordert'), ('credentials_revoked', 'Zugangsdaten widerrufen'), ('security_violation', 'Sicherheitsverletzung'), ('mfa_enabled', '2FA aktiviert'), ('mfa_disabled', '2FA deaktiviert'), ('mfa_failed', '2FA-Verifikation fehlgeschlagen'), ('backup_codes_generated', '2FA Backup-Codes generiert'), ('backup_codes_used', '2FA Backup-Code verwendet'), ('backup_codes_regenerated', '2FA Backup-Codes neu generiert'), ('webauthn_registered', 'Passkey registriert'), ('webauthn_removed', 'Passkey entfernt'), ('webauthn_failed', 'Passkey-Verifikation fehlgeschlagen'), ('sudo_mode_entered', 'Sudo-Mode aktiviert (Re-Auth)'), ('sudo_mode_failed', 'Sudo-Mode Re-Auth fehlgeschlagen'), ('system_view', 'Systembereich aufgerufen'), ('audit_export', 'Audit-Log exportiert'), ('maintenance_enabled', 'Wartungsmodus aktiviert'), ('maintenance_disabled', 'Wartungsmodus deaktiviert'), ('retention_run_completed', 'Retention-Lauf abgeschlossen'), ('restore_verified', 'Restore-Test verifiziert'), ('deletion_requested', 'Löschung beantragt'), ('deletion_approved', 'Löschung genehmigt'), ('deletion_rejected', 'Löschung abgelehnt'), ('deletion_withdrawn', 'Löschantrag zurückgezogen'), ('snapshot_run_completed', 'Statistik-Snapshot-Lauf abgeschlossen'), ('breach_scan_completed', 'Breach-Detection-Scan abgeschlossen'), ('mv_refresh_completed', 'Statistik-View-Refresh abgeschlossen'), ('audit_chain_verified', 'Audit-Ketten-Prüfung abgeschlossen'), ('audit_prune_checkpoint', 'Audit-Pruning-Checkpoint'), ('event_history_redacted', 'Ereignis-Historie redigiert'), ('offline_note_export', 'Offline-Notiz exportiert'), ('config_create', 'Konfiguration angelegt'), ('config_update', 'Konfiguration geändert'), ('config_delete', 'Konfiguration gelöscht'), ('config_reorder', 'Konfiguration umsortiert'), ('import_uploaded', 'Import hochgeladen'), ('import_committed', 'Import übernommen'), ('import_aborted', 'Import abgebrochen'), ('rectification_requested', 'Berichtigung beantragt'), ('rectification_approved', 'Berichtigung genehmigt'), ('rectification_rejected', 'Berichtigung abgelehnt'), ('processing_restricted', 'Verarbeitung eingeschränkt (Art. 18)'), ('processing_restriction_lifted', 'Einschränkung der Verarbeitung aufgehoben')], max_length=30, verbose_name='Aktion'),
        ),
    ]
