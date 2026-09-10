"""Manuelle Schichtnotiz in der Uebergabe: Modell + Aufbewahrungsfrist (Refs #2042).

Drei Operationen, alle von ``makemigrations`` erzeugt (``--check`` danach leer):

* ``AddField`` der neuen, bewusst kurzen Frist ``Settings.retention_handover_notes_days``
  (Vorgabe 90 Tage). ``PositiveIntegerField`` — negative Werte schliesst schon der
  Spaltentyp aus; der bestehende ``settings_retention_days_min_1``-Constraint wird
  NICHT erweitert (das waere eine Verhaltensaenderung an einer fremden Zusage), die
  Enforcement-Funktion bricht bei ``< 1`` defensiv ab.
* ``AlterField`` auf ``AuditLog.action`` — mechanische Folge der drei neuen
  ``Action``-Werte (``handover_note_created``/``_updated``/``_deleted``). Reine
  ``choices``-Aenderung: kein DDL auf der Spalte, ``max_length`` bleibt 30.
* ``CreateModel`` ``ShiftHandoverNote``. Der ``UniqueConstraint`` traegt
  ``nulls_distinct=False`` (Postgres 15+), damit die Ganztags-Schicht
  (``time_filter IS NULL``) nicht beliebig viele Notizen desselben Tages zulaesst.

**Fix-Runde 1 Befunde E-01/E-02).** Diese Migration wurde im Worktree neu
erzeugt, bevor sie irgendwo gemergt war — der Migrationsslot bleibt 0179/0180:

* ``window_start``/``window_end``/``editable_until`` frieren die zur Anlagezeit
  gueltigen Fenstergrenzen an der Zeile ein. Vorher rechnete jeder Zugriff aus den
  gegenwaertig aktiven Zeitfiltern neu, und eine Konfigurationsaenderung riss das
  Editierfenster einer abgeschlossenen Schicht wieder auf.
* ``time_filter`` traegt ``on_delete=PROTECT`` statt ``CASCADE``: das Loeschen eines
  Zeitfensters raeumte seine Notizen sonst am auditierten Loeschpfad und an der
  Aufbewahrungsfrist vorbei ab.

Kein ``RunPython`` und damit kein RLS-Rollen-Guard noetig (#2114-Muster greift nur
fuer ``RunPython``) — die RLS-Policy der neuen Tabelle folgt in
`0180_rls_shifthandovernote.py`, nach dem Muster von `0154_rls_banrevision.py`.
Kein Backfill: die Tabelle ist neu und startet leer.
"""

import django.core.validators
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0178_event_deleted_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="settings",
            name="retention_handover_notes_days",
            field=models.PositiveIntegerField(
                default=90,
                help_text="Wie lange manuelle Schichtnotizen in der Übergabe aufbewahrt werden, bevor enforce_retention sie endgültig löscht.",
                validators=[django.core.validators.MinValueValidator(1)],
                verbose_name="Aufbewahrung Übergabenotizen (Tage)",
            ),
        ),
        migrations.AlterField(
            model_name="auditlog",
            name="action",
            field=models.CharField(
                choices=[
                    ("login", "Anmeldung"),
                    ("logout", "Abmeldung"),
                    ("login_failed", "Anmeldung fehlgeschlagen"),
                    ("login_unlock", "Account-Sperre aufgehoben"),
                    ("view_qualified", "Daten der Stufe „Mit Zusatzangaben“ eingesehen"),
                    ("export", "Export"),
                    ("delete", "Löschung"),
                    ("stage_change", "Stufenwechsel"),
                    ("settings_change", "Einstellungen geändert"),
                    ("download", "Download"),
                    ("legal_hold", "Legal Hold"),
                    ("offline_key_fetch", "Offline-Schlüssel abgerufen"),
                    ("offline_bundle_read", "Offline-Bundle gelesen"),
                    ("offline_facility_bundle_read", "Offline-Facility-Bundle gelesen"),
                    ("client_create", "Person angelegt"),
                    ("client_update", "Person aktualisiert"),
                    ("client_soft_deleted", "Person in Papierkorb"),
                    ("client_restored", "Person wiederhergestellt"),
                    ("client_anonymized", "Person anonymisiert"),
                    ("client_merged", "Person zusammengeführt"),
                    ("case_create", "Fall angelegt"),
                    ("case_update", "Fall aktualisiert"),
                    ("case_close", "Fall geschlossen"),
                    ("case_reopen", "Fall wiedereröffnet"),
                    ("case_soft_deleted", "Fall in Papierkorb"),
                    ("case_restored", "Fall wiederhergestellt"),
                    ("case_anonymized", "Fall anonymisiert"),
                    ("case_event_assigned", "Ereignis dem Fall zugeordnet"),
                    ("case_event_removed", "Ereignis vom Fall gelöst"),
                    ("episode_update", "Episode aktualisiert"),
                    ("goal_update", "Wirkungsziel aktualisiert"),
                    ("milestone_delete", "Meilenstein gelöscht"),
                    ("event_create", "Ereignis angelegt"),
                    ("event_update", "Ereignis aktualisiert"),
                    ("attachment_upload", "Anhang hochgeladen"),
                    ("attachment_delete", "Anhang gelöscht"),
                    ("attachment_quota_exceeded", "Speicherkontingent überschritten"),
                    ("workitem_create", "Aufgabe angelegt"),
                    ("workitem_update", "Aufgabe aktualisiert"),
                    ("user_created", "Benutzer angelegt"),
                    ("user_access_updated", "Benutzerzugang geändert"),
                    ("user_invite_sent", "Einladung versendet"),
                    ("user_role_changed", "Benutzerrolle geändert"),
                    ("user_deactivated", "Benutzer deaktiviert"),
                    ("deletion_confirmer_changed", "Recht Löschbestätigung geändert"),
                    ("password_reset_requested", "Passwort-Reset angefordert"),
                    ("credentials_revoked", "Zugangsdaten widerrufen"),
                    ("security_violation", "Sicherheitsverletzung"),
                    ("mfa_enabled", "2FA aktiviert"),
                    ("mfa_disabled", "2FA deaktiviert"),
                    ("mfa_failed", "2FA-Verifikation fehlgeschlagen"),
                    ("backup_codes_generated", "2FA Backup-Codes generiert"),
                    ("backup_codes_used", "2FA Backup-Code verwendet"),
                    ("backup_codes_regenerated", "2FA Backup-Codes neu generiert"),
                    ("webauthn_registered", "Passkey registriert"),
                    ("webauthn_removed", "Passkey entfernt"),
                    ("webauthn_failed", "Passkey-Verifikation fehlgeschlagen"),
                    ("sudo_mode_entered", "Sudo-Mode aktiviert (Re-Auth)"),
                    ("sudo_mode_failed", "Sudo-Mode Re-Auth fehlgeschlagen"),
                    ("system_view", "Systembereich aufgerufen"),
                    ("audit_export", "Audit-Log exportiert"),
                    ("maintenance_enabled", "Wartungsmodus aktiviert"),
                    ("maintenance_disabled", "Wartungsmodus deaktiviert"),
                    ("retention_run_completed", "Retention-Lauf abgeschlossen"),
                    ("restore_verified", "Restore-Test verifiziert"),
                    ("deletion_requested", "Löschung beantragt"),
                    ("deletion_approved", "Löschung genehmigt"),
                    ("deletion_rejected", "Löschung abgelehnt"),
                    ("deletion_withdrawn", "Löschantrag zurückgezogen"),
                    ("snapshot_run_completed", "Statistik-Snapshot-Lauf abgeschlossen"),
                    ("breach_scan_completed", "Breach-Detection-Scan abgeschlossen"),
                    ("mv_refresh_completed", "Statistik-View-Refresh abgeschlossen"),
                    ("audit_chain_verified", "Audit-Ketten-Prüfung abgeschlossen"),
                    ("audit_prune_checkpoint", "Audit-Pruning-Checkpoint"),
                    ("event_history_redacted", "Ereignis-Historie redigiert"),
                    ("offline_note_export", "Offline-Notiz exportiert"),
                    ("config_create", "Konfiguration angelegt"),
                    ("config_update", "Konfiguration geändert"),
                    ("config_delete", "Konfiguration gelöscht"),
                    ("config_reorder", "Konfiguration umsortiert"),
                    ("import_uploaded", "Import hochgeladen"),
                    ("import_committed", "Import übernommen"),
                    ("import_aborted", "Import abgebrochen"),
                    ("rectification_requested", "Berichtigung beantragt"),
                    ("rectification_approved", "Berichtigung genehmigt"),
                    ("rectification_rejected", "Berichtigung abgelehnt"),
                    ("processing_restricted", "Verarbeitung eingeschränkt (Art. 18)"),
                    ("processing_restriction_lifted", "Einschränkung der Verarbeitung aufgehoben"),
                    ("handover_note_created", "Übergabenotiz angelegt"),
                    ("handover_note_updated", "Übergabenotiz geändert"),
                    ("handover_note_deleted", "Übergabenotiz gelöscht"),
                ],
                max_length=30,
                verbose_name="Aktion",
            ),
        ),
        migrations.CreateModel(
            name="ShiftHandoverNote",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "shift_date",
                    models.DateField(
                        help_text="Lokales Kalenderdatum des Schichtbeginns.", verbose_name="Schichtdatum"
                    ),
                ),
                (
                    "note",
                    models.JSONField(
                        blank=True,
                        help_text="Wird verschlüsselt gespeichert.",
                        null=True,
                        verbose_name="Notiz (verschlüsselt)",
                    ),
                ),
                ("window_start", models.DateTimeField(verbose_name="Schichtbeginn")),
                ("window_end", models.DateTimeField(verbose_name="Schichtende")),
                ("editable_until", models.DateTimeField(verbose_name="Bearbeitbar bis")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Erstellt am")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Aktualisiert am")),
                (
                    "author",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="authored_handover_notes",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Verfasst von",
                    ),
                ),
                (
                    "facility",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="shift_handover_notes",
                        to="core.facility",
                        verbose_name="Einrichtung",
                    ),
                ),
                (
                    "time_filter",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="handover_notes",
                        to="core.timefilter",
                        verbose_name="Schicht",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="updated_handover_notes",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Zuletzt geändert von",
                    ),
                ),
            ],
            options={
                "verbose_name": "Übergabenotiz",
                "verbose_name_plural": "Übergabenotizen",
                "ordering": ["-shift_date", "-created_at"],
                "indexes": [models.Index(fields=["facility", "shift_date"], name="handovernote_fac_date_idx")],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("facility", "shift_date", "time_filter"),
                        name="uniq_handover_note_per_shift",
                        nulls_distinct=False,
                    )
                ],
            },
        ),
    ]
