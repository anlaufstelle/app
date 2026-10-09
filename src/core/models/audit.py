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
        # Refs #1876 (ADR-045): Duplikat-Merge. Zwingend GETRENNT von
        # ``CLIENT_SOFT_DELETED``: ``detect_mass_client_destruction``
        # (``compliance/breach_detection.py``) zaehlt jene Aktion ab 20 im
        # Kurzfenster — eine Aufraeum-Kampagne ueber Alt-Duplikate ist
        # gezieltes Konsolidieren, kein Vernichten, und darf keinen
        # Breach-Alarm ausloesen. ``target_obj`` ist die QUELLE (die Person,
        # die zum Tombstone wird); das ``detail`` traegt Ziel-UUID,
        # Undo-Inventar (UUID-Listen) und uebernommene Feld-Slugs — nie Werte.
        CLIENT_MERGED = "client_merged", _("Person zusammengeführt")
        CASE_CREATE = "case_create", _("Fall angelegt")
        CASE_UPDATE = "case_update", _("Fall aktualisiert")
        CASE_CLOSE = "case_close", _("Fall geschlossen")
        CASE_REOPEN = "case_reopen", _("Fall wiedereröffnet")
        # Refs #1818 (ADR-044): Fall-Loeschpfad. Bewusst drei eigene Aktionen
        # statt der generischen ``DELETE``-Zeile — sie spiegeln die drei
        # Zustaende des Papierkorbs (hinein, zurueck, endgueltig redigiert)
        # genauso wie die Personen-Trias ``CLIENT_SOFT_DELETED``/
        # ``CLIENT_RESTORED``/``CLIENT_ANONYMIZED``, und unter /audit/ ist genau
        # diese Unterscheidung die Frage. Die generischen ``DELETION_*``-Zeilen
        # des Vier-Augen-Antrags (target_type ``DeletionRequest``) bleiben
        # unveraendert nutzbar. ``target_obj`` ist der **Fall**; der Falltitel
        # steht nie im ``detail`` (Freitext, #1751).
        CASE_SOFT_DELETED = "case_soft_deleted", _("Fall in Papierkorb")
        CASE_RESTORED = "case_restored", _("Fall wiederhergestellt")
        CASE_ANONYMIZED = "case_anonymized", _("Fall anonymisiert")
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
        # Refs #2129 (Befund N-5): Ein Upload, der am Speicherkontingent der
        # Einrichtung scheitert. Bewusst KEINE ``SECURITY_VIOLATION``: das ist
        # ein normaler Betriebszustand („die Platte der Einrichtung ist voll"),
        # kein Angriff — unter ``SECURITY_VIOLATION`` verwaesserte es die
        # Sicherheits-Sicht und erbte zugleich deren Prune-Ausnahme. Eine eigene
        # Aktion statt ``detail["reason"]`` an einer generischen, weil unter
        # /audit/ genau die Frage gestellt wird („warum kann hier niemand mehr
        # hochladen?"). Das ``detail`` traegt nur Konfigurations- und
        # Mengenangaben (``used_mb``/``limit_mb``/``requested_mb``), nie den
        # Dateinamen.
        ATTACHMENT_QUOTA_EXCEEDED = "attachment_quota_exceeded", _("Speicherkontingent überschritten")
        WORKITEM_CREATE = "workitem_create", _("Aufgabe angelegt")
        WORKITEM_UPDATE = "workitem_update", _("Aufgabe aktualisiert")
        # Refs #1871: Die Fach-UI fuer Zugaenge schliesst die bisherige
        # Audit-Luecke der Benutzeranlage und macht den Versand eines
        # Setup-Links als eigenes Security-Ereignis sichtbar. Aenderungen,
        # die nicht bereits eine schmalere Action besitzen (z.B. MFA-Pflicht
        # oder Reaktivierung), laufen ueber USER_ACCESS_UPDATED.
        USER_CREATED = "user_created", _("Benutzer angelegt")
        USER_ACCESS_UPDATED = "user_access_updated", _("Benutzerzugang geändert")
        USER_INVITE_SENT = "user_invite_sent", _("Einladung versendet")
        USER_ROLE_CHANGED = "user_role_changed", _("Benutzerrolle geändert")
        USER_DEACTIVATED = "user_deactivated", _("Benutzer deaktiviert")
        DELETION_CONFIRMER_CHANGED = "deletion_confirmer_changed", _("Recht Löschbestätigung geändert")
        PASSWORD_RESET_REQUESTED = "password_reset_requested", _("Passwort-Reset angefordert")
        # Refs #1965: Abschluss eines Credential-Wechsels (Passwortwechsel,
        # Passwort-Reset, Invite-Setup) samt der daran haengenden Widerrufe —
        # Offline-Salt, Sudo-Fenster, Zwangswechsel-Flag, Lockout. Bewusst EINE
        # Aktion mit ``detail["reason"]`` statt drei Aktionen: die Frage unter
        # /audit/ ist "wann wechselten hier zuletzt Zugangsdaten", nicht "ueber
        # welche der drei Routen". Bewusst NICHT auf dem Rechteentzugs-Pfad
        # (Rollenwechsel/Deaktivierung/Lockout) geschrieben — der traegt mit
        # ``USER_ROLE_CHANGED``/``USER_DEACTIVATED``/``LOGIN_FAILED`` bereits
        # seine eigene Spur. Der Name folgt der Funktion ``revoke_credentials``:
        # protokolliert wird der WIDERRUF der am alten Passwort haengenden
        # Artefakte, nicht das neue Passwort.
        CREDENTIALS_REVOKED = "credentials_revoked", _("Zugangsdaten widerrufen")
        SECURITY_VIOLATION = "security_violation", _("Sicherheitsverletzung")
        MFA_ENABLED = "mfa_enabled", _("2FA aktiviert")
        MFA_DISABLED = "mfa_disabled", _("2FA deaktiviert")
        # Refs #2469: facility administration removed every second factor
        # (TOTP, passkeys, backup codes) of ANOTHER account. ``user`` is the
        # target, ``actor`` the administrator; the detail only carries counters.
        MFA_RESET_BY_ADMIN = "mfa_reset_by_admin", _("2FA durch Administration zurückgesetzt")
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
        FILE_REENCRYPTION_COMPLETED = "file_reencryption_completed", _("Datei-Umschlüsselung abgeschlossen")
        # Refs #932: 4-Augen-Lösch-Workflow — pro Workflow-Stufe ein
        # dedizierter AuditLog-Eintrag (DSGVO Art. 5(2) Rechenschaftspflicht).
        # Generisch über target_type="DeletionRequest" — funktioniert sowohl
        # für Event- als auch Client-Lösch-Anträge.
        DELETION_REQUESTED = "deletion_requested", _("Löschung beantragt")
        DELETION_APPROVED = "deletion_approved", _("Löschung genehmigt")
        DELETION_REJECTED = "deletion_rejected", _("Löschung abgelehnt")
        # Refs #2040: vierter Ausgang desselben Workflows — die
        # Antragstellerin zieht ihren eigenen, noch nicht entschiedenen
        # Antrag zurueck. Bewusst eine eigene Aktion statt einer
        # ``DELETION_REJECTED``-Zeile ohne Reviewer: abgelehnt hat hier
        # niemand, und die Rechenschaftsspur muss den Unterschied zwischen
        # „Vier-Augen-Pruefung sagte nein" und „Antrag wurde zurueckgezogen"
        # tragen koennen.
        DELETION_WITHDRAWN = "deletion_withdrawn", _("Löschantrag zurückgezogen")
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
        # Refs #2415: Result of ``manage.py check_db_roles``. The web container
        # has no admin DB credentials (Refs #1962) and cannot check the admin
        # role itself; the maintenance run records its result here and the
        # compliance dashboard reads the latest row. Written after every run
        # that queried the roles, so a failing run supersedes an older green
        # one. ``detail`` carries only fixed slugs (``result``, ``problems``),
        # never role names, passwords or a DSN.
        DB_ROLES_VERIFIED = "db_roles_verified", _("DB-Rollen-Prüfung abgeschlossen")
        # Refs #1070: Tombstone-Marker, der beim Pruning den entry_hash der
        # juengsten geloeschten Zeile (``boundary_hash``) festhaelt. Die
        # HMAC-Kette wird nie umgeschrieben — der Checkpoint legitimiert die
        # Diskontinuitaet, die das Loeschen alter Zeilen hinterlaesst.
        AUDIT_PRUNE_CHECKPOINT = "audit_prune_checkpoint", _("Audit-Pruning-Checkpoint")
        # Refs #2065 (ADR-050 D5.3/M-4): Nachweis je Charge des
        # EventHistory-Sweeps. Er belegt, dass die Append-only-Garantie fuer
        # genau diese Zeilen legitim geoeffnet wurde — und muss deshalb mit
        # DERSELBEN Transaktion committen wie die Redaktion, die er belegt.
        # Prune-exempt wie AUDIT_PRUNE_CHECKPOINT (M-4): ein Nachweis, der vor
        # den von ihm belegten Zeilen verschwindet, belegt nichts mehr.
        EVENT_HISTORY_REDACTED = "event_history_redacted", _("Ereignis-Historie redigiert")
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
        # Refs #2045: Art.-18-Sperrvermerk. Bewusst KEINE Wiederverwendung von
        # ``LEGAL_HOLD`` — der Hold ist das Gegenteil (er blockiert die
        # Loeschung nach Art. 17 Abs. 3, die Einschraenkung die Nutzung), und
        # genau diese Konflation war der Befund. Auch ``CLIENT_UPDATE`` taugt
        # nicht: Setzen und Aufheben sind Betroffenenrechte-Vorgaenge mit
        # eigener Rechtsfolge, die unter /audit/ als solche auffindbar sein
        # muessen — Muster ``DELETION_*``/``RECTIFICATION_*``.
        PROCESSING_RESTRICTED = "processing_restricted", _("Verarbeitung eingeschränkt (Art. 18)")
        PROCESSING_RESTRICTION_LIFTED = (
            "processing_restriction_lifted",
            _("Einschränkung der Verarbeitung aufgehoben"),
        )
        # Refs #2042: manuelle Schichtnotiz in der Uebergabe. Drei eigene
        # Aktionen statt einer generischen „Notiz geaendert" — unter /audit/
        # ist genau die Unterscheidung die Frage („wurde eine Uebergabenotiz
        # geloescht, bevor die Folgeschicht sie lesen konnte?"), und ein
        # Enum-Wert je Vorgang erspart es, dafuer das ``detail`` zu lesen
        # (Muster ``CLIENT_SOFT_DELETED``/``CLIENT_RESTORED``). Das ``detail``
        # traegt ausschliesslich Schicht-Koordinaten (``shift_date``,
        # ``time_filter``) und ``changed_fields`` — nie den Notiztext: der ist
        # Freitext ueber Personen und liegt verschluesselt an der Zeile
        # (#1751). Die Loeschung durch den Retention-Sweep laeuft nicht
        # hierueber, sondern ueber ``audit_retention_decision``.
        HANDOVER_NOTE_CREATED = "handover_note_created", _("Übergabenotiz angelegt")
        HANDOVER_NOTE_UPDATED = "handover_note_updated", _("Übergabenotiz geändert")
        HANDOVER_NOTE_DELETED = "handover_note_deleted", _("Übergabenotiz gelöscht")

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
    # ``entry_hash = HMAC(key, prev_hash || canonical(row))``. Both nullable
    # only for rows written before the chain existed (sealed later by
    # ``backfill_audit_chain``); a new row must carry both -- migration 0184
    # rejects every INSERT without them (Refs #2449).
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
