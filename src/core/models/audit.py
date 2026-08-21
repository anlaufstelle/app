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
        # Refs #1805: Label auf die neue Stufen-Vokabel gezogen (wie
        # ``Client.ContactStage``). Der gespeicherte WERT bleibt
        # ``view_qualified`` — Audit-Zeilen sind unveraenderlich, und der Code
        # ist der Datenvertrag fuer Export/Filter; nur die Anzeige wechselt.
        VIEW_QUALIFIED = "view_qualified", _("Daten der Stufe „Mit Zusatzangaben“ eingesehen")
        EXPORT = "export", _("Export")
        DELETE = "delete", _("Löschung")
        STAGE_CHANGE = "stage_change", _("Stufenwechsel")
        SETTINGS_CHANGE = "settings_change", _("Einstellungen geändert")
        DOWNLOAD = "download", _("Download")
        LEGAL_HOLD = "legal_hold", _("Legal Hold")
        OFFLINE_KEY_FETCH = "offline_key_fetch", _("Offline-Schlüssel abgerufen")
        # Refs #1410 (M15): Lesen eines Offline-Client-Bundles. Bewusst NICHT
        # ``EXPORT`` — Sammel-Mitnahme + periodische Revalidierung erzeugen viele
        # GETs; unter ``EXPORT`` floeteten sie die Massen-Export-Breach-Heuristik
        # (``detect_mass_export``) mit Fehlalarmen. Die DSGVO-Rechenschaftsspur
        # ("wer nahm wann welche Personen offline mit") bleibt erhalten, nur die
        # Klassifikation aendert sich.
        OFFLINE_BUNDLE_READ = "offline_bundle_read", _("Offline-Bundle gelesen")
        # Refs #1518 (#1499): Lesen des personenlosen Facility-Meta-Bundles
        # (Offline-Create-Katalog). Wie ``OFFLINE_BUNDLE_READ`` bewusst NICHT
        # ``EXPORT`` (haelt die Massen-Export-Breach-Heuristik sauber); zusaetzlich
        # personenlos ⇒ PII-frei (kein Pseudonym, keine Personen-Referenz).
        OFFLINE_FACILITY_BUNDLE_READ = "offline_facility_bundle_read", _("Offline-Facility-Bundle gelesen")
        CLIENT_CREATE = "client_create", _("Person angelegt")
        CLIENT_UPDATE = "client_update", _("Person aktualisiert")
        CLIENT_SOFT_DELETED = "client_soft_deleted", _("Person in Papierkorb")
        CLIENT_RESTORED = "client_restored", _("Person wiederhergestellt")
        CLIENT_ANONYMIZED = "client_anonymized", _("Person anonymisiert")
        CASE_CREATE = "case_create", _("Fall angelegt")
        CASE_UPDATE = "case_update", _("Fall aktualisiert")
        CASE_CLOSE = "case_close", _("Fall geschlossen")
        CASE_REOPEN = "case_reopen", _("Fall wiedereröffnet")
        # Refs #1957 (#1860 B5): Ereignis↔Fall-Zuordnung. Bewusst zwei
        # gerichtete Aktionen statt einer generischen „Zuordnung geaendert" —
        # unter /audit/ ist genau die Richtung die Frage („wurde ein Ereignis
        # aus dem Fall geloest?"), und ein Enum-Wert je Richtung erspart es,
        # dafuer das ``detail`` lesen zu muessen. ``target_obj`` ist das
        # **Ereignis**, der Fall steht als ``case_id`` im ``detail``.
        CASE_EVENT_ASSIGNED = "case_event_assigned", _("Ereignis dem Fall zugeordnet")
        CASE_EVENT_REMOVED = "case_event_removed", _("Ereignis vom Fall gelöst")
        # Refs #1957 (#1860 B2/B3): Bearbeitung der beiden Fall-Unterobjekte.
        # Beide tragen im ``detail`` nur ``case_id`` + ``changed_fields`` —
        # nie Titel oder Beschreibung, die sind Freitext am Fall.
        EPISODE_UPDATE = "episode_update", _("Episode aktualisiert")
        GOAL_UPDATE = "goal_update", _("Wirkungsziel aktualisiert")
        MILESTONE_DELETE = "milestone_delete", _("Meilenstein gelöscht")
        EVENT_CREATE = "event_create", _("Ereignis angelegt")
        # Refs #1957 (#1860 B1): Ereignis-Bearbeitungen liefen bisher nur in die
        # ``EventHistory`` — unter /audit/ fehlte jede Bearbeitung. Das
        # ``detail`` traegt Dokumentationstyp + geaenderte Feld-Slugs, nie
        # Werte (Beschaeftigtendatenschutz + Art. 5 Abs. 1 lit. c DSGVO, siehe
        # #1860 „DSGVO-Einordnung").
        EVENT_UPDATE = "event_update", _("Ereignis aktualisiert")
        # Refs #1957 (#1860 B4): Anhaenge. Bewusst owner-agnostisch (Ereignis,
        # Person, Fall) — die Zuordnung laeuft ueber ``owner_type``/``owner_id``
        # im ``detail`` (ADR-039 §5), die ``target_id`` traegt die Owner-PK.
        # ``DOWNLOAD`` bleibt unveraendert: der Wert ist Datenvertrag bereits
        # geschriebener, unveraenderlicher Zeilen.
        ATTACHMENT_UPLOAD = "attachment_upload", _("Anhang hochgeladen")
        ATTACHMENT_DELETE = "attachment_delete", _("Anhang gelöscht")
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
        # Passkeys/WebAuthn als zweiter Faktor (ADR-032, Refs #1492).
        WEBAUTHN_REGISTERED = "webauthn_registered", _("Passkey registriert")
        WEBAUTHN_REMOVED = "webauthn_removed", _("Passkey entfernt")
        WEBAUTHN_FAILED = "webauthn_failed", _("Passkey-Verifikation fehlgeschlagen")
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
        # Refs #1872: Last-Run-Marker fuer ``verify_audit_chain``. Der Job
        # meldete einen gruenen Lauf bisher nur als Exit-Code an supercronic —
        # im UI war nicht erkennbar, ob die HMAC-Kette ueberhaupt je geprueft
        # wurde. Wird NUR nach vollstaendig gruenem Lauf geschrieben; ein
        # Tamper-Fund beendet den Command vorher mit Exit != 0.
        AUDIT_CHAIN_VERIFIED = "audit_chain_verified", _("Audit-Ketten-Prüfung abgeschlossen")
        # Refs #1070: Tombstone-Marker, der beim Pruning den entry_hash der
        # juengsten geloeschten Zeile (``boundary_hash``) festhaelt. Die
        # HMAC-Kette wird nie umgeschrieben — der Checkpoint legitimiert die
        # Diskontinuitaet, die das Loeschen alter Zeilen hinterlaesst.
        AUDIT_PRUNE_CHECKPOINT = "audit_prune_checkpoint", _("Audit-Pruning-Checkpoint")
        # Refs #1476 (R10): Export einer nicht uebertragbaren Offline-Notiz als
        # Klartext-Datei auf die Platte (entschluesselte Art.-9-PII verlaesst die
        # App). Der Client meldet diese bewusste Nutzeraktion offline-tauglich
        # ueber die Replay-Queue. Bewusst NICHT ``EXPORT`` (analog
        # ``OFFLINE_BUNDLE_READ``): der Offline-Konflikt-Workflow soll die
        # Massen-Export-Breach-Heuristik nicht mit Einzel-Notiz-Exporten
        # fluten; die DSGVO-Rechenschaftsspur ("wer exportierte wann eine
        # Offline-Notiz") bleibt als distinkte Aktion erhalten.
        OFFLINE_NOTE_EXPORT = "offline_note_export", _("Offline-Notiz exportiert")
        # Refs #1056 (M0): Mutationen an der Formular-Konfiguration
        # (DocumentType/FieldTemplate/FieldGroup/DocumentTypeField) über den
        # Config-Service. CONFIG_REORDER buendelt Umsortierungen (Gruppen,
        # Felder, Feld-Verschiebung) in genau EINEM Audit-Eintrag je Batch.
        CONFIG_CREATE = "config_create", _("Konfiguration angelegt")
        CONFIG_UPDATE = "config_update", _("Konfiguration geändert")
        CONFIG_DELETE = "config_delete", _("Konfiguration gelöscht")
        CONFIG_REORDER = "config_reorder", _("Konfiguration umsortiert")
        # Refs #1060 (M4): CSV-Import-Wizard — pro Lebenszyklus-Schritt eines
        # ImportJob ein dedizierter Eintrag (Upload/Commit/Abbruch). Bewusst
        # NICHT ``EXPORT``/``CLIENT_CREATE``-Wiederverwendung: der Commit
        # erzeugt zusaetzlich je Zeile die regulaeren Service-Audit-Eintraege;
        # diese Marker tragen die Job-Ebene (Datei, Zeilenzahl, Ergebnis).
        IMPORT_UPLOADED = "import_uploaded", _("Import hochgeladen")
        IMPORT_COMMITTED = "import_committed", _("Import übernommen")
        IMPORT_ABORTED = "import_aborted", _("Import abgebrochen")
        # Refs #1060 (M4): Art.-16-Berichtigungsworkflow (ADR-041) — analog
        # zum 4-Augen-Loesch-Workflow (DELETION_*) je Workflow-Stufe ein
        # Eintrag; ``detail`` traegt nie Feldwerte (die liegen verschluesselt
        # am RectificationRequest), nur field_path + Begruendungs-Metadaten.
        RECTIFICATION_REQUESTED = "rectification_requested", _("Berichtigung beantragt")
        RECTIFICATION_APPROVED = "rectification_approved", _("Berichtigung genehmigt")
        RECTIFICATION_REJECTED = "rectification_rejected", _("Berichtigung abgelehnt")

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
    # Refs #1369: die **handelnde** Person (Actor), getrennt vom ``user``-Feld.
    # Fuer die meisten Aktionen ist ``user`` bereits der Actor (LOGIN, EXPORT,
    # CLIENT_CREATE …) und ``actor`` bleibt NULL. Fuer die *ziel-attribuierten*
    # Privileg-Events (USER_ROLE_CHANGED / USER_DEACTIVATED /
    # DELETION_CONFIRMER_CHANGED) traegt ``user`` das **Ziel** und ``actor``
    # den handelnden Admin — sonst laese sich der immutable Log, als haette das
    # Ziel sich selbst das Flag gesetzt. ``NULL`` bei nicht-request-getriebenen
    # ORM-Pfaden (Management-Command, Service, Bootstrap); die dann fehlende
    # menschliche Zuordnung wird im ``detail`` explizit als ``system`` markiert.
    # ``SET_NULL`` analog zu ``user`` — die tamper-evidente, namens-erhaltende
    # Kopie liegt in ``detail`` (siehe signals/audit.py). Refs #1070.
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_actions",
        verbose_name=_("Handelnde Person"),
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
