# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

_Noch nicht veröffentlicht._ Sammelt die seit v0.15.0 hinzugekommenen Änderungen — Schwerpunkte: die erste öffentliche Demo-Instanz (`demo.anlaufstelle.app`) mit eigener Demo-Schutzschicht und eine umfassend überarbeitete Aufgabenübersicht, dazu Security-Härtung und Zeitzonen-Korrekturen.

### Security

- **Ratelimit für den System-Audit-Log-Export** (#1193) — `SystemAuditLogExportView` ist gegen Massenabruf gedrosselt, analog zu den übrigen Export-/Download-Pfaden.
- **`cryptography` auf 48.0.1** ([GHSA-537c-gmf6-5ccf](https://github.com/advisories/GHSA-537c-gmf6-5ccf)) — Security-Patch innerhalb des `<49`-Rahmens, schließt eine von `pip-audit` gemeldete Schwachstelle in 48.0.0; reiner Cap-Bump (`>=48.0.1,<49`), keine API-/Verhaltensänderung. Der Major-Bump auf 49.0.0 wird separat verifiziert (#1129).

### Added

- **Öffentliche Demo-Instanz `demo.anlaufstelle.app`** (#1062, #971) — eigener Deployment-Stack auf separater VPS (Image-Build auf dem Server, stündlicher Reset), ein `DEMO_MODE`-Banner und ein Login-Panel mit den Demo-Zugangsdaten und dem nächsten Reset-Zeitpunkt. Eine `DemoGuardMiddleware` sperrt schadensträchtige Aktionen (Wartungsmodus-Toggle, 2FA-Setup, Passwortänderung, Benutzerverwaltung); Banner und Panel weisen zudem auf deaktivierte Funktionen hin (E-Mails gehen nur in die Konsole).
- **Aufgabenübersicht als zweispaltige Arbeitsansicht** (#1149) — „Offen" und „In Bearbeitung" stehen auf breiten Bildschirmen nebeneinander (mobil weiter untereinander); „Kürzlich erledigt" rückt als standardmäßig eingeklappter Rückblick („letzte 7 Tage") darunter.

### Changed

- **Aufgaben-Aktionen vereinheitlicht und abgesichert** (#1130, #1146) — Tabellen- und Detailansicht nutzen dieselben Begriffe („Aufgabe übernehmen", „Als nicht relevant schließen"), bieten „Als erledigt markieren" als direkte Aktion und sichern abschließende Statuswechsel mit einer erklärenden Bestätigung ab. Die Status-Transition-Logik bleibt unverändert.
- **Aufgabenliste: Frist eindeutig benannt** (#1133) — das Fristen-Badge trägt das sichtbare Präfix „Fällig:"; das missverständliche, unbeschriftete Erstellungsdatum entfällt in der Übersicht (bleibt in der Einzelansicht).
- **Zeitstrom-Übergabe auf Schichtrelevantes fokussiert** (#1139) — die zur Aufgaben-Fokusbox redundante allgemeine „Offene Aufgaben"-Liste entfällt; übergaberelevante Aufgaben erscheinen weiterhin schichtbezogen in den Hinweisen.
- **Zeitstrom: dauerhafte Sektion „Aktueller Dienst"** (#1138) — die Dienst-Kennzahlen beziehen sich jetzt immer auf die laufende Schicht (Mitternachts-Überlappung berücksichtigt) statt auf die Datums-/Schichtauswahl im Zeitstrom.

### Fixed

- **Verschlüsselte Anhänge im Dokumentations-Flow erreichbar** (#1142) — der Kontakt-Dokumentationstyp bietet wieder ein nutzbares (weiterhin at-rest verschlüsseltes) Datei-Upload-Feld. Zuvor blendete die Sensitivitäts-Regel das einzige Datei-Feld des Seeds für Fachkraft und Assistenz aus, sodass das beworbene Feature „Verschlüsselte Anhänge" im Default-Flow nicht nutzbar war.
- **Aufgaben-Default-Filter zeigt die passende Liste** (#1145) — Anzeige und Filterwirkung des Zuweisungs-Filters stimmen wieder überein (eigener Sentinel „Mir & unzugewiesene"); ein HTMX-Filter-Reload sammelt die gleichnamigen Bulk-Selects nicht mehr als leere Doppel-Parameter ein, die fremd-zugewiesene Aufgaben einblendeten.
- **Aufgaben-Sicht beim Bulk-Statuswechsel stabil** (#1134) — die „Kürzlich erledigt"-Liste ist an dieselbe Default-Eingrenzung gebunden wie die übrigen Listen, und eine explizite „Alle"-Sicht bleibt nach dem Statuswechsel erhalten; Listenanzeige und tatsächlicher Status laufen nicht mehr auseinander.
- **Aufgaben-Bulk-Auswahl & -Aktionsleiste** (#1132) — die Einzelauswahl per Checkbox wirkt im CSP-Alpine-Build wieder (Auswahl wird nach jedem Change frisch aus dem DOM abgeleitet), der Auswahlzähler bleibt konsistent und die Aktionsleiste erscheint auch bei rein manueller Auswahl.
- **Verständlicher Bulk-Berechtigungshinweis** (#1136, #1148) — eine Sammelaktion mit fremd-zugewiesenen Aufgaben nennt jetzt konkret, wie viele Aufgaben blockieren, zeigt das als Inline-Warnung über der Liste statt als nackte Forbidden-Seite und hält die blockierende Auswahl markiert. Die Alles-oder-nichts-Semantik bleibt erhalten (#583).
- **Bestätigung vor „Als erledigt markieren"** (#1147) — der abschließende Statuswechsel fragt in Detail-, Tabellen- und Bulk-Kontext konsistent nach, damit ein schneller Klick ihn nicht versehentlich auslöst.
- **Überfällige Aufgabe mit unverändertem Datum speicherbar** (#1131) — das statische HTML5-`min`=heute blockierte das Speichern eines bereits überfälligen Items nicht mehr; ein aktives Verschieben auf ein anderes Vergangenheits-Datum fängt weiterhin die Server-Validierung ab.
- **Verwaister Datei-Hilfetext entfernt** (#1143) — der Datei-Upload-Hinweis erschien fälschlich am Mehrfach-Auswahl-Feld „Leistungen" im Kontakt-Formular (Bedingung auf den echten Datei-Widget-Typ umgestellt).
- **Navigation: Systembereich nicht mehr fälschlich aktiv** — der Aktivzustand testete per Substring (`dashboard` ⊂ `system_dashboard`); ein neuer `split`-Template-Filter macht daraus einen exakten Element-Test über die gesamte Desktop- und Mobil-Navigation.
- **Standalone-Auth-Seiten scrollen bei hohem Inhalt** (#1062) — Login, MFA-Login/-Setup, Passwort-Reset und Lockout-Recovery schneiden Inhalt oberhalb des Viewports nicht mehr ab (`min-h-screen` statt `h-full`).
- **Legal-Hold-Zeitzonenkonsistenz** (#1191, #1192) — `is_active`, der Dashboard-SQL-Filter und der Enforcement-Pfad nutzen `timezone.localdate()` statt des naiven `date.today()`, sodass die Aktiv-/Abgelaufen-Grenze nahe Mitternacht nicht um einen Tag springt.

## [0.15.0] - 2026-06-16

Sicherheits- und Stabilisierungs-Release (Pre-Release) auf dem Weg zur Demo-Version: vertieft die Härtungsagenda nach v0.14.0 — eine Laufzeit-Autorisierungs-Matrix als dauerhafter Nachweis, ein datenbankweiter PII-Residue-Sweep nach Löschung und Retention, die Entkopplung der Vier-Augen-Löschfreigabe in einen rollenunabhängigen Genehmiger-Pool und die abschließende Härtung des Offline-Caches (ADR-022). Dazu der Datenbank-Major-Sprung auf PostgreSQL 18, Node 24 LTS in der Build-Toolchain sowie UI-Polish rund um die neue Arbeitszentrale und die Schichtübergabe. Keine Datenmodell-Brüche; Vorwärts-Migration ohne Datenverlust. Weiterhin **noch nicht für den Produktiveinsatz freigegeben**.

### Security

- **Datenbankweiter PII-Residue-Sweep nach Löschung & Retention** (#1083) — ein zeilenweiser Bedarfs-Scan über die facility-gescopten Tabellen deckte stille Klartext-Reste nach Anonymisierung/Retention auf. Behoben: `Event.data_json`/`search_text` werden bei `anonymize_client` redigiert (#1089), ebenso die WorkItem-bezogene `Activity` (#1090) und der Client-Target-Löschantrag (#1091); der Retention-Soft-Delete tilgt jetzt auch den `search_text` der Event-Zeile (#1092); Klienten-PII wird beim Schreiben aus `AuditLog.detail` minimiert (#1093); und der K-Anonymitäts-Retention-Pfad kaskadiert die Tilgung auf Fall-/Episoden-/Aufgaben-Freitext (ADR-023, #1094).
- **Klartext-Pseudonym bei Anonymisierung redigiert** (#1067) — das Klartext-Pseudonym wird bei der Klienten-Anonymisierung auch aus `Activity.summary` entfernt.
- **Offline-Cache abschließend gehärtet (ADR-022)** (#1100) — der verschlüsselte Offline-Snapshot setzt jetzt seine TTL durch und revalidiert serverseitig (#1110); der Cache-Zugriff wird bei Rollenwechsel und Deaktivierung entzogen (#1110); ein Idempotenzschlüssel verhindert Doppel-Submits beim Queue-Replay und `updated_at` wandert ins Event-Bundle für die Konflikterkennung (#1109); der Krypto-Schlüssel ist an die Session-Lebenszeit gekoppelt (#1065).
- **MFA-Backup-Code-Bestätigung serverseitig erzwungen** (#1118) — die Quittierung der Backup-Codes ist ein verbindliches Setup-Gate und wird serverseitig durchgesetzt, nicht nur im UI.
- **File-Vault-Downgrade abgewehrt** (#1069) — manipulierte Datei-Header (Downgrade-/Blanking-Versuch gegen verschlüsselte Chunks) werden abgelehnt statt als leerer Inhalt akzeptiert.
- **IDOR-Härtung bei Event-Update/-Delete** (#1072) — die Event-Bearbeitungs-/Lösch-Views sind gegen objektbezogene Zugriffe ohne Berechtigung abgesichert.
- **`client.notes` hinter staff+-Gate** (#1068) — das Freitext-Notizfeld wird im Web-Detail nur ab Rolle `staff` aufwärts gerendert.
- **CSV-Formel-Injection im Audit-Export neutralisiert** (#1064) — der AuditLog-Export entschärft führende Formelzeichen, analog zum bestehenden Schutz im Events-/Statistik-Export.
- **Legal-Hold im Papierkorb durchgesetzt** (#1066) — der Client-Papierkorb-Pfad respektiert jetzt einen aktiven Legal Hold.
- **Ratelimit für DSGVO-Export und Attachment-Download** (#1084) — beide Download-Pfade sind gegen Massenabruf rate-limitiert.
- **Fehlgeschlagene Sudo-Re-Authentifizierung auditiert** (#1084) — eine misslungene Sudo-Re-Auth schreibt einen `SUDO_MODE_FAILED`-AuditLog-Eintrag und zählt zu den kritischen Aktionen des Compliance-Checks.
- **Admin-CSP-Relax nur für `text/html`** (#1084) — die gelockerte CSP des Admin-Bereichs greift nur noch für HTML-Antworten, nicht für andere Content-Types.
- **Laufzeit-Autorisierungs-Matrix als dauerhafter Nachweis** (#1055) — eine parametrisierte Live-Test-Suite prüft am laufenden System alle Rollen über die URL-Patterns inklusive IDOR-Proben über Facility-Grenzen, Session-Cookie-Flags und Security-Header; Befunde sind als `KNOWN_GAPS` mit Folge-Issue dokumentiert.
- **Threat-Model auf HMAC-SHA256-Backup-Integrität nachgezogen** (#1099) — `docs/threat-model.md` beschrieb Backups noch als nur „AES-256-CBC verschlüsselt"; Asset-Tabelle und Trust-Boundary TB5 spiegeln jetzt den real ausgelieferten Encrypt-then-MAC-Schutz (detached HMAC-SHA256-Sidecar, der vor der Entschlüsselung geprüft wird).

### Added

- **Arbeitszentrale als Cockpit-Kopf der Start-Seite** (#1124) — eine schlanke Arbeitszentrale bündelt den Handlungsbedarf der Schicht an der Spitze der Start-Seite.
- **Team-Fokusbox in der Zeitstrom-Sidebar** (#1128) — eine Sidebar-Fokusbox bündelt den offenen Handlungsbedarf des Teams (eigenes Dashboard-Service-Modul).
- **Recht „Löschbestätigung" — Genehmiger-Pool entkoppelt von der Rolle** (#1053) — die zweite Person der Vier-Augen-Löschfreigabe ist jetzt über ein eigenes Recht (`can_confirm_deletion`) kuratierbar statt fest an die Leitungsrolle gebunden; das entdeadlockt den Workflow bei nur einer Leitung.
- **Deployte Version im Footer** (#1050) — das eingeloggte Layout zeigt die laufende Version im Footer (Login-Seite bleibt versionslos); im Demo-/Pilotbetrieb erleichtert das die Zuordnung.
- **Klarsicht-Toggle für Passwortfelder** (#1049) — ein Auge-Button blendet Passwörter ein/aus (`aria-pressed`), als wiederverwendbares Formularfeld-Pattern.

### Changed

- **PostgreSQL 16 → 18** (#1039) — Datenbank-Major-Upgrade: Image-Pins und PG18-Volume-Layout in allen Compose-Dateien, Drei-Rollen-/RLS-Verifikation und Major-Upgrade-Runbook (§ 13) nachgezogen.
- **Node 20 → 24 LTS** (#1075) — Build-Toolchain und CI laufen auf Node 24 LTS (Node 20 EOL 2026-04-30); CONTRIBUTING auf Node 24+ angehoben.
- **Schichtübergabe als Ansicht im Zeitstrom** (#1124) — die Übergabe ist in den Zeitstrom als Schicht-Ansicht gefaltet und die staff-Arbeitszentrale auf den reinen Handlungsbedarf verschlankt.
- **Aufgaben in der Hauptnavigation unter Zeitstrom** (#1126) — die operative Hauptnavigation ist neu geordnet, Aufgaben sitzen direkt unter dem Zeitstrom.
- **Englische Dokumentation vollständig synchronisiert** (#1078, #1071) — README.en, CONTRIBUTING.en und docs/en/* decken jetzt u. a. Arbeitszentrale, Lockout-Selbsthilfe (E-Mail/Backup-Code), Drei-Rollen-Datenbankmodell, Compliance-Dashboard, Genehmiger-Pool und datenschutzfreundliche externe Berichte ab.
- **Übersetzungs-Gate verschärft** (#1078) — `scripts/check_translation_versions.py` verlangt Übersetzungs-Sync mit jedem Minor-Release (`MAX_MINOR_BEHIND` 2 → 0); Teil der neuen EN-Sync-Policy „hartes Release-Gate".
- **Compose-Image-Pins vereinheitlicht** (#1082) — `docker-compose.staging.yml` und `docker-compose.prod.yml` ziehen den App-Image-Tag konsistent über `${APP_VERSION}` (vorher war staging hart auf das vier Minors alte `v0.10.2` gepinnt und der prod-Fallback ebenfalls veraltet). Der Release-Doc-Sync hält den Fallback künftig aktuell.
- **Release-Testprofile auf automatisiert-first umgestellt** (#1081) — `docs/testing/release-test-profiles.md` definiert nun ein verbindliches automatisiertes Gate (`make ci` + volle E2E + Autorisierungs-Matrix + `make release-gates`) als primären Release-Nachweis; die manuellen Profile sind auf den nicht-automatisierten Rest (visueller Augenschein, Pen-/Spot-Checks) reduziert und in der Release-Checkliste operativ verdrahtet.

### Fixed

- **Aufgaben-Zuweisung & -Sichtbarkeit korrigiert** (#1125) — nicht zugewiesene Aufgaben werden wieder als mutierbare Teamaufgaben behandelt, die Assistenz ist als Aufgaben-Empfängerin zuweisbar, und explizite Filter zeigen alle Aufgaben der Einrichtung.
- **Schichtübergabe präzisiert** (#1120, #1121, #1122) — überfällige offene Aufgaben werden in der Übergabe markiert, die Überschrift konkreter benannt und die redundante Aktivitäten-Kachel entfernt.
- **Redirect nach Löschantrag-Review kontextsensitiv** (#1119) — die Weiterleitung nach der Review führt kontextgerecht zurück, abgesichert über den zentralen Open-Redirect-Schutz.
- **Eigener Löschantrag ohne Genehmigen-Button** (#1052) — der eigene Antrag zeigt einen Statushinweis „wartet auf zweite Person" statt einer toten Genehmigen-Aktion (Vier-Augen-Konsistenz).
- **Super-Admin-Dashboard verlinkt `/system/audit/`** (#1048) — die Audit-Karten der System-Arbeitszentrale zeigen für `super_admin` auf den korrekten Systembereich statt in einen 403.
- **Papierkorb-Link für `facility_admin`** (#1040) — der Papierkorb-Link erscheint in der Klientenliste für `facility_admin`, nicht für Staff.
- **Datums-/Zeitwerte in `data_json` als ISO-Strings normalisiert** (#1073) — Datums- und Zeitwerte werden konsistent als ISO-Strings abgelegt.
- **Seed-Umgebungs-Guard + atomarer Flush** (#1040) — das `seed`-Command verlangt ein explizites `SEED_ALLOWED` (Schutz gegen versehentliches `--flush` auf Prod) und führt den Flush atomar aus.
- **Wartungsjobs mit `--pull never`** (#1047) — `run-as-admin.sh` setzt `--pull never`, damit Retention/Breach/MV-Refresh nicht still gegen ein neu gezogenes Image brechen (per Architektur-Guard abgesichert).
- **Tabellen-Ownership nach Migrate-als-Admin normalisiert** (#1085) — der Migrate-Job normalisiert die Ownership frisch erstellter Tabellen auf den DB-Owner, sodass die App-Rolle zugreifen kann (kein `permission denied` mehr); generalisiert das frühere per-Tabelle-Muster.
- **`postgres-init`-Rollenanlage auf `\gexec`** (#1039) — die Anlage der DB-Rollen im Init-Skript läuft robust über `\gexec`.

### Docs

- **DSGVO-Wegweiser** (#1104, #1105) — ein nach Zielgruppe getaggter Wegweiser (Artikel → Quelle) als Einstiegspunkt, mit DSGVO-Begriffen im Glossar und vollständigem EN-Mirror des Datenschutz-Hubs.
- **ADR-022 auf „Accepted"** (#1100) — die Offline-Snapshot-Entscheidung ist nach Security-Review und Pen-Test auf den eingegrenzten Scope „Accepted" gesetzt.
- **ADR-023 aktualisiert** (#1106) — dokumentiert, dass die K-Anonymitäts-Retention die Freitext-Tilgung kaskadiert.
- **PostgreSQL-18-Runbook (§ 13)** (#1039) — Major-Upgrade-Pfad und PG18-Referenzen im Ops-Runbook ergänzt.

## [0.14.0] - 2026-06-11

Sicherheits- und Stabilisierungs-Release (Pre-Release): konsolidiert eine breite Hardening-Runde aus dem Post-v0.13.0-Plan (#1016) — Privilege-Escalation- und Admin-Scoping-Abdichtung, MFA-/Sudo-Verschärfung, authentifizierte Backups, Datei-Chunk-Binding v2, Webhook-SSRF-Härtung — plus UI/UX-Polish (#1024). Keine Datenmodell-Brüche; Vorwärts-Migration ohne Datenverlust. Weiterhin **noch nicht für den Produktiveinsatz freigegeben**.

### Security

- **Privilege-Escalation & Admin-Facility-Scoping abgedichtet** (#1016) — `facility_admin` kann keine `super_admin`-Rechte mehr erlangen; der `facility`-FK wird zentral als Single Source of Truth gescopt und erzwungen; `OrganizationAdmin` ist auf `super_admin` beschränkt. E2E-Regressionstests sichern jede der drei Abdichtungen.
- **Sudo-Mode verlangt frischen zweiten Faktor** (#1016) — bei aktivem TOTP erfordert die Sudo-Re-Authentifizierung neben dem Passwort einen frischen OTP-Code, nicht nur das Passwort.
- **MFA-Default-Enforcement für privilegierte Rollen** (#1016) — `super_admin` und `facility_admin` werden hinter `MFA_ENFORCE_PRIVILEGED_ROLES` standardmäßig zur MFA-Einrichtung gezwungen.
- **Vier-Augen-Prinzip bei Löschfreigabe serverseitig erzwungen** (#1016) — `approve_deletion` prüft die Trennung von Antragsteller und Genehmiger im Service-Layer als SSoT, nicht nur im UI.
- **Row-Level-Security-Lücke geschlossen** (#1016) — der `super_admin`-Bypass wird auf vier zuvor fehlende facility-Policies nachgezogen.
- **Authentifizierte Backups** (#1016) — Backup-Artefakte sind per Encrypt-then-MAC (HMAC-SHA256) gegen Manipulation geschützt.
- **Datei-Chunk-Binding v2 + Header-Tamper-Schutz** (#1016) — verschlüsselte Datei-Chunks sind an Storage-ID und Position gebunden, mit Downgrade-Erkennung gegen das Vertauschen oder Wiedereinspielen alter Chunks; In-Place-Encrypt schließt eine Klartext-Zwischenstufe.
- **Klartext-PII-Heilung** (#1016) — `reencrypt_fields` verschlüsselt nachträglich etwaige Klartext-PII in `Event.data_json`.
- **Webhook-SSRF-Härtung** (#1016) — Breach-Webhooks folgen keinen Redirects mehr, sind per DNS-Pinning gegen Rebinding geschützt und der Guard prüft `not is_global` statt einer Negativliste.
- **Geteilter DatabaseCache + Ratelimit-Backend** (#1016) — gemeinsamer DB-Cache, Ratelimits laufen über `RATELIMIT_USE_CACHE` statt prozesslokal.
- **k-Anonymität: sekundäre Suppression** (#1016) — komplementäre Offenlegung über sich ergänzende Auswertungen wird durch zusätzliche Unterdrückung verhindert.
- **Retention-/Audit-Pruning gehärtet** (#1016) — `SECURITY_VIOLATION`- und `RETENTION_RUN_COMPLETED`-Einträge sind vom Pruning ausgenommen; AuditLog-Pruning rechnet kalendergenau statt mit 30-Tage-Näherung.
- **Härtung am Rand** (#1016) — `/health` mit reduzierter Detailtiefe plus Cache/Rate-Limit, `X-Robots-Tag: noindex` als Default, CSP-Report über den kanonischen `get_client_ip`, verengte Entschlüsselungs-Fehlerbehandlung (nur `InvalidToken`) und ein Settings-Guard-Cluster gegen Fehlkonfiguration in Produktion.
- **Django 6.0.5 → 6.0.6** — Security-Patch-Release, schließt fünf von Django veröffentlichte Schwachstellen (PYSEC-2026-197 bis -201). Reiner Versions-Bump im `<6.1`-Rahmen, keine API-/Verhaltensänderung.

### Added

- **HTMX-Fehler-Toast, Lade-Spinner & Doppel-Submit-Schutz** (#1024) — fehlgeschlagene HTMX-Requests zeigen einen Toast statt stillem Scheitern; laufende Requests bekommen einen Spinner, und Doppel-Submits werden unterbunden.
- **Inline-Edit für Ziele/Meilensteine** (#1024) — Ziele und Meilensteine sind direkt editierbar, mit Bestätigung und Leereingabe-Validierung.

### Changed

- **Wiederverwendbare UI-Komponenten** (#1024) — gemeinsame `components/_badge.html` und `components/_form_field.html`, `@layer components`-Klassen für `.btn-primary`/`.btn-secondary`/`.card`/`.badge` sowie eine gemeinsame Alpine-Basis für Autocomplete-Felder (Dedup) vereinheitlichen das Markup.
- **Interne Refactorings** (#1016) — System-AuditLog-Filter zentralisiert (DRY), totes `FacilityScopedViewMixin` entfernt, HTMX-Detection an das Projektmuster angeglichen. Kein Verhaltensunterschied.
- **i18n EN-Katalog synchronisiert** (#1024) — englischer Übersetzungskatalog auf den aktuellen Stand gebracht, übersetzt und kompiliert.

### Fixed

- **Lost-Update-Schutz** (#1016) — `defer_count` wird atomar via `F()` hochgezählt und WorkItem-Bulk-Pfade nutzen `select_for_update`; gleichzeitige Bearbeitungen überschreiben sich nicht mehr.
- **CSP-Konformität in der UI** (#1016, #1024) — Reload-Buttons ohne `javascript:`-URI, Inbox-Select-All ohne `$event` in der Alpine-Expression, `Ctrl+Enter` nutzt `requestSubmit()` statt `submit()`.
- **MFA-Logout-Links als POST-Form** — die MFA-Logout-Aktion ist ein POST-Formular statt eines GET-Anchors (behebt einen 405).
- **Cache-Tabelle dem DB-Owner zugewiesen** (#1030) — die Cache-Tabelle gehört der korrekten DB-Rolle, sodass die App-Rolle zugreifen kann (`permission denied` behoben).
- **Deploy- & Ops-Härtung** — `check_db_roles` als Fail-Fast-Gate vor der Migration und Identifikation der App-Rolle über `POSTGRES_APP_USER`, Wartungs-Cron als BYPASSRLS-Admin-Rolle mit Fail-loud-Guard, expliziter ClamAV-TCP-3310-Scan-Pfad-Vertrag, `GRANT SET ON PARAMETER session_replication_role` für den Anonymisierungs-Pfad, lauffähiges `dev-seed` via `--entrypoint python`.

## [0.13.3] - 2026-06-02

Patch-Release (Pre-Release): härtet einen flaky E2E-Test, der beim v0.13.2-Release-CI an einer HTMX-Swap-Race scheiterte. Reine Test-Stabilität — **kein** App-Code, keine Verhaltens- oder API-Änderung. Weiterhin **noch nicht für den Produktiveinsatz freigegeben**.

### Fixed

- **Flaky E2E-Test gehärtet** (#1013) — `test_event_save_and_appears_in_detail` füllte die per HTMX nachgeladenen FieldTemplate-Felder, bevor der DOM-Swap abgeschlossen war; unter CI-Last ersetzte der Swap das gerade befüllte Feld (`AssertionError: HTMX-Swap hat dauer ueberschrieben`). Jetzt wird auf den `event_fields_partial`-Response gewartet und mit Verify-Retry gefüllt (Muster aus [`test_statistics_dashboard.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/e2e/test_statistics_dashboard.py)). Kein Produktivcode betroffen.

## [0.13.2] - 2026-06-02

Patch-Release (Pre-Release): vollständige Triage und Behebung aller **29 offenen CodeQL-Code-Scanning-Alerts** auf dem öffentlichen Mirror (#1011). Schwerpunkt ist defensive Security-Härtung (Open-Redirect-Schutz, keine Exception-Details in Fehlerantworten, einheitliche Download-Header); keine der gemeldeten Stellen war real ausnutzbar. Weiterhin **noch nicht für den Produktiveinsatz freigegeben**.

### Security

- **Open-Redirect-Schutz gehärtet** (#1011) — der zentrale `?next=`-Sanitizer `safe_redirect_path` (Sudo-Mode, WorkItem-Status-Redirects) nutzt jetzt Django's [`url_has_allowed_host_and_scheme`](https://github.com/anlaufstelle/app/blob/main/src/core/views/utils.py). Das deckt zusätzliche Bypass-Klassen (u.a. Backslash- und protokoll-relative Weiterleitungen) gegenüber einer eigenen Pfad-Prüfung ab. Same-origin-Pfade bleiben unverändert erlaubt.
- **Keine Exception-Strings in Bulk-Fehlerantworten** (#1011) — die Bulk-WorkItem-Aktionen (Status/Priorität/Zuweisung) liefern Validierungsfehler über eine kontrollierte, übersetzte Meldung statt `str(exception)`. Unerwartete Exceptions werden nicht mehr abgefangen und in die 400-Antwort gespiegelt, sondern propagieren als 500 (serverseitig geloggt, nicht exponiert).
- **Einheitliche Download-Header beim Audit-Export** (#1011) — der Cross-Facility-Audit-Export (CSV/JSON) läuft jetzt über den zentralen [`safe_download_response`](https://github.com/anlaufstelle/app/blob/main/src/core/utils/downloads.py)-Builder, inklusive `Content-Disposition: attachment`, RFC-5987-Dateiname und `X-Content-Type-Options: nosniff` — konsistent mit allen anderen Downloads.

### Fixed

- **Autosave-Entwurf zuverlässig verwerfen** (#1011) — beim Klick auf einen „Entwurf verwerfen"-Link wird das (stets asynchrone) Löschen des Offline-Entwurfs direkt verkettet statt über einen toten Laufzeit-Guard; das Navigationsziel wird in jedem Fall nach Abschluss angesteuert.

### Changed

- **Code-Hygiene aus der CodeQL-Triage** (#1011) — leere `except`-Blöcke aufgelöst (`.filter().first()` statt `try/except DoesNotExist`, `contextlib.suppress`, gezieltes Debug-Logging für defekte Daten), toter/ungenutzter Code entfernt und kleinere E2E-Test-Hygiene (explizite String-Konkatenation, nicht-redundante Assertions). Rein intern, ohne Verhaltensänderung.

## [0.13.1] - 2026-06-01

Patch-Release (Pre-Release) mit Schwerpunkt **Außenwirkung und Aufräumen**: neue, automatisiert erzeugte Screenshots in Deutsch und Englisch (Desktop + Mobil) samt vollständiger Galerie-Seiten, ein wiederverwendbares Screenshot-Tooling, präsentablere Seed-Daten und das Schließen einiger bei v0.13.0 offen gebliebener Enden. Weiterhin **noch nicht für den Produktiveinsatz freigegeben**.

### Added

- **Screenshot-Generator `manage.py screenshot`** (#1005) — Neuer Management-Command erzeugt Doku-Screenshots reproduzierbar per Playwright gegen eine laufende Instanz: Login als Seed-User, Sprachumschaltung (DE/EN) über `preferred_language`, deklarative Shot-Liste, Ausgabe als **WebP** (via Pillow) in Desktop (1280×800) und Mobil (375px). Neues Make-Target [`docs-screens`](https://github.com/anlaufstelle/app/blob/main/Makefile) fährt den kompletten Lauf (Seed → Server → Generieren → Stop). `/system/`-Screens (Sudo-Mode) sind vorerst ausgeklammert.
- **QuickTemplate-Seeds** (#1004) — Das seit #494 existierende `QuickTemplate`-Modell (vorbefüllte Schnelleintrags-Vorlagen) wurde bisher nie geseedet. Neues Seed-Modul [`core/seed/quick_templates.py`](https://github.com/anlaufstelle/app/blob/main/src/core/seed/quick_templates.py) erzeugt pro Einrichtung realistische, facility-gescopte Vorlagen für alle Scales (idempotent); in den Seed-Lauf verdrahtet. Demo-/Screenshot-Daten zeigen den Schnelleintrags-Workflow jetzt mit.

### Changed

- **Dependency-Bumps (Dependabot)** — `ruff` 0.15.14→0.15.15, `sentry-sdk[django]` 2.60.0→2.61.0, `django-stubs` 6.0.4→6.0.5. Begleitend der `requirements.txt`-Header durch `pip-compile` normalisiert, damit `make deps-check` grün bleibt.

### Docs

- **Neue Screenshots + Galerie-Seiten** (#1005) — [`README.md`](https://github.com/anlaufstelle/app/blob/main/README.md) und [`README.en.md`](https://github.com/anlaufstelle/app/blob/main/README.en.md) zeigen aktuelle WebP-Highlights (inkl. Arbeitszentrale und Ereignis-Erfassung), neue Galerie-Seiten [`docs/screenshots.md`](https://github.com/anlaufstelle/app/blob/main/docs/screenshots.md) und [`docs/screenshots.en.md`](https://github.com/anlaufstelle/app/blob/main/docs/screenshots.en.md) bilden den vollen Funktionsumfang ab. Veraltete Hero-PNGs entfernt.
- **K-Anonymität konzeptionell erklärt** (#999) — [`docs/glossar.md`](https://github.com/anlaufstelle/app/blob/main/docs/glossar.md) erhält eine ausformulierte Definition mit Alltagsbeispiel (k=5, Unterdrückung kleiner Merkmalsgruppen), Verweis auf das Setting `k_anonymity_threshold` (Default 5) und Abgrenzung zur Pseudonymisierung; der frühere „(siehe unten)"-Platzhalter zeigte ins Leere.
- **Seed-Scale-Tabelle** (#1004) — [`CONTRIBUTING.md`](https://github.com/anlaufstelle/app/blob/main/CONTRIBUTING.md) um die Spalte „Quick-Vorlagen" ergänzt.

### Fixed

- **clamav-TCP-Scan-Pfad als Folge-Issue erfasst** (#1006) — Der bei #938 angekündigte, aber nie angelegte Follow-up zum TCP-3310-Scan-Vertrag von `virus_scan.py` ist jetzt als eigenes Issue dokumentiert (fail-closed, kein aktiver Defekt).

## [0.13.0] - 2026-05-30

Minor-Release (Pre-Release) mit Schwerpunkt Sicherheits-Härtung, Datenschutz und neuen Betriebs-Werkzeugen — **noch nicht für den Produktiveinsatz freigegeben**. Security: rollen-gegatete Custom-AdminSite mit Sudo-Pflicht, gehärtete GitHub-Actions-Lieferkette (SHA-Pinning aller Refs + Minimal-`permissions:`) und ein 3-Rollen-Postgres-Modell in Produktion, das eine RLS-Lücke schließt (Breaking-Change für Self-Hoster). Datenschutz und Betrieb: K-Anonymität im Retention-Löschpfad, DSGVO-Audit für den Vier-Augen-Lösch-Workflow, datenschutzfreundliche externe Berichte, Compliance-Dashboard mit 14 Checks und Self-Service-Lockout-Recovery — dazu ein großer Foundation-/Vereinfachungs-Refactor (Service-Layer in Domänen-Subpackages, typisierte Audit-Helper, Architektur-Tests) und der vollständige englische Übersetzungskatalog.

### Security

- **Custom AdminSite mit Rollen-Gate + Sudo-Mode** (#785) — `/admin-mgmt/` wechselt von Default-Django-AdminSite auf eine `AnlaufstelleAdminSite` (erbt von `UnfoldAdminSite`). `has_permission()` erlaubt nur `super_admin` und `facility_admin` (`lead`/`staff`/`assistant` werden geblockt, auch wenn `is_staff=True`). Plus Sudo-Mode-Pflicht über `login()`-Override — eingeloggter berechtigter User ohne Sudo wird zu `/sudo/?next=...` redirected. `FacilityScopedAdminMixin` filtert `ModelAdmin.get_queryset()` für `facility_admin` auf die eigene Facility; `super_admin` sieht alle Facilities. EventHistoryAdmin und EventAttachmentAdmin filtern über `event__facility`. Architektur-Test sichert, dass alle 17 Modelle an der Custom-Site hängen und kein Modell am Default-`admin.site` landet. 16 Unit + 5 E2E Tests. `is_staff` im Seed bleibt unverändert — Defense-in-Depth ohne Seed-Drift. [`docs/security-notes.md`](https://github.com/anlaufstelle/app/blob/main/docs/security-notes.md) Rollen-Matrix korrigiert.
- **GitHub-Actions-Härtung: Minimal-`permissions:`-Blocks** (#888) — Workflows `e2e`, `lint`, `test`, `perf-nightly` haben jetzt expliziten Top-Level-`permissions:`-Block (`contents: read`, plus `issues: write` für `perf-nightly` wegen `peter-evans/create-issue-from-file`). `codeql`, `dev-image`, `release` hatten bereits Job-level Permissions. Defense-in-Depth-Hygiene nach Mini-Shai-Hulud-Vorfall (npm/PyPI 2026-05-11) — Anlaufstelle ist vom Vorfall selbst **nicht betroffen** (keine kompromittierten Pakete in den Lockfiles, kein `pull_request_target`, Repo ist privat).
- **GitHub-Actions-Härtung: SHA-Pinning aller Actions** (#888) — Alle 42 `uses:`-Referenzen über 7 Workflows verweisen jetzt auf 40-stellige Commit-SHAs statt Major-Tags. Schützt vor Tag-Verschiebung bei einer hypothetischen Action-Repo-Übernahme (vgl. TanStack-Postmortem: Tag-Mutation war ein Baustein des Angriffs). Dependabot ist über `github-actions`-Ökosystem in [`.github/dependabot.yml`](https://github.com/anlaufstelle/app/blob/main/.github/dependabot.yml) bereits konfiguriert und aktualisiert SHA + Tag-Kommentar bei neuen Versionen automatisch.
- **Drei-Rollen-Postgres-Modell in Produktion** (#902) — Prod-Datenbank-Rollen gehärtet. Prod hebt jetzt auf das gleiche Drei-Rollen-Modell wie Dev: hartkodierter `postgres`-Bootstrap-Superuser legt nur die App-Rollen an; App-Rolle (`POSTGRES_USER`) ist `NOSUPERUSER NOBYPASSRLS`; Admin-Rolle (`POSTGRES_ADMIN_USER`) ist `NOSUPERUSER BYPASSRLS` für Migrationen/Seed/Retention-Pruning. Neues `manage.py check_db_roles`-CLI prüft die Topologie zur Laufzeit (Exit-Code 0/1/2). **Breaking-Change für Self-Hoster**: `.env` muss um `POSTGRES_BOOTSTRAP_PASSWORD`, `POSTGRES_ADMIN_USER`, `POSTGRES_ADMIN_PASSWORD` ergänzt werden — siehe aktualisierte [`.env.example`](https://github.com/anlaufstelle/app/blob/main/.env.example) und [`docs/coolify-deployment.md`](https://github.com/anlaufstelle/app/blob/main/docs/coolify-deployment.md).

### Added

- **Hintergrundjob-Status-Block in der `/system/`-Übersicht** (#977) — Die Systembereich-Startseite zeigt jetzt einen kompakten Block „Hintergrundjobs" mit einer Status-Zeile je Cron-Job (Backup, Retention, Snapshots, Breach-Scan, MV-Refresh), Last-Run-Info, Gesamt-Indikator (grün/gelb/rot/grau) und Link aufs Compliance-Dashboard. Neue `cron_job_checks()`-Aggregatorfunktion bündelt die fünf job-bezogenen `ComplianceCheck`-Helfer DRY für die `/system/`-Übersicht und `/system/compliance/`; `aggregate_checks()` teilt sich denselben defensiven `_run_helpers()`-Runner. Der Restore-Test bleibt bewusst draußen (manueller Operator-Workflow, kein Cron-Job). Unit-, View- und E2E-Tests.
- **Hintergrundjob-Frische im Compliance-Dashboard** (#794, #919) — Drei neue AuditLog-Aktionen `SNAPSHOT_RUN_COMPLETED`, `BREACH_SCAN_COMPLETED` und `MV_REFRESH_COMPLETED` (Migration [`0090_auditlog_cron_actions.py`](https://github.com/anlaufstelle/app/blob/main/src/core/migrations/0090_auditlog_cron_actions.py)) werden vom jeweiligen Cron-Command nach erfolgreichem Lauf geschrieben — analog zum `RETENTION_RUN_COMPLETED`-Marker aus #919. Das Compliance-Dashboard [`/system/compliance/`](https://github.com/anlaufstelle/app/blob/main/src/core/views/system/compliance.py) bekommt eine eigene Kategorie **„Hintergrundjobs"** mit Last-Run-Checks für Statistik-Snapshot, Breach-Scan und Materialized-View-Refresh. Die systemd-Timer für die Dev-Umgebung werden bei jedem Deploy installiert (siehe Fixed-Sektion, #980).
- **Lockout-Recovery — Token-Mail, Backup-Code, Auto-Unlock per Password-Reset** (#869) — Drei neue Self-Service-Pfade ergaenzen CLI/Admin-Action: (1) Erfolgreiches Password-Reset schreibt jetzt automatisch einen `LOGIN_UNLOCK`-AuditLog (Trigger `password_reset`) — der User braucht keinen separaten Admin-Eingriff mehr, wenn er ohnehin gerade ein neues Passwort setzt. Implementiert ueber neue `CustomPasswordResetConfirmView` in [`core/views/auth.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/auth.py). (2) Dedizierter Recovery-Token-Flow unter `/account/recovery/`: E-Mail-Eingabe + `TimestampSigner`-basierter Token (30 Minuten TTL, Salt `lockout-recovery.v1`, kein neues Modell) in [`core/services/lockout_recovery.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/lockout_recovery.py); Anti-Enumeration via konstanter Response, Audit ueber bestehende `PASSWORD_RESET_REQUESTED`-Action mit `flow: lockout_recovery_token`. (3) MFA-Backup-Code-Recovery unter `/account/recovery/backup-code/`: Username + Backup-Code -> `verify_backup_code()` -> `LOGIN_UNLOCK` (Trigger `backup_code`) + `BACKUP_CODES_USED` (Flow `lockout_recovery`). Login-Seite [`auth/login.html`](https://github.com/anlaufstelle/app/blob/main/src/templates/auth/login.html) zeigt die drei Recovery-Links prominent + gruenen Banner bei `?recovered=1`. `unlock_user()` in [`core/services/login_lockout.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/login_lockout.py) bekommt `trigger=` Parameter (`admin`/`cli`/`password_reset`/`recovery_token`/`backup_code`) fuer Forensik. 11 Unit-Tests + 6 E2E-Smokes. Admin-/User-Guide aktualisiert.
- **Rollenbezogene Arbeitszentrale** (#920) — Neue Landingpage `/start/` ([`core:dashboard`](https://github.com/anlaufstelle/app/blob/main/src/core/views/dashboard.py)) liefert pro Rolle ein eigenes Template mit verdichteten Kacheln auf bestehende Daten — keine neuen Modelle, keine neuen Permissions. Fachkraft/Assistent: heutige Kontakte, eigene offene Aufgaben, zuletzt bearbeitete Personen ([`role_staff.html`](https://github.com/anlaufstelle/app/blob/main/src/templates/core/dashboard/role_staff.html)). Leitung: ausstehende Loeschantraege, Retention-Vorschlaege, aktive Legal Holds, letzter Statistik-Snapshot ([`role_lead.html`](https://github.com/anlaufstelle/app/blob/main/src/templates/core/dashboard/role_lead.html)). Facility-Admin: User ohne MFA, Konfigurations-Warnungen (MFA-Pflicht, K-Anon) ([`role_facility_admin.html`](https://github.com/anlaufstelle/app/blob/main/src/templates/core/dashboard/role_facility_admin.html)). Super-Admin: Anzahl Mandanten/aktive Benutzer, Audit-Events der letzten 24 h, kritische Audit-Aktionen ([`role_super_admin.html`](https://github.com/anlaufstelle/app/blob/main/src/templates/core/dashboard/role_super_admin.html)). Daten-Aggregation im neuen Service [`core/services/dashboard.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/dashboard.py). Sidebar bekommt einen neuen Nav-Link "Arbeitszentrale" ueber dem Zeitstrom. `/` bleibt unveraendert beim Zeitstrom — die Arbeitszentrale ist ein zusaetzlicher Einstieg, keine Verdraengung. 10 Service-Tests + 6 View-Tests + 6 E2E-Smokes ([`test_dashboard_roles.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/e2e/test_dashboard_roles.py)).
- **K-Anonymisierung im Retention-Pfad verdrahtet** (#780) — Das Facility-Setting `retention_use_k_anonymization` (Migration [`0049_k_anonymization.py`](https://github.com/anlaufstelle/app/blob/main/src/core/migrations/0049_k_anonymization.py)) und der Service [`k_anonymize_client()`](https://github.com/anlaufstelle/app/blob/main/src/core/services/k_anonymization.py) existierten seit #535, wurden aber vom Retention-Pfad bisher nirgends gelesen — ein Charakterisierungs-Test aus #776 sicherte den Status Quo, damit ein zukünftiger Wire-Up bewusst aktiviert wird. Mit diesem Wire-Up ruft [`anonymize_clients()`](https://github.com/anlaufstelle/app/blob/main/src/core/retention/anonymization.py) bei `retention_use_k_anonymization=True` jetzt `k_anonymize_client(client, k=settings.k_anonymity_threshold)` statt `client.anonymize()`; der Audit-Eintrag bekommt `category="client_k_anonymized"` zur Unterscheidung vom Hard-Anonymize-Pfad. Bestehendes Verhalten bei Setting=False (Default) unverändert — Pseudonym beginnt weiterhin mit `Gelöscht-`. 5 Tests in [`test_retention_k_anonymization.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/test_retention_k_anonymization.py): zwei Service-Pfad-Tests (Setting True/False), ein End-to-End-Command-Test via `call_command("enforce_retention")`, plus zwei Bucket-Größen-Tests.
- **DSGVO-Audit für 4-Augen-Lösch-Workflow** (#932) — DSGVO Art. 5 (2) Rechenschaftspflicht: jeder Workflow-Schritt (Antrag/Approve/Reject) schreibt einen dedizierten `AuditLog`-Eintrag. Drei neue generische `AuditLog.Action`-Werte (`DELETION_REQUESTED`, `DELETION_APPROVED`, `DELETION_REJECTED`); Migration [`0089_deletion_audit_actions.py`](https://github.com/anlaufstelle/app/blob/main/src/core/migrations/0089_deletion_audit_actions.py). Service-Calls in [`events/deletion.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/events/deletion.py) (`request_deletion`, `approve_deletion`, `reject_deletion`) und [`clients.py::request_client_deletion`](https://github.com/anlaufstelle/app/blob/main/src/core/services/clients.py). Target-Type unterscheidet `DeletionRequest` (Workflow-Stufen) von `Event`/`Client` (eigentliche Löschung via `soft_delete_event`). Idempotenz-Schutz: wenn `request_deletion` einen existierenden PENDING-Antrag zurückgibt, wird KEIN doppeltes Audit-Event geschrieben. 6 TDD-Tests.
- **Datenschutzfreundliche externe Berichte** (#921) — Neuer Service [`core/services/external_report.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/external_report.py): wrappt `statistics.get_statistics()`, entfernt `top_clients` (Pseudonym-Ranking) komplett, wendet K-Anonymity-Schwelle auf Aggregate an (Werte < Schwelle → `count=None`, `suppressed=True`). Datenschutzprofil-Metadaten am Report-Kopf (Facility, Zeitraum, K-Anon-Schwelle, generated_at, `privacy_profile=external`). Reuses bestehendes [`Settings.k_anonymity_threshold`](https://github.com/anlaufstelle/app/blob/main/src/core/models/settings.py) (Default 5) — kein neues Setting, keine Migration nötig. Neuer `ExternalReportView` unter `/statistics/external/` mit HTML (Template [`external_report.html`](https://github.com/anlaufstelle/app/blob/main/src/templates/core/statistics/external_report.html)) und JSON-Endpoint (`?format=json`). `AuditLog.EXPORT`-Eintrag pro Aufruf mit Format + Schwelle im `detail`. 14 Unit + 3 E2E Tests.
- **Release-Sanitize-Tool** (#885) — Neues generisches Sanitize-Tool [`release/sanitize_release_tree.py`](https://github.com/anlaufstelle/app/blob/main/release/sanitize_release_tree.py) für Tree-Snapshot dev → stage → app. Ersetzt das externe `sanitize-changelog.py` um alle Markdown-/Text-Dateien im Release-Tree, nicht nur CHANGELOG.md. 9 Pattern-Klassen abgedeckt (Issue-Links, PR-Links, Refs-Inline, blob/tree/commit/compare-URLs, ghcr.io-Refs, Plain-Text-Refs), plus 3 Schutz-Tests (Code-Block-Schutz, Idempotenz, File-Walk + Pre-Push-Grep-Check). 12 TDD-Tests. **User-Action:** In lokaler `build-release.sh` den `release/`-Ordner zu Excludes hinzufügen, damit das Tooling nicht in stage/app landet.
- **Compliance-Dashboard `/system/compliance/`** (#919) — Neuer Superadmin-Bereich mit elf typed Checks aus §2.4: drei DB-Rollen-Checks (über `check_db_roles()` aus #902), Backup-Alter, Restore-Verified-Alter, ClamAV-Ping + Signaturalter, Retention-Run-Alter, MFA-Quote (Superadmin/Facility-Admin/Lead), pending Django-Migrationen, App/Django/Python-Versionen, kritische Audit-Events der letzten 24 h. Aggregator in [`core/services/compliance.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/compliance.py) ist defensiv: ein Check-Fehler kippt das Dashboard nicht (Status `unknown`). Status-Schema: `ok`/`warning`/`critical`/`unknown` mit Summary-Badges, Cards pro Kategorie, optionalem Action-Hint. Begleitend zwei neue AuditLog-Aktionen `RETENTION_RUN_COMPLETED` und `RESTORE_VERIFIED` (mit CLI `manage.py mark_restore_verified`) als Frische-Marker, sowie eine neue `signature_info()`-Funktion in [`core/services/virus_scan.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/virus_scan.py) für das ClamAV-Signaturalter. `SYSTEM_VIEW`-AuditLog pro Aufruf, `facility_admin` → 403.
- **`BACKUP_DIR` im web-Container sichtbar** (#871) — Die Health-Card im `/system/`-Dashboard ruft jetzt `system_health.last_backup_info()` auf, das `BACKUP_DIR` zur Laufzeit lesen kann. Mount in [`docker-compose.dev.yml`](https://github.com/anlaufstelle/app/blob/main/docker-compose.dev.yml) und Default-Pfad in [`deploy/backup.sh`](https://github.com/anlaufstelle/app/blob/main/deploy/backup.sh) angepasst.

### Changed

- **Dependency-Bumps (Dependabot)** — Python-Pakete: `sentry-sdk[django]` 2.59.0→2.60.0 (#936), `django-unfold` 0.93.0→0.94.0 (#965), `playwright` 1.59.0→1.60.0 (#968), `pytest-playwright` 0.7.2→0.8.0 (#967), `ruff` 0.15.12→0.15.14 (#966). GitHub-Actions (SHA-gepinnt, Refs #888): `docker/setup-buildx-action` v4.0.0→v4.1.0 (#961), `docker/login-action` v4.1.0→v4.2.0 (#962), `github/codeql-action` v4.35.4→v4.36.0 (#963), `docker/build-push-action` v7.1.0→v7.2.0 (#964).
- **Service-Layer in Domänen-Subpackages reorganisiert** (#959) — Die bisher flache [`core/services/`](https://github.com/anlaufstelle/app/blob/main/src/core/services/)-Ebene ist in neun thematische Subpackages gegliedert: `audit/` (audit + audit_hash), `security/` (Auth-/MFA-/Lockout-Files), `compliance/` (breach_detection, vvt, sensitivity, k_anonymization — zusätzlich zum Compliance-Dashboard-Split aus #958), `events/` (feed; der leere `event.py`-Stub entfällt), `file_vault/` (encryption + virus_scan ziehen ein), `client/` (clients, client_export, dsgvo_package), `case/` (cases, goals, workitems, handover), `dashboard/` und `system/`. Jedes `__init__.py` re-exportiert die Public API — keine Aufrufer-, URL- oder Template-Änderung nötig. Setzt die Vereinfachungs-Triage aus RFC #959 fort (analog zum Subpackage-Muster aus #958).
- **Grünes-Haus-Branding für Favicon und PWA-Icons** — Neues [`favicon.svg`](https://github.com/anlaufstelle/app/blob/main/src/static/icons/favicon.svg) spiegelt das Login-Logo (grünes rounded-xl-Quadrat in Akzentfarbe `#006547` mit weißem Heroicons-Haus) statt des bisherigen blauen „A"-Icons; `rel=icon`-Link in [`base.html`](https://github.com/anlaufstelle/app/blob/main/src/templates/base.html) und [`auth/login.html`](https://github.com/anlaufstelle/app/blob/main/src/templates/auth/login.html). Auch die PWA-/Apple-Touch-Icons (`icon-192`/`icon-512`, PNG + SVG) sind auf das vollflächige grüne Quadrat mit Haus umgestellt — konsistent mit `manifest purpose 'any maskable'` (Haus in der Safe-Zone zentriert). PNGs via headless-Chrome aus den SVGs gerendert.
- **URL-Schema: HTMX-Fragmente und JSON-APIs getrennt** (#848) — Bisher mischte `/api/`-Prefix HTML-Partials und JSON-APIs. Jetzt: HTMX-Partials unter `/partials/<feature>/` (9 Routen: `workitems/<pk>/status/`, `clients/autocomplete/`, `events/fields/`, `zeitstrom/feed/`, `cases/for-client/`, `search/global/`, `retention/<pk>/approve/`, `retention/<pk>/hold/`, `retention/hold/<pk>/dismiss/`), JSON-APIs unter `/api/v1/<feature>/` (1 Route: `offline/bundle/client/<pk>/` fuer Service-Worker). URL-Namen unveraendert — Templates und Reverse-Aufrufer brauchen keine Anpassung. [`src/core/urls.py`](https://github.com/anlaufstelle/app/blob/main/src/core/urls.py), [`src/static/js/offline-client.js`](https://github.com/anlaufstelle/app/blob/main/src/static/js/offline-client.js) (`_bundleUrl`), 7 E2E-Test-Files (`fetch()`-Pfade) angepasst. Konvention in CONTRIBUTING.md dokumentiert. Loest RFC #848 ab (Option C — Voll umziehen).
- **Vereinfachungs-Triage — Splits, Konsolidierung, Dead-Code-Entfernung** (#958) — Sechs Refactor-Schritte ohne User-sichtbare Verhaltensänderung: (1) Fünf Test-Files >700 LoC nach Test-Klassen-Cluster gesplittet (`test_mutation_followup_offline`, `test_mutation_followup_client_export`, `test_mutation_followup_snapshot`, `test_system_views`, `test_workitems`) mit shared Helper-Modulen; alle 395 betroffenen Tests bleiben grün. (2) `core/services/compliance.py` (589 LoC) zu Subpackage `core/services/compliance/` mit zehn fokussierten Files inkl. Single-Source `_clock.now()`-Mockpunkt für Time-Branches. (3) Admin-Permissions/Facility-Scope auf `AnlaufstelleAdminSite` konsolidiert (`has_role_permission`/`scope_to_facility` als SSOT); `RoleBasedPermissionMixin` und `FacilityScopedAdminMixin` sind jetzt Thin Delegates. (4) `core/admin.py` (559 LoC) zu Subpackage `core/admin/` nach Domäne (users, organization, clients, documents, events, workflow, system) gesplittet. (5) Ungenutzte Template-Partials (`_confirm_modal.html`, `_tasks_widget.html`) und zugehörige Alpine-Komponente entfernt. (6) Drei Trivial-Services (`password.py`, `episodes.py`, `offline_keys.py`) in Model-Methods inlined. Alle Subpackage-`__init__.py` re-exportieren die Public API — keine Aufrufer-Änderungen.
- **Typed audit-Domain-Helper-API** (#901) — Foundation-Schritt zur Konsolidierung der bisher 50 direkten `AuditLog.objects.create()`-Aufrufe im Service-Layer. Neue typed Helper in [`core/services/audit.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/audit.py): `audit_event`, `audit_client_event`, `audit_retention_decision`, `audit_security_violation`, `audit_system_view`. Sieben Migrationsschritte (S1–S8) bringen Clients-, Retention-, File-Vault-/Breach-Detection-, System-Views-, Service-Layer- und MFA/Attachments/Sudo-Pfade von Direkt-Aufrufen auf die typed Helper. Neuer Architekturtest `TestAuditLogCreationAllowlist` scannt `src/core/**/*.py` nach verbliebenen `AuditLog.objects.create(`-Sites und verhindert Code-Drift gegen die Allowlist. `log_audit_event`/`log_settings_change` bleiben unverändert.
- **`views/system.py` → `views/system/`-Subpackage** (#904) — 867-LOC-Modul mit neun heterogenen View-Klassen ersetzt durch thematisches Subpackage mit zehn Dateien (`mixins`, `dashboard`, `organization`, `audit`, `lockouts`, `maintenance`, `retention`, `vvt`, `legal_holds`, `compliance`). `__init__.py` re-exportiert alle Klassen — keine URL-, Template- oder Permissions-Änderungen.
- **Field-Type-Registry** (#907) — Feldtyp-Logik war dreifach verteilt (`FieldTemplate`-Model-Validierung, `DynamicEventDataForm.FIELD_TYPE_MAP`, SELECT/MULTI_SELECT-Sonderpfade). Neues [`core/services/field_types.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/field_types.py) bündelt pro Feldtyp `form_field_cls`, `widget_factory`, `parse_default`, `validate_default`, `allows_default` in `FieldTypeSpec`-Dataclass; `FIELD_TYPE_REGISTRY` deckt alle neun `FieldType`-Codes ab. Datei-Security bleibt in `file_vault` — Registry deckt nur UX-/Form-/Default-Aspekte ab.
- **`WorkItem`-Statusübergänge zentralisiert** — Übergangslogik aus Views/Forms in den Service-Layer ([`core/services/workitems.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/workitems.py)) verschoben. Erleichtert Audit-Integration und Tests.
- **`anonymize_client` in private Teilschritte zerlegt** — Die Anonymisierungs-Pipeline (DSGVO Art. 17) ist in private Helper aufgeteilt; Characterization-Tests sichern das bestehende Verhalten ab, bevor weiter refactored wird.
- **`file_vault` als Subpackage gesplittet** (#910) — Die ehemaligen `file_vault.py` (208 LoC) und `file_vault_validation.py` (228 LoC) sind jetzt das Subpackage [`core/services/file_vault/`](https://github.com/anlaufstelle/app/blob/main/src/core/services/file_vault/) mit vier thematischen Modulen: [`policy.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/file_vault/policy.py) (Pre-Encrypt-Validation: Extension-Whitelist, Magic-Bytes, ClamAV), [`audit.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/file_vault/audit.py) (`log_attachment_violation`-Sink für `SECURITY_VIOLATION`-AuditLogs), [`storage.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/file_vault/storage.py) (Hot-Path Upload/Replace/Read/Soft-Delete) und [`cleanup.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/file_vault/cleanup.py) (Event-Löschung, Orphan-Cron). [`__init__.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/file_vault/__init__.py) re-exportiert die Public API — keine Aufrufer-Änderung nötig. Mutmut-Whitelist in [`pyproject.toml`](https://github.com/anlaufstelle/app/blob/main/pyproject.toml) zeigt jetzt auf `file_vault/policy.py`. Akzeptanzkriterien aus #910 erfüllt: Public API unverändert, jedes Modul <200 LoC (größtes `policy.py` ~150), 71 file_vault-Tests + 19 indirekte Konsumenten-Tests + 13 Architecture-Guards bleiben unverändert grün.
- **`alpine-components.js` featureweise gesplittet + Navigation in Partials** (#911) — Die ehemalige [`src/static/js/alpine-components.js`](https://github.com/anlaufstelle/app/blob/main/src/static/js/alpine-components.js) (748 LoC, 20 Alpine-Komponenten) ist in fünf thematische Module unter [`src/static/js/alpine/`](https://github.com/anlaufstelle/app/blob/main/src/static/js/alpine/) aufgeteilt: [`base-layout.js`](https://github.com/anlaufstelle/app/blob/main/src/static/js/alpine/base-layout.js) (Offline-Banner, GlobalSearch, Create-/Mobile-Menus), [`widgets.js`](https://github.com/anlaufstelle/app/blob/main/src/static/js/alpine/widgets.js) (Overflow-Menu, ExpandableCard, HistoryDetails), [`auth.js`](https://github.com/anlaufstelle/app/blob/main/src/static/js/alpine/auth.js) (MFA, Backup-Codes, PWA-Install), [`forms.js`](https://github.com/anlaufstelle/app/blob/main/src/static/js/alpine/forms.js) (Client/Event-Autocomplete, Date-Quick-Buttons) und [`dashboards.js`](https://github.com/anlaufstelle/app/blob/main/src/static/js/alpine/dashboards.js) (Workitem-/Retention-Bulk, Proposal-Card, Goals-Section). Der DOMContentLoaded-Date-Input-Validity-Helper liegt jetzt in [`date-input-i18n.js`](https://github.com/anlaufstelle/app/blob/main/src/static/js/date-input-i18n.js). [`base.html`](https://github.com/anlaufstelle/app/blob/main/src/templates/base.html) ist von 405 auf 119 LoC geschrumpft — die zwei `<nav>`-Blöcke (Desktop-Sidebar + Mobile-Bottom-Nav) liegen jetzt in [`partials/_navigation_desktop.html`](https://github.com/anlaufstelle/app/blob/main/src/templates/partials/_navigation_desktop.html) (171 LoC) und [`partials/_navigation_mobile.html`](https://github.com/anlaufstelle/app/blob/main/src/templates/partials/_navigation_mobile.html) (128 LoC). CSP-Eigenschaften unverändert: HTML5 `defer` garantiert sequentielle Ausführung in Dokumenten-Order, damit `alpine:init`-Listener vor `alpine-csp.min.js` registriert sind. Verifikation: 29 Architecture-Guards grün, 105 Auth/Dashboard/Zeitstrom-Tests grün, 17 E2E-Smokes (`test_layout` + `test_dashboard`) grün.

### Fixed

- **mypy-Failures auf Stage-CI** (#993) — Zwei Typecheck-Fehler, die auf Dev nicht auffielen: ungenutzter `# type: ignore[arg-type]`-Kommentar in [`core/forms/events.py`](https://github.com/anlaufstelle/app/blob/main/src/core/forms/events.py) entfernt; `timezone.datetime` in [`core/services/dashboard/main.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/dashboard/main.py) (Django re-exportiert das stdlib-Modul ohne `__all__`) durch direkten stdlib-Import ersetzt. Plus: `typecheck` ist jetzt Bestandteil des lokalen `make ci`-Gates ([`Makefile`](https://github.com/anlaufstelle/app/blob/main/Makefile)), damit die Stage-CI-Lücke geschlossen bleibt.
- **ClamAV-Signaturalter aus dem `version()`-String als Fallback** (#979) — Manche ClamAV-Builds liefern in `stats()` keine parsbare Build-time-Zeile, führen das Signatur-Build-Datum aber im `version()`-String mit (`ClamAV <ver>/<sig-nr>/<date>`). `signature_info()` meldete dann dauerhaft `unknown`, obwohl die Signaturen frisch waren — der Compliance-Check schlug fälschlich an. Neuer `_parse_version_date()`-Fallback parst das dritte `/`-getrennte Feld (UTC), wenn `stats()` nichts liefert.
- **Dev-systemd-Timer bei jedem Deploy installiert** (#980) — Die systemd-Timer (Backup/Retention/Snapshots/Breach/MV-Refresh) wurden bisher nur inline in `bootstrap.sh` beim Erst-Provisioning installiert — nachträglich ergänzte Timer kamen nie auf den laufenden Host (der Backup-Cron blieb aus, Backup auf dev 13 Tage alt). Timer-Installation in das neue idempotente `deploy/install-timers.sh` extrahiert, das `deploy-dev.sh` bei jedem Deploy aufruft; `bootstrap.sh` verweist nur noch darauf.
- **Restore-Drill lauffähig gemacht** (#981) — Drei im Live-Lauf aufgedeckte Defekte behoben: (1) `CREATE DATABASE` scheiterte, weil `anlaufstelle_admin` zwar BYPASSRLS, aber nicht CREATEDB ist — der Drill nutzt jetzt den Postgres-Superuser (Local-Socket-Trust im db-Container). (2) `pg_restore` brach mit rc=1 ab, weil das Dump ein `REFRESH MATERIALIZED VIEW` gegen FORCE-RLS auf `core_event` enthält (nur Statistik-Cache, keine Nutzdaten) — reine MV-Refresh-Fehler werden jetzt toleriert, jeder andere Fehler bleibt FAIL. (3) Der Trigger-Check meldete unter `pipefail` fälschlich „blockt nicht". Zusätzlich synct `deploy-dev.sh` jetzt `scripts/restore-drill.sh` auf den Server. Verifiziert auf dev (87 Personen / 1503 Events restauriert, RLS auf 22 Tabellen).
- **super_admin sieht eigenes `/account/`-Profil** (#975) — `AccountProfileView` nutzte `AssistantOrAboveRequiredMixin`, das super_admin (`facility=None`) ausschloss → 403 auf das eigene Profil. `/account/` ist die Eigenprofil-Seite und soll jeder authentifizierten Person offenstehen. Mixin auf `LoginRequiredMixin` umgestellt ([`core/views/account.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/account.py)); die facility-gebundenen Widgets (Events/Cases/WorkItems/RecentVisits) filtern auf `facility=None` → leere Ergebnisse statt Crash. RBAC-Matrix-Erwartung 403→200 nachgezogen.
- **super_admin landet bei `?next=/` trotzdem auf `/system/`** (#970, #867) — Ruft ein unauth super_admin `/` auf, redirected Django (ZeitstromView = `LoginRequiredMixin`) zu `/login/?next=/`; das implizite `?next=/` übersteuerte nach Login bisher die `/system/`-Default-Logik aus #867, sodass der super_admin auf der für ihn leeren Zeitstrom-Seite landete. Fix in [`core/views/auth.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/auth.py): `?next=` übersteuert `/system/` nur bei echten Deep-Links — `/` und `LOGIN_REDIRECT_URL` gelten als impliziter Default, nicht als gezielter Wunsch.
- **`csrf_failure`-View loggt Diagnose-Kontext** (#970) — Revidiert bewusst die #699-Entscheidung „reason loggen wir nicht": für User irrelevant, für DevOps bei Production-Vorfällen (CSRF-403-nach-Login) aber unverzichtbar — ohne `reason` war die Django-Failure-Klassifikation (Origin-Mismatch vs. Token incorrect vs. Cookie missing) nicht erkennbar. Strukturiertes WARNING-Log an `django.security.csrf` mit reason + path + referer + origin + HTMX-Flag + user ([`core/views/errors.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/errors.py)); für den User unverändert sichtbar.
- **EN-Übersetzungskatalog vervollständigt** (#974) — Der englische Katalog hatte 121 fuzzy + 122 leere Einträge (~22 %) plus ~84 nie extrahierte Quellstrings — Folge der Klientel→Person-Umbenennung ohne nachgezogene Übersetzung; die EN-UI fiel flächendeckend auf Deutsch zurück (z. B. „Arbeitszentrale", „Neue Person"). `makemessages -l en` + vollständige Übersetzung aller aktiven Einträge ([`src/locale/en/LC_MESSAGES/django.po`](https://github.com/anlaufstelle/app/blob/main/src/locale/en/LC_MESSAGES/django.po)): 0 fuzzy, 0 untranslated, 1183 Messages. Terminologie: Person/People, GDPR für DSGVO, dt. Rechtsnormen (SGB/StGB) beibehalten.
- **Favicon-404 + Deprecation-Warnung im `<head>`** (#973) — Live-Verifikation deckte auf jeder Seite einen 404 auf `/favicon.ico` (kein `<link rel="icon">` im `<head>`) sowie eine Deprecation-Warnung zu `apple-mobile-web-app-capable` auf. `<link rel="icon">` und modernes `<meta name="mobile-web-app-capable">` in [`base.html`](https://github.com/anlaufstelle/app/blob/main/src/templates/base.html) und [`auth/login.html`](https://github.com/anlaufstelle/app/blob/main/src/templates/auth/login.html) ergänzt (auth-Seiten erben `base.html` nicht); das `apple-*`-Pendant bleibt für iOS-Standalone-PWA erhalten.
- **`deploy-dev.sh` Web-Recreate deterministisch und hart verifiziert** (#887, #956, #976) — Drei aufeinanderfolgende Fix-Iterationen für denselben Recreate-Bug. Beim Rolling-Tag `:main` zog `docker compose pull` das neue Image, aber `up -d` recreated den Container nicht zuverlässig — stale Container nach Deploy. Erst `--force-recreate`, dann `compose rm -sf web` + `up`, schließlich `-T` + `</dev/null` (entkoppelt vom Skript-STDIN) plus Post-Condition-Check per **Container-ID-Wechsel** (statt fragilem Image-Digest-Vergleich). Aktueller Stand: race-frei, deterministisch, hart verifiziert. Caddy/DB/ClamAV (tag-gepinnt) bleiben unangetastet, kurze Web-Downtime ~10–30 s während Container-Restart.
- **E2E-Sudo-Setup im Account-Lockout-Test** (#960) — `tests/e2e/test_auth_roles.py::TestZZAccountLockout::test_admin_unlock_action_restores_login` stammt aus (vor Custom-AdminSite mit Sudo-Mode-Pflicht aus #785) und navigierte direkt zu `/admin-mgmt/core/user/` ohne `enter_sudo_mode()`-Step. In e2e/prod-Settings mit `SUDO_MODE_ENABLED=True` wurde der Test damit zu `/sudo/?next=...` umgeleitet und die UserAdmin-Liste lud nie — die Lena-Zeilen-Checkbox blieb unauffindbar (30s-Timeout). Fix: `enter_sudo_mode(admin, base_url)` vor `goto()` ergaenzt, analog [`test_client_export.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/e2e/test_client_export.py) und [`test_dsgvo_package.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/e2e/test_dsgvo_package.py). Aufgefallen beim `make test-e2e-parallel`-Lauf für #958.
- **clamav-Healthcheck** (#938) — `clamdcheck.sh` testet TCP 3310, aber `clamd` lauscht im Image-Default nicht auf TCP — Container-Status blieb permanent `unhealthy`, obwohl `clamd` selbst funktioniert. Healthcheck in [`docker-compose.yml`](https://github.com/anlaufstelle/app/blob/main/docker-compose.yml) und [`docker-compose.prod.yml`](https://github.com/anlaufstelle/app/blob/main/docker-compose.prod.yml) auf `clamdscan --version` umgestellt (robusterer Smoke-Test). Beendet den False-Positive `unhealthy`-Status. **Follow-Up out-of-scope:** der eigentliche TCP-3310-Bug (`virus_scan.py` nutzt TCP) wird in einem separaten Issue adressiert.
- **`deploy/backup.sh`** — pg_dump verbindet jetzt als `POSTGRES_ADMIN_USER` (BYPASSRLS) statt als App-User. Hintergrund: das v0.12.0-RLS-Bootstrap-Trennungsmodell macht `pg_dump` aus dem App-User unmöglich (FORCE ROW LEVEL SECURITY auf `core_activity`). Aufgefallen beim v0.12.0-Pre-Deploy-Backup auf `dev.anlaufstelle.app`.
- **Settings-Audit-Vollständigkeit** (#893) — `_AUDIT_FIELDS` in [`core/services/settings.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/settings.py) enthielt nur 9 von 17 verhaltensrelevanten Feldern; Änderungen an Retention-Schwellen, MFA-Enforcement, Login-Lockout u. Ä. blieben unauditiert. Neuer Architekturtest in [`test_audit_coverage.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/test_audit_coverage.py) erzwingt Audit-Coverage auf allen verhaltensrelevanten Feldern.
- **N+1 in `build_attachment_context`** (#894) — Pro File-Marker im Event wurde eine eigene Query für die letzte Version abgesetzt. Jetzt batched via `prefetch_related` mit annotated Subquery. Verifiziert über `test_attachment_versioning_stage_b.py`-Query-Count-Asserts.
- **UFW-Docker-Regeln nach `compose up` re-syncen** — `deploy/deploy-dev.sh` läuft nach jedem `docker compose up -d` einmal `ufw-docker check` + `ufw-docker allow`-Sync für die exponierten Ports, damit nach Restart kein Drift zwischen UFW- und Docker-iptables-Chain entsteht.
- **Timezone-Bugs in `test_export_external_no_pseudonyms` und `test_zeitstrom_perf`** (#939) — Beide Tests verglichen UTC-aware-Werte gegen Berlin-local-Datumsgrenzen, sodass Events zwischen 22-24 UTC aus dem erwarteten Range fielen und CI nach 23 UTC rot wurde. Fix: `timezone.localtime()` für tagaktuelle Vergleichsbasis statt `timezone.now().date()`.
- **Selector-Stability-Guard-Whitelist** — `test_goals_htmx`-E2E-Suite erweitert um zusätzliche `data-testid`-Anchors; Architecture-Guard-Whitelist in `test_architecture_guards_audit.py` mitgezogen.
- **E2E-`ban_banner`-Seed** — Seed nutzte verlegtes Encryption-Modul und falsches `occurred_at`-Feld; Fix in [`src/tests/e2e/conftest.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/e2e/conftest.py) (Refs #922, #924).

### Docs

- **Tech-Stack-Doku auf Django 6.0** — Versions-Header in [`README.md`](https://github.com/anlaufstelle/app/blob/main/README.md), [`README.en.md`](https://github.com/anlaufstelle/app/blob/main/README.en.md), [`CONTRIBUTING.md`](https://github.com/anlaufstelle/app/blob/main/CONTRIBUTING.md), [`CONTRIBUTING.en.md`](https://github.com/anlaufstelle/app/blob/main/CONTRIBUTING.en.md), [`docs/fachkonzept-anlaufstelle.md`](https://github.com/anlaufstelle/app/blob/main/docs/fachkonzept-anlaufstelle.md), [`docs/ops-runbook.md`](https://github.com/anlaufstelle/app/blob/main/docs/ops-runbook.md) und [`CLAUDE.md`](https://github.com/anlaufstelle/app/blob/main/CLAUDE.md) auf Django 6.0 statt 5.1.
- **TDD-Policy als Pflicht verankert** (#951) — Bisher stand in [`CLAUDE.md`](https://github.com/anlaufstelle/app/blob/main/CLAUDE.md) explizit Test-After („Reihenfolge: Implementieren → Testen → Commit → Push"). User-Direktive 2026-05-20: für jede Service-/Form-/Model-/CBV-Änderung zuerst einen pytest-Test schreiben, der mit erwartetem `AssertionError` fehlschlägt (Red), dann minimal Code bis grün (Green), dann refactoren. E2E-Tests in `src/tests/e2e/` bleiben **manuell-first**. Neuer Abschnitt `### Test-Driven Development (Unit/Service)` in [`CONTRIBUTING.md`](https://github.com/anlaufstelle/app/blob/main/CONTRIBUTING.md) mit Red-Green-Refactor-Beispiel.
- **Audit-Konsolidierung 2026-04-30 abgeschlossen** (#764) — Issue aus der Audit-Runde 2026-04-30 in Einzel-Issues #770–#848 aufgespalten und überwiegend abgeschlossen. Detail-Tracking läuft jetzt über die Roadmap #890.
- **Security-Notes: GitHub-Repo-Härtung dokumentiert** (#888) — Neuer Abschnitt in [`docs/security-notes.md`](https://github.com/anlaufstelle/app/blob/main/docs/security-notes.md) hält fest: (a) Diagnose der Mini-Shai-Hulud-Lage (nicht betroffen), (b) umgesetzte Permissions- und SHA-Pinning-Massnahmen, (c) Ziel-Branch-Protection-Ruleset für `main`, das beim Repo-Public-Switch oder GitHub-Pro aktiviert werden muss (auf privatem Free-Repo via `gh api` nicht zugänglich).
- **Manuelle Test-Matrix stark erweitert** — Neue Bereichscodes `SETUP`, `COMP`, `PRIV`, `A11Y`, `REPORT`; Sektion D für LOKAL/SSH-Ops-Tests; A11Y-Bereich mit 9 manuellen Stichprobenfällen; Security-Negativtests in Sektion C ausgebaut; Offline-/PWA-Privacy-Failure-Modes `ENT-OFFL-13..18`; 5 TCs für Lösch-Kaskaden und Mandantentrennung; Anhang E mit Performance-Budgets; Ops-/Self-Hosting-Cases `D.OPS`; Auto-Patch für Anhang C; Coverage-Updates für 20 P1- und 15 P0-TCs auf „automatisiert". Quellen: [`docs/testing/manual-test-matrix.md`](https://github.com/anlaufstelle/app/blob/main/docs/testing/manual-test-matrix.md) und `docs/testing/test-matrix-index.md` (jetzt als kompakte Übersichtstabelle, auto-generiert von [`scripts/build_test_matrix_index.py`](https://github.com/anlaufstelle/app/blob/main/scripts/build_test_matrix_index.py)).
- **Release-Test-Profile** — Neue `docs/testing/release-test-profiles.md` definiert drei Profile (Smoke, Standard, Full) mit konkreten `pytest`/`make`-Targets und „keep-manual"-Sektion für Fälle, die bewusst manuell bleiben.
- **Run-Template für Testläufe** — Neue `docs/testing/run-template.md` + `docs/testing/runs/`-Verzeichnis, damit Auditoren Testlauf-Protokolle strukturiert ablegen.
- **Mutation-Testing-Workflow** — Drei aufeinander abgestimmte Docs: [`CONTRIBUTING.md`](https://github.com/anlaufstelle/app/blob/main/CONTRIBUTING.md) (Quick-Reference für `make mutation`-Run, Watchdog-Skript, Whitelist-Pflege), `docs/testing/mutation-testing.md` als Single Source of Truth (Run-Workflow für lange Mutation-Testing-Runden mit Pre-Run-Cleanup, dokumentierte mutmut-3.5-Quirks: kein echtes Resume, `.meta`-Reset, broken `--rerun-all`) und `docs/testing/mutation-survivors-baseline.md` (Ergebnisse der Runden, Lessons Learned, Re-Run nach Survivor-Mutanten-Folgetests).
- **`make clean`** — Neues Target räumt lokale generierte Artefakte (`htmlcov/`, `.coverage*`, `mutmut-run-logs/`, `media/` u. a.) auf; in [`Makefile`](https://github.com/anlaufstelle/app/blob/main/Makefile) und beiden `CONTRIBUTING`-Dateien dokumentiert.

### Tests

- **Guard gegen fuzzy/unübersetzte EN-Einträge** (#974) — Neuer [`test_i18n_catalog.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/test_i18n_catalog.py) parst den aktiven EN-Katalog per stdlib (kein neues Dep) und schlägt fehl, sobald fuzzy- oder leere `msgstr`-Einträge auftauchen — verhindert die Regression aus #974. Läuft in der normalen Unit-Suite.
- **Mutation-Testing-Infrastruktur** (Refs #922, #923, #930) — `mutmut` 3.x in [`requirements-dev.txt`](https://github.com/anlaufstelle/app/blob/main/requirements-dev.txt); Runner-Wrapper [`scripts/run_mutmut.py`](https://github.com/anlaufstelle/app/blob/main/scripts/run_mutmut.py) mit Subprocess-Isolation für die Clean-Phase (mutmut 3.x bietet kein echtes Resume, jeder Start resettet `.meta`); Watchdog-Skript [`scripts/run_mutmut_watchdog.sh`](https://github.com/anlaufstelle/app/blob/main/scripts/run_mutmut_watchdog.sh) für robusten Re-Start auf Long-Running-Läufen; `paths_to_mutate` als TOML-Liste, Whitelists für `virus_scan.py`, `system_health.py`, `invite.py` in [`pyproject.toml`](https://github.com/anlaufstelle/app/blob/main/pyproject.toml).
- **Survivor-Mutanten-Folgetests** — Elf neue Test-Module decken überlebende Mutanten der Mutation-Testing-Runden ab: `snapshot`-Statistics-Service, `client_export` (DSGVO Art. 15), `core.services.export`, `offline.build_client_offline_bundle`, `handover`-Collect-Helpers, `feed.build_feed_items`, `DynamicEventDataForm`, `dsgvo_package._settings_hash`, `events.context`, compliance dsgvo-/security-checks. Re-Run-Ergebnisse in `docs/testing/mutation-survivors-baseline.md`.
- **E2E-HTMX-Updates** — Neue Suiten für Cases, WorkItems-Bulk, Goals, Episoden, zentrale Anhang-Übersicht und Events. Mobile-Workflows inkl. Mobile-Fixture in [`src/tests/e2e/conftest.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/e2e/conftest.py). Locale-Wechsel, Deletion-Reject, Optimistic-Locking via zwei Browser-Sessions und echter Lockout-Workflow für Ban-Banner.
- **`data-testid`-Selektoren + Architecture-Guard** — Helper-Library [`src/tests/e2e/_selectors.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/e2e/_selectors.py), `data-testid`-Refactor über alle Templates , und neuer Architecture-Guard in `test_architecture_guards_audit.py`, der unstable Selektoren (`:has-text`, `text=…` ohne Anchor) blockiert. Stabile Selektoren für `client-edit`, `workitems-deletion`, `dsgvo-package`, `pwa-offline`, `auth-roles`, `workitem-ui`, `client-export`, `audit-detail`.
- **Boundary- und Robustheits-Tests** — Unicode/Emoji/Null-Byte/RTL-Marker in User-Input (`test_boundary_unicode.py`), Max-Length-Constraints aus Model-Meta abgeleitet (`test_boundary_maxlength.py`), Empty-Collection-Renders für Dashboard/Statistics/Audit/Search (`test_boundary_empty.py`), DST-Übergänge und Aware/Naive-Mix (`test_timezone_dst.py`), File-Upload-Errors (Oversize, Empty, Filename-Injection, Unicode), File-Vault Fake-Magic-Bytes + Double-Extension.
- **Audit/RBAC/RLS-Vertiefungstests** — Append-Only-Trigger gegen UPDATE/DELETE, Deletion-Workflow-Audit-Trail mit dokumentiertem Audit-Gap, RBAC-Export-Filter nach Rolle, externe Berichte ohne Pseudonyme, Retention-Sensitivity, MFA-Lockout-Flow, MFA-Devices facility-scoped, User-Deaktivierung beendet Sessions, RLS für `DeletionRequest` cross-facility + Client-Detail-Scoping, Cascade/PROTECT-Verhalten für Cases und Clients, Krisen-Eskalations-Workflow.
- **CLI- und Form-Smoke-Tests** — Smoke-Tests für `detect_breaches` und `cleanup_orphan_storage_files`. Form-Tests für `WorkItemForm` (Date-Constraints + Recurrence), `EventMetaForm` + `MultiFileField`, `CaseForm` + `EpisodeForm` (Cross-Facility-Validation), `ClientForm` (Validation + Boundary-Cases). Form-Test-Helpers extrahiert in [`src/tests/_form_helpers.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/_form_helpers.py).
- **Grosse Test-Datei-Splits** — `test_architecture.py` (1220 LOC) → fünf Guard-Module (`models`, `services`, `views`, `templates`, `audit` + e2e). `test_events.py` (1354 LOC) → fünf Module (`crud`, `history`, `deletion`, `access_policy`, `attachments`). `test_rbac_matrix.py` (783 LOC) → Helpers + vier View-Module (`cases`, `exports`, `workitems`, `audit`). Filter-Persistence-Tests in `test_filters.py` zusammengeführt.
- **Mutation-Score-Lift in `core.services` und `core.forms`** (Refs #941, #942, #943) — Adjustierte Killrate `core.services` 75 % → **80.86 %**, `core.forms` 80 % → **84.65 %** (gemittelt). Compliance-Survivor-Triage mit gezielten Logic-Tests für DSGVO-/Security-Checks; Forms-Bucket-Lift via `required=False`-Queryset-Tests und ValidationError-Pragmas.
- **Coverage-Lift 94.10 % → 96.09 %** (Refs #945, #949) — Sechzehn neue/erweiterte Test-Module über vier Buckets: `formatting`, `admin_csp_relax`, `settings_seed-signal`, `refresh_statistics_view`, `handover_view`, `system_maintenance`, `core_tags`, `utils_dates_remind_at`, `client_deletion_workflow`, `seed_events_helpers`, `views_auth_branches`, `system_audit_branches`, `event_deletion_review`, `workitem_bulk_branches`. +129 Tests, +170 covered Lines. Mock-basierte OSError-Tests für nicht-real erreichbare I/O-Pfade. Schließt den Stabilisierungsplan #922 ab (2026-05-20).

### CI

- **GHCR-Image-Visibility automatisch public** (#886) — GHCR-Pakete sind beim Erstanlage immer privat, auch wenn das Source-Repo public ist; beim v0.12.0-Release fiel auf, dass `docker pull` ohne Authentifizierung mit 401 fehlschlägt. Neuer Step in [`.github/workflows/release.yml`](https://github.com/anlaufstelle/app/blob/main/.github/workflows/release.yml) setzt nach Image-Push die Package-Visibility per GitHub-API idempotent auf public — mit `if: github.event.repository.private == false`-Guard (läuft nicht im privaten Dev-Repo). [`docs/release-checklist.md`](https://github.com/anlaufstelle/app/blob/main/docs/release-checklist.md) § 3 um Smoke-Check-Bullet ergänzt. **User-Action:** PAT mit `admin:packages`-Scope erzeugen und als Secret `GHCR_VISIBILITY_TOKEN` in `anlaufstelle/stage` und `anlaufstelle/app` hinterlegen. Bestehende Pakete `v0.10.x`/`v0.11.x`/`v0.12.0` einmalig manuell auf public flippen — der Workflow heilt nur künftige Releases.
- **Nightly Mutation-Testing-Job** — Neuer Job in [`.github/workflows/test.yml`](https://github.com/anlaufstelle/app/blob/main/.github/workflows/test.yml), läuft nachts mit `mutmut` 3.x über die in `pyproject.toml` definierten `paths_to_mutate`. Drift-Check für die manuelle Test-Matrix in [`scripts/check_test_matrix_drift.py`](https://github.com/anlaufstelle/app/blob/main/scripts/check_test_matrix_drift.py) verhindert, dass automatisierte und manuelle TCs auseinanderlaufen.
- **Neue `make`-Targets** — `make mutation`, `make verify-matrix-drift`, `make ci-coverage`, `make clean`. Whitelists für `virus_scan.py` und `system_health/invite.py` in [`pyproject.toml`](https://github.com/anlaufstelle/app/blob/main/pyproject.toml) eingepflegt; pre-existing Ruff-Errors auto-fixed; mutmut-Run-Logs in [`.gitignore`](https://github.com/anlaufstelle/app/blob/main/.gitignore) exkludiert.
- **Coverage-Schwelle 93 % → 96 %** (Refs #945) — [`pyproject.toml`](https://github.com/anlaufstelle/app/blob/main/pyproject.toml) `[tool.coverage.report] fail_under` und [`.github/workflows/test.yml`](https://github.com/anlaufstelle/app/blob/main/.github/workflows/test.yml) zusammen auf 96 angehoben; bricht ab, sobald Coverage unter das v0.13.0-Niveau fällt.

## [0.12.0] - 2026-05-12

Minor-Release. Schwerpunkte: 5-Rollen-Modell mit Superadmin und cross-facility `/system/`-Bereich; produktive `dev.anlaufstelle.app`-Topologie auf Hetzner als Coolify-Ablöse; RLS-Hardening rund um Bootstrap und Pre-Auth-AuditLogs. Zusätzlich Sicherheits-Bumps (Django 6.0.5 mit drei CVEs, urllib3 2.7.0, cryptography 48 mit X.509-Hardening, gunicorn 26 mit HTTP/1.1-RFC-9112-Validierung).

### Security

- **Django 6.0.4 → 6.0.5** ([#881](https://github.com/anlaufstelle/app/pull/881)) — drei CVE-Fixes:
 - CVE-2026-6907 — Caching von Requests bei gesetztem `Vary`-Header
 - CVE-2026-35192 — `Vary`-Header beim Setzen einer Session
 - CVE-2026-5766 — `DATA_UPLOAD_MAX_MEMORY_SIZE`-Enforcement im `MemoryUploadHandler`
 - Im Django-Stack mit aktualisiert: `django-stubs` 6.0.3 → 6.0.4.
- **`urllib3` 2.6.3 → 2.7.0** — CVE-2026-44431, CVE-2026-44432. Transitive Dependency über `sentry-sdk`, jetzt explizit in `requirements.in` gepinnt.
- **`cryptography` 47.0.0 → 48.0.0** ([#882](https://github.com/anlaufstelle/app/pull/882)) — Hardening: strikte X.509-CRL-Validierung (Mismatch zwischen `TBSCertList.signature` und `signatureAlgorithm` löst jetzt `ValueError`). Post-Quantum-Support (ML-KEM/ML-DSA) via OpenSSL 3.5+, AWS-LC, BoringSSL.
- **`gunicorn` 25.3.0 → 26.0.0** ([#883](https://github.com/anlaufstelle/app/pull/883)) — Hardening: strikte HTTP/1.1-Request-Target-Validierung nach RFC 9112 § 3.2.3/3.2.4. Breaking-Change „Eventlet-Worker entfernt" betrifft uns nicht (wir nutzen Sync-Worker).

### Added

- **5-Rollen-Modell mit Superadmin** (#867) — Neue Hierarchie `SUPER_ADMIN > FACILITY_ADMIN > LEAD > STAFF > ASSISTANT`. Superadmin ist facility-übergreifend.
 - Bestehende `ADMIN`-User werden zu `FACILITY_ADMIN` migriert (Rename).
 - RLS-Bypass läuft über ein Postgres-Session-Setting, **nicht** über die `BYPASSRLS`-Role — Migrationen [`0084_user_role_super_admin.py`](https://github.com/anlaufstelle/app/blob/main/src/core/migrations/0084_user_role_super_admin.py) und [`0085_rls_superadmin_bypass.py`](https://github.com/anlaufstelle/app/blob/main/src/core/migrations/0085_rls_superadmin_bypass.py).
 - Superadmin wird per `manage.py create_super_admin`-CLI angelegt (interaktiv, kein Seed-Default in Production).
 - Hintergrund und Trade-offs: [ADR-018](https://github.com/anlaufstelle/app/blob/main/docs/adr/018-rollenmodell-superadmin.md), Fachkonzept v1.5, FAQ.

- **`/system/`-Bereich für Superadmin** (#866) — Login-Redirect, eigene Sidebar, facility-übergreifendes Dashboard. Facility-gescopte Menü-Einträge werden in `/system/` ausgeblendet.
 - **Tier 1:** System-Health-Card (#871), Sperrkonten-Liste mit Unlock-Button (#872), AuditLog-Export CSV/JSON (#873), Maintenance-Mode-Toggle (#874).
 - **Tier 2:** Retention-Übersicht (#875), Verzeichnis Verarbeitungstätigkeiten (Art. 30, read-only) (#876), Legal-Hold-Übersicht (#877).
 - `manage.py unlock <username>`-CLI als Recovery-Pfad, falls kein Superadmin verfügbar ist.

- **`dev.anlaufstelle.app` Live-Deployment** (#671, #862, #554) — Plain Docker Compose auf Hetzner CX22 als Coolify-Ablöse ([ADR-017](https://github.com/anlaufstelle/app/blob/main/docs/adr/017-deployment-topology.md)). Coolify-Runbook ist deprecated.
 - Compose-Stack: [`docker-compose.dev.yml`](https://github.com/anlaufstelle/app/blob/main/docker-compose.dev.yml), [`Caddyfile.dev`](https://github.com/anlaufstelle/app/blob/main/Caddyfile.dev), [`.env.dev.example`](https://github.com/anlaufstelle/app/blob/main/.env.dev.example).
 - Deploy-Skripte: [`deploy/bootstrap.sh`](https://github.com/anlaufstelle/app/blob/main/deploy/bootstrap.sh) (UFW als letzter Schritt, damit die SSH-Session nicht abreißt), [`deploy/deploy-dev.sh`](https://github.com/anlaufstelle/app/blob/main/deploy/deploy-dev.sh), [`deploy/backup.sh`](https://github.com/anlaufstelle/app/blob/main/deploy/backup.sh).
 - Settings-Modul [`devlive`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/devlive.py) und Make-Target `make deploy-dev`.
 - [`dev-image`-Workflow](https://github.com/anlaufstelle/app/blob/main/.github/workflows/dev-image.yml) baut bei jedem `main`-Push ein `:main`-Image.
 - `RobotsTxtView` mit `Disallow: /` — Dev-Instanz wird nicht indexiert.

- **Manuelle Test-Matrix** (#864) — [`docs/testing/manual-test-matrix.md`](https://github.com/anlaufstelle/app/blob/main/docs/testing/manual-test-matrix.md) mit drei Sektionen (Anwender, Entwickler, Auditor) für Funktionalität, DSGVO und Sicherheit. Setup einmalig pro Test-Tag gegen `dev.anlaufstelle.app`.

### Changed

- **2-User-DB-Modell für RLS-Bootstrap** — Postgres-Init legt einen separaten Admin-User mit `BYPASSRLS` an; Migrationen und Seed verbinden als `POSTGRES_ADMIN_USER`. App-Worker laufen weiterhin auf einem nicht-bypass-fähigen App-User. Self-Hoster brauchen die neuen Env-Vars (siehe [`.env.dev.example`](https://github.com/anlaufstelle/app/blob/main/.env.dev.example)). Das Pattern ist in [`docs/dev-deployment.md`](https://github.com/anlaufstelle/app/blob/main/docs/dev-deployment.md) und ADRs [005](https://github.com/anlaufstelle/app/blob/main/docs/adr/005-facility-scoping-and-rls.md) + [007](https://github.com/anlaufstelle/app/blob/main/docs/adr/007-auditlog-append-only.md) dokumentiert.
- **i18n** (#878) — `/system/`-Bereich und vollständig DE + EN übersetzt.
- **CSP-Hygiene** — `data-confirm`/`data-action`-Attribute ersetzen verbliebene inline-`onclick`/`onsubmit`-Handler. Wireup in neuem [`confirm-action.js`](https://github.com/anlaufstelle/app/blob/main/src/static/js/confirm-action.js).
- **Routine-Dependency-Bumps** — `sentry-sdk` 2.58.0 → 2.59.0 ([#884](https://github.com/anlaufstelle/app/pull/884)), `django-unfold` 0.91.0 → 0.93.0 (Admin-Theme), `docker/setup-qemu-action` v3 → v4 ([#880](https://github.com/anlaufstelle/app/pull/880)) und `actions/upload-artifact` v4 → v7 ([#879](https://github.com/anlaufstelle/app/pull/879)) in den GitHub-Workflows.

### Fixed

- **Pre-Auth-AuditLogs unter RLS** (#866) — Login-Versuche, Lockout-Trigger und anonyme Reset-Anfragen schreiben AuditLogs, bevor `FacilityScopeMiddleware` die Session-Variable setzt; die `WITH CHECK`-Policy lehnte sie ab. Zwei Eingriffe lösen das:
 - Migration [`0083`](https://github.com/anlaufstelle/app/blob/main/src/core/migrations/0083_auditlog_rls_with_check.py) erlaubt NULL-Facility-INSERTs auf `core_auditlog`.
 - `user_logged_in` und `user_login_failed` setzen `app.current_facility_id` jetzt selbst, bevor sie auditieren.
 - Sichtbar sind diese Logs nur für Superadmin im `/system/`-Bereich.
- **Seed unter `FORCE ROW LEVEL SECURITY`** (#863) — `make seed` lief am Bootstrap-Henne-Ei vorbei (App-User kann die eigenen Policies nicht umgehen); Seed verbindet jetzt als `POSTGRES_ADMIN_USER` (BYPASSRLS).
- **Bootstrap-UFW-Reihenfolge** — UFW-Aktivierung im [`bootstrap.sh`](https://github.com/anlaufstelle/app/blob/main/deploy/bootstrap.sh) ist der letzte Schritt, sonst kappte das Skript die laufende SSH-Session, bevor `ufw allow OpenSSH` durch war.
- **Health-Check ohne TLS-Termination im Container** — `SECURE_REDIRECT_EXEMPT` für `/health/` gesetzt (Caddy macht TLS davor); `migrate` läuft via [`docker-migrate.sh`](https://github.com/anlaufstelle/app/blob/main/deploy/docker-migrate.sh) als Admin-User.

## [0.11.1] - 2026-05-05

Patch-Release: Dependency-Bumps und CI-Hardening als Folge zum v0.11.0 Stage-CI Lock-Drift-Befund. Keine Code-Änderungen am App-Verhalten.

### Changed

- **Dependencies aktualisiert** — `psycopg` 3.3.3 → 3.3.4, `mypy` ≥1.20 → ≥1.20.2.
- **GitHub-Actions aktualisiert** — `actions/setup-python` 5 → 6, `actions/setup-node` 4 → 6, `github/codeql-action` 3 → 4, `docker/build-push-action` 6 → 7, `peter-evans/create-issue-from-file` 5 → 6.
- **`make lint`-Scope** auf `scripts/` erweitert (#860) — die `check_*.py`-Helfer waren bisher nur im pre-commit-Hook erfasst, nicht in `make ci`. Drei Format-Drifts in `scripts/` mit gefixt; `pyproject.toml` hat per-file-ignores für Subprocess-Aufrufe in Dev-/CI-Tools.

### Fixed

- **Lock-File-Drift-Schutz** (#860) — `make ci` ruft `deps-check` auf, `.pre-commit-config.yaml` hat `pip-compile`-Hooks für `requirements*.in` und einen `pre-push-fast-ci`-Hook (`make lint && make deps-check && make check`). Ersetzt Branch Protection mit Required Status Checks, die bei direktem `git push` auf `main` nicht greifen würden.
- **Workflow-Health-Check als Pre-Flight-Schritt** (#860) in [`docs/release-checklist.md`](https://github.com/anlaufstelle/app/blob/main/docs/release-checklist.md) — verhindert, dass deaktivierte Workflows unbemerkt bleiben. Hintergrund: Test/E2E/Lint/CodeQL/Release auf `anlaufstelle/app` waren von 2026-04-29 bis 2026-05-05 manuell deaktiviert, sodass v0.11.0 ohne CI auf `main` durchging und der Lock-Drift erst auf Stage-CI auffiel.

## [0.11.0] - 2026-05-05

Großer Sicherheits- und Hardening-Release. Hauptthemen: Wechsel auf Django 6.0 inkl. fünf CVE-Fixes, Sudo-Mode-Re-Auth für sensible Aktionen, DSGVO-Art.-33/34-Breach-Detection, Vier-Augen-Lösch-Workflow, Maintenance-Mode, neue Health-Checks, sowie ein A11y- und i18n-Sweep, der die Sprachleitlinie „Person" flächig durchzieht.

### Security

- **Django 5.1 → 6.0 Migration** (#732) — Wechsel von Django 5.1.15 auf 6.0.4. Django 5.1 ist EOL. Mit dem Sprung kommen die Sicherheits-Fixes CVE-2026-33034 (`DATA_UPLOAD_MAX_MEMORY_SIZE` enforcement), CVE-2026-33033 (`MultiPartParser`-DoS), CVE-2026-4292 (`ModelAdmin.list_editable`), CVE-2026-4277 (`GenericInlineModelAdmin`) und CVE-2026-3902 (Header mit Underscores in `ASGIRequest`). `django-unfold` auf 0.91.0 gehoben (6.0-Kompatibilität). Plugin-Stack (`django-csp`, `django-htmx`, `django-otp`, `django-ratelimit`, `sentry-sdk`) unverändert kompatibel. `django.contrib.postgres` zu `INSTALLED_APPS` hinzugefügt (in 6.0 strikt für `GinIndex` auf `Client.pseudonym` erforderlich, postgres.E005).
- **Sudo-Mode Re-Auth für sensible Aktionen** (#683) — Zeitlich begrenztes Re-Authentifizierungs-Fenster (15 min) vor besonders sensiblen Aktionen wie MFA-Disable, Passwort-Änderung, Daten-Export. `RequireSudoModeMixin` + neue Form mit Rate-Limit. Details in [`docs/faq.md` § 13a](https://github.com/anlaufstelle/app/blob/main/docs/faq.md#13a-was-ist-sudo-mode-re-auth-fenster).
- **DSGVO Art. 33/34 Breach-Detection** (#685) — Heuristik-basiertes `detect_breaches`-Cron-Kommando (stündlich:30) für Failed-Login-Burst, Mass-Export und Mass-Delete. Schreibt `SECURITY_VIOLATION`-AuditLog und liefert optional einen Webhook für SIEM/Pager. Runbook-Eintrag in [`docs/ops-runbook.md` § 6.5b](https://github.com/anlaufstelle/app/blob/main/docs/ops-runbook.md).
- **Klartext-Freitexte: UI-Warnung + Inventar** (#716, #717) — `Client.notes`, `Case.description`, `Episode.description` sind weiterhin nicht feldverschlüsselt. Sicht- und Editfelder zeigen jetzt eine UI-Warnung, dass dort keine Klarnamen oder Art-9-Daten gehören. Klartext-Inventar dokumentiert in [`docs/security-notes.md`](https://github.com/anlaufstelle/app/blob/main/docs/security-notes.md).
- **CSP-Reporting via `report-uri`** — neuer lokaler `/csp-report/`-Endpoint speichert Browser-CSP-Verstöße als `AuditLog` (Typ `CSP_VIOLATION`). Trade-off-Diskussion zu `report-to` vs. `report-uri` in [`docs/security-notes.md`](https://github.com/anlaufstelle/app/blob/main/docs/security-notes.md) (#684).
- **MFA-Backup-Codes auf 128 Bit + Hash-Storage** (#790) — Codes werden mit `secrets.token_urlsafe(16)` (128 Bit Entropie) erzeugt und nur als HMAC-SHA-256-Hash gespeichert. Bestandsdaten werden beim nächsten Login pro User automatisch migriert.
- **Passwort-Reset-AuditLog: E-Mail durch HMAC-Hash ersetzt** (#791) — Anonyme Reset-Anfragen legen im AuditLog jetzt nur noch einen HMAC-SHA-256-Hash der E-Mail-Adresse ab — die Forensik-Wiederbenutzbarkeit (Burst-Erkennung) bleibt erhalten.
- **Passwort-Mindestlänge auf 12 Zeichen** (#789) — `MinimumLengthValidator` von 8 auf 12 angehoben. Bestandsuser werden beim nächsten Login zur Änderung gezwungen.
- **CSV-Export auf `Event.objects.visible_to(user)`** (#779) — Der CSV-Export läuft jetzt einheitlich über den `visible_to`-Manager und ist damit rollenkonform zur UI-Sichtbarkeit.
- **q-Suchbegriffe nicht mehr in `sessionStorage`** (#787) — Die Filter-Persistenz (`data-filter-persist`) schließt das `q`-Suchfeld jetzt explizit aus, sodass eingegebene Begriffe nicht im Browser-`sessionStorage` verbleiben.
- **DSGVO-Top-Pseudonyme aus Standard-PDFs entfernt** (#792) — Pseudonyme erscheinen nur noch in als intern gekennzeichneten Admin-PDFs (Internal-Mode-Banner); alle anderen Auswertungen aggregieren.
- **CSV-Formula-Injection neutralisiert** — `services/export.py` prefixt führende `=`, `+`, `-`, `@`, `\t`, `\r` mit `'`, damit Excel/LibreOffice die Felder nicht als Formel auswertet.
- **Retention löscht jetzt wirklich** — `EventHistory` wird im Retention-Pfad jetzt durchgängig mit-redigiert; `audit_pruning` läuft ohne `DISABLE TRIGGER` (Refs #778, #781).
- **`Client.anonymize()` schließt zugehörige Daten ein** — die zugehörigen `EventHistory`, `EventAttachment` und `DeletionRequest` werden jetzt atomar in einer Transaktion mit-anonymisiert.
- **Login-Lockout `select_for_update` + Autocomplete `block=True`** — `select_for_update` macht den Login-Lockout-Counter gegen parallele Failed-Logins monoton. Autocomplete-Endpoint blockt unauthentifizierte Requests jetzt explizit, neuer Architektur-Test verbietet künftige Sensible-GETs ohne Auth-Check.
- **`WorkItemUpdateView`-Permission-Check** — die Permission-Prüfung der Edit-View läuft jetzt zentral über `can_user_mutate_workitem` (Server-Layer als Single Source of Truth).
- **`FacilityScopeMiddleware` leert `app.current_facility_id` für anonyme Requests** — Anonyme Requests (Login, Health, statische Assets) setzen `app.current_facility_id` jetzt explizit auf `NULL` — Defense-in-Depth für die RLS-Mandantentrennung im Connection-Pool.
- **Service-Layer-Konsistenz-Sweep** — vier Stellen aus Audit B.2.2 (RLS-Lücke bei Bulk-Aktionen, fehlende `select_for_update` auf zwei Counter-Updates) abgeräumt.
- **Validator erzwingt `is_encrypted=True` für FieldTemplate-Sensitivity HIGH** — bisher nur Form-Hint, jetzt Schema-Constraint im Save-Pfad.
- **SBOM (CycloneDX) als CI-Artefakt** — `release.yml` veröffentlicht jetzt eine `cyclonedx-bom.json` als Build-Asset; SCA-Scanner können den Stand pro Release direkt vom GitHub-Release ziehen.
- **CodeQL-Workflow** — neuer `codeql.yml` mit Python + JavaScript-Sprache, Cron + PR-Trigger; Sichtbarkeit Dev/Stage/App in [`docs/release-checklist.md`](https://github.com/anlaufstelle/app/blob/main/docs/release-checklist.md) dokumentiert.
- **Dev-Postgres an `127.0.0.1`** (#799) — `docker-compose.yml` band Postgres an `0.0.0.0:5432`, was lokal auf Multi-User-Maschinen beobachtbar war. Jetzt `127.0.0.1:5432`.

### Added

- **Vier-Augen-Lösch-Workflow für Personen mit Papierkorb-Frist** — Lösch-Anträge gehen erst nach Genehmigung durch Leitung/Admin in den Papierkorb, dort konfigurierbare Frist bis zur Hard-Deletion. Vor Ablauf ist Restore möglich.
- **Maintenance-Mode mit 503-Page** (#700) — Admin-toggelbarer Wartungsmodus mit IP-Allowlist; 503-Template im Design-System. Runbook: [`docs/ops-runbook.md` § 6.5a](https://github.com/anlaufstelle/app/blob/main/docs/ops-runbook.md).
- **Custom CSRF-Failure-Page** (#699) — Eigene 403-CSRF-Seite im App-Layout statt Django-Default; klare Handlungsanweisung („Bitte neu laden, Cookies prüfen").
- **PWA Offline-Fallback-Page für Navigation-Requests** (#701) — Service-Worker liefert eine eigene Offline-Seite für Navigation-Fetches statt der Default-Browser-Fehlerseite.
- **Aufklappbare Event-Cards im Zeitstrom** (#707) — Event-Cards lassen sich per Chevron inline aufklappen; alle Felder inkl. Textarea-Notizen direkt sichtbar. Generische `expandableCard`-Alpine-Komponente, identisches Pattern für Übergabe-Highlights.
- **Health-Checks SMTP / Encryption-Key / Backup-Alter / Disk-Frei** (#796) — `/health/` prüft jetzt zusätzlich SMTP-Erreichbarkeit, Encryption-Key-Verfügbarkeit, Backup-Alter und Disk-Frei. Kompatibler Health-Vertrag (clamav-Alias, `status`-Feld), Container-Healthcheck liest direkt das `status`-Feld (#798).
- **DSGVO-Versionsstempel + AGPL-Footer in Templates** (#840, #833, #835) — DSGVO-Paket-Footer trägt App-Version, Generierungszeitpunkt und AGPL-Hinweis; in DSGVO-Template-Sektion versioniert.
- **Threat Model (STRIDE-Lite)** — neues [`docs/threat-model.md`](https://github.com/anlaufstelle/app/blob/main/docs/threat-model.md) mit Assets, Akteuren, Vertrauensgrenzen und STRIDE-Tabellen je Boundary inkl. Mitigation und offenen Lücken.
- **Architecture Decision Records (ADRs)** (#831) — drei ADRs nachgezogen: File Vault, MFA, Suche.
- **`reencrypt_fields` rotiert auch EventHistory + EventAttachment** (#783) — Schlüssel-Rotation deckt jetzt den vollständigen Daten-Pfad ab, nicht nur die Live-Events.
- **Off-Site-Backup-Hook in `scripts/backup.sh`** — optionaler Sync-Hook nach erfolgreichem Backup; State-File und Exit-Code für wiederholte Fehlversuche (#797).
- **Backup-Restore-Drill als ausführbares Skript** — `scripts/restore-drill.sh` führt den 7-Schritt-Drill aus, prüft RLS und AuditLog-Trigger.
- **Übergabe-Highlights aufklappbar** — gleiches Toggle-Pattern wie Event-Cards in der Schichtübergabe.

### Changed

- **Sprachleitlinie „Klientel" → „Person"** (#604) — flächig durchgezogen: UI-Strings, Form-Labels, Fehlermeldungen, Handbuch, FAQ, admin-guide, README + Screenshots, Übersetzungs-Coverage-Wachhund in CI (#813, #814, #815, #834). Datums-/Zeitformate auf Django-L10N umgestellt (langes Format mit Wochentag).
- **Migrationen als One-Shot-Job vor Rolling-Restart** (#802) — `docker-compose.prod.yml` führt Migrationen jetzt in einem Init-Container aus, der vor dem Web-Service läuft. Lange RunPython-Migrationen blockieren keine Worker mehr.
- **Caddy: www-Redirect, Access-Log, Rate-Limit-Hinweis** (#801) — `www.anlaufstelle.app` redirected jetzt 301 auf Apex; Access-Log JSON-formatiert, Rate-Limit-Header dokumentiert.
- **DSGVO-Vorlagen ins App-Paket verschoben** (#784) — Templates wandern aus dem Repo-Root in `src/core/templates/dsgvo/`, sind im Paket und beim Deployment automatisch dabei.
- **Persistentes `media:`-Volume in `docker-compose.prod.yml`** — vorher Bind-Mount, das bei Coolify-Deploys verloren ging. Jetzt named volume mit Backup-/Restore-Pfad.
- **Service-Aufteilungen** — `services/event.py` in `services/events/` zerlegt (`crud.py`, `context.py`, `fields.py`, `attachments.py`, Refs #777, #804). Retention-Strategien in `core/retention/strategies.py` konsolidiert (#778). Statistik-Periodenparser extrahiert (#816). `audit_pruning` ohne `DISABLE TRIGGER` (#781).
- **`PaginatedListMixin` + `FEED_MAX_PER_TYPE` konsolidiert** (#803) — Pagination-Logik aus drei Listen-Views zusammengezogen, Feed-Maximum zentral.
- **`log_audit_event` in 8 View-Callsites** (#817) — direkter `AuditLog.objects.create`-Aufruf durch zentralen Service ersetzt; einheitliche Felder + IP-Hashing.
- **Audit-Plan-1 Quickwins////** (#819) — fünf kleine Refactorings aus dem Audit-Plan.
- **Inline-Imports an Modulkopf** (#818) — Retention-Hot-Path bekam Imports zentral, Modul-Lade-Zeit deterministisch.
- **`DocumentType.UniqueConstraint(facility, name, category)`** — vorher nur `(facility, name)`; jetzt erlaubt eine Einrichtung denselben Namen in unterschiedlichen Kategorien.

### Fixed

- **Datums-/Zeit-Tooltips in App-Sprache** (#710) — HTML5-Validation-Tooltip an `<input type="date">` folgte der Browser-Locale; jetzt lokalisierte Meldungen aus den App-Translations.
- **WorkItem Quick-Date-Buttons + Min-Date-Validierung** (#709) — „Heute / Morgen / Nächste Woche / In 2 Wochen" funktionieren wieder unter `@alpinejs/csp`.
- **WorkItem-Datumsvalidierung max. 31.12. Folgejahr** (#708, #711) — `due_date`/`remind_at` müssen ≥ heute und ≤ 31.12. Folgejahr sein, sowohl HTML5 als auch Server-Side; verhindert versehentliche „Aufgabe verschwindet im Jahr 3345".
- **A11y-Cluster** (#805–#812) — `aria-invalid` + `aria-describedby` in fünf Form-Templates, Touch-Target ≥ 44 px für Sidebar + Datum-Arrows, stabile `aria-live`-Region für HTMX-Erfolge, `role=table/row/columnheader/cell` auf Personen-Liste, `html lang` dynamisch aus `LANGUAGE_CODE`, sekundärer Text ≥ 12 px, `non_field_errors`-Block in clients/cases/workitems-Forms, `tabindex`-Anti-Pattern aus Event-Create entfernt.
- **EN-Übersetzungen entfuzzt + fehlende msgids** (#706) — 27 fuzzy-markierte Übersetzungen finalisiert, fehlende msgids für neue Features ergänzt.
- **Datei-Download nicht mehr durch Service-Worker als Offline-Fallback abgefangen** — Download-Routes werden im SW-Match jetzt explizit ausgeschlossen.
- **Download liefert 404 statt Connection-Reset bei fehlender Datei** — vorher reset-by-peer, jetzt sauberer 404 mit AuditLog-Eintrag.
- **Zeitstrom-Dienstübersicht zeigt Kennzahlen statt leerer Karten** — KPI-Berechnung griff vor Time-Filter, Cards waren leer; Reihenfolge korrigiert.
- **Aufgaben-Bearbeiten-Button nur bei Berechtigung** — Button war für alle Rollen sichtbar, scheiterte aber serverseitig; jetzt im Template gegated.
- **Auswahlwerte in „Erfasste Daten" als Labels** — Multi-Select-Werte wurden als Python-Listen-Repr gerendert; jetzt komma-separierte Labels.
- **Fälle müssen einer Person zugeordnet sein** (#680) — `Case.client` von `null=True` auf `null=False, on_delete=PROTECT`; Bestandsdaten in Migration `0080` gegen Anonym-Marker referenziert.
- **Aufgaben-Quickbutton „Nächste Woche" = heute+7** — vorher der nächste Freitag, jetzt konsistent zu Buchungssystem-Konventionen.
- **Statistik-Begriffe verständlicher + PDF-Export Halbjahresbericht** — Labels „Eindeutige Personen / Top-Personen / Verlauf"; PDF-Export liefert jetzt einen Halbjahresbericht statt nur Jahres-Summen.
- **Login-Footer mit Anlaufstelle-Marke** — Footer fehlte auf der Login-Seite, jetzt konsistent.
- **WorkItem-Status-Race + Idempotenz-Guard** — paralleles Toggle „Done"/“Reopen" konnte zwei AuditLog-Einträge schreiben; `select_for_update` + Idempotency-Token.
- **Healthcheck-Vertrag stabilisiert** (#798) — `clamav`-Alias und Container-Healthcheck lesen jetzt das `status`-Feld einheitlich; vorher Inkompatibilität bei Coolify-Deploys.
- **Off-Site-Sync State-File + Exit-Code bei wiederholtem Fehler** (#797) — wiederholtes Sync-Scheitern bricht jetzt die Backup-Rotation hart ab statt silent zu loggen.
- **Offline `escapejs` aus `data-pk`-Attributen entfernt** (#750) — `escapejs` ist für JS-Strings, nicht HTML-Attributwerte; PK-Vergleich scheiterte bei UUIDs mit `-`.
- **Offline-Marker `__files__` minimieren** (#786) — vorher kompletter Filename + Mime im Marker, jetzt nur Anzahl.

### Performance

- **Explizites `Event.search_text` + GIN-trgm-Index** (#827) — Volltextsuche aus dem Trigger generierten Index zieht jetzt auf eine pre-computed `search_text`-Spalte; Fuzzy-Suche skaliert deutlich besser bei großen Facilities.
- **Locust-Last-Tests + Nightly-Workflow + Budgets** (#825) — `locustfile.py` mit realen Routes, Nightly-CI führt Last-Tests gegen ein E2E-Setup, Budgets in der Pipeline schlagen Alarm.
- **Query-Count-Schutz für 4 Detail-Views** (#824) — `assertNumQueries` für Klient-, Fall-, Episode-, Event-Detail; verhindert N+1-Regressionen bei künftigen Refactorings.
- **`apply_attachment_changes` Bulk-Load** (#782) — pro Event-Speicherung eine Query statt N (eine pro Anhang).
- **`SESSION_SAVE_EVERY_REQUEST=False`** — HTMX-Polls und statische Routes schreiben keine Session-Update mehr; reduziert DB-Writes spürbar.
- **Pagination-Cap auf 500** — `cases`/`clients`/`audit` haben jetzt einen Server-Side-Cap, verhindert versehentliche `?page_size=10000`-Requests.
- **`AuditLog`-Retention-Pruning in `enforce_retention`** — eigener Pruning-Pfad fürs Audit-Log; Tabelle wuchs vorher unbegrenzt.
- **`select_related` für Zeitstrom-Sidebar-Workitems** — sechs Queries pro Render → eine.

### Tooling / Internal

- **pre-commit-Config** (#820) — `.pre-commit-config.yaml` mit Ruff (Lint+Format), mypy, end-of-file-fixer, trailing-whitespace.
- **Ruff-Regelsatz erweitert** (#821) — `B` (bugbear), `UP` (pyupgrade), `SIM` (simplify), `N` (naming), `S` (security) aktiviert; Bestand auf 0 gebracht.
- **mypy-Strict-Zone für `core/forms`** (#822) — strikt typisierte Zone wächst weiter; Forms-Layer komplett getypt.
- **pip-licenses Allowlist als CI-Lint** (#839) — Build bricht bei nicht-allowlisted Lizenzen; verhindert versehentliche AGPL-fremde Dependencies.
- **Translation-Coverage-Wachhund** (#813) — CI prüft, dass alle msgids übersetzt sind; verhindert untranslated-strings-Drift.
- **Coverage-Gate `--cov-fail-under=93`** (#823) — Coverage darf nicht mehr unter 93 % fallen.
- **CHANGELOG-relevant: Tag-Signing verbindlich** (#838) — Release-Tags müssen GPG-signiert sein, in der Release-Checkliste festgeschrieben.

## [0.10.2] - 2026-04-28

### Changed

- **CSP-Migration auf `@alpinejs/csp` finalisiert** (#672, PR #690) — vendored Alpine-Build durch CSP-Variante ersetzt, alle Inline-`x-data="{...}"`-Objekte auf registrierte `Alpine.data()`-Komponenten umgestellt, komplexe Expressions in Component-Methoden ausgelagert. `script-src 'unsafe-eval'` ist damit endgültig aus der globalen CSP entfernt.

### Fixed

- **CSP-Folgefehler nach `@alpinejs/csp`-Migration** (#692, #693) — Time-Filter-Tab-Highlight auf Zeitstrom-Feed (`hx-on::before-request` durch JS-Listener ersetzt), 11 Alpine-Expressions auf Computed-Getter / pre-formatierte Properties umgestellt (Toast-Farbe, Klientel-Autocomplete-Highlight, Aktivitätskarten-Pfeil, Offline-Toggle-Label, Konflikt-Diff-Tabelle, Offline-Detail-Sichtbarkeit). Architektur-Test verbietet zukünftig Ternaries, `||`/`&&`, Method-Calls und Object-Literale in `:class`/`x-text`/`x-show`/`x-if`/`x-bind:`/`x-model`-Direktiven sowie HTMX-Inline-Handler `hx-on::`.
- **Django-Admin Modal-Overlay blockt Action-Klicks** (#698) — django-unfold lädt seinen eigenen Alpine-Build, der für die Cmd+K-Suche-Modal `new AsyncFunction()`-basierte Expression-Auswertung nutzt. Globale CSP `script-src 'self'` (ohne `unsafe-eval`) blockt das, Component initialisiert nicht, `<div x-show="openCommandResults">` bleibt mit `display: flex` sichtbar und blockt Klicks. Neue `AdminCSPRelaxMiddleware` ergänzt `'unsafe-eval'` per-Request nur für `/admin-mgmt/*` (privilegierte Routes mit MFA-Gate).
- **Retention Bulk-Toolbar reagiert nicht auf Selektion** (#698) — Inline-`@change="$dispatch('retention-bulk-change')"` ist im `@alpinejs/csp`-Build verboten (Function-Calls mit String-Argumenten). Neue `notifyBulkChange()`-Method auf `proposalCard`-Component, Template nutzt `@change="notifyBulkChange"`.
- **`autosave-discard` Race-Condition** (#698) — `wait_for_url`-Test-Helper erkannte Same-URL-Reload nicht als Navigation; nachfolgende `page.evaluate` scheiterte mit "Execution context was destroyed". Test nutzt `expect_navigation`-Context-Manager, der auf `framenavigated`-Event auch bei identischer Target-URL synchronisiert.
- **`python-magic` fehlte in `requirements-dev.txt`** — `make deps-lock` regeneriert Lock-Files (drift gegenüber `.in`-Files); ohne `python-magic` scheiterten Test-Job (`ModuleNotFoundError`) und E2E-`seed --flush` (transitiv über `core.services.file_vault`).
- **CI `Test/check`-Job schlägt fehl** — Workflow-env setzte `SECRET_KEY`, `prod.py`/`base.py` lesen aber `DJANGO_SECRET_KEY`. Variable umbenannt.
- **CI `lock-check`-Job schlägt fehl** — siehe `python-magic`-Fix oben (regeneriertes Lock-File matcht jetzt `pip-compile`-Output).
- **E2E-Test-Helpers ignorieren xdist-Worker-DB** (#698) — vier Subprocess-Helper (`_seed_failed_logins_and_check_lock`, `_clear_lockout_for`, `_enable_totp_and_generate_codes`, `_cleanup_totp` in 3 Test-Files) nutzten `os.environ` als Subprocess-Env ohne `E2E_DATABASE_NAME`; Subprocess landete in default-DB `anlaufstelle_e2e` statt worker-spezifischer `anlaufstelle_e2e_1`. Helpers nehmen jetzt `e2e_env` aus der gleichnamigen Fixture als Parameter.
- **`TestZZAccountLockout` ohne Cleanup** (#698) — Tests sperrten `miriam`/`lena` per 10× LOGIN_FAILED-AuditLog ohne Cleanup; nachfolgende `_staff_storage_state`/`_assistant_storage_state`-Fixtures scheiterten weil User auf `/login/` hängenblieben. Autouse-Teardown-Fixture `_cleanup_lockout_state` ruft `login_lockout.unlock()` für miriam + lena nach jedem Test der Klasse auf (Cleanup über `LOGIN_UNLOCK`-AuditLog-Eintrag, weil `core_auditlog` einen `auditlog_immutable`-DB-Trigger hat).
- **`test_event_save_and_appears_in_detail` synchrones `is_visible()`** (#698) — `wait_for_url`-Match nach Server-Redirect kehrt sofort zurück, aber Detail-Template rendert `<dl>`/`<dd>`-Sektionen unter Last (xdist + 2 Worker auf gleicher VM) noch nicht im DOM. Asserts auf `wait_for(state="visible")` umgestellt.
- **`_ensure_proposals` Test-Helper unzuverlässig** (#698) — `RetentionProposal.objects.get_or_create(... status__in=[...])` matched approved-Proposals als unique-Constraint-Konflikt; im parallelen Run scheiterte `assert n >= 2 false`. Helper neu geschrieben: zählt pending, holt fehlende Anzahl Events ohne existierende Proposal-Verknüpfung, legt frische pending-Proposals an.

## [0.10.1] - 2026-04-26

### Added

- **Visual Refresh** — Theme „Grün" mit DM Sans/Mono (self-hosted, kein Google-CDN), OKLCH-Akzentfarbe `#2d6a4f`, neuer Sidebar mit Logo-Box und „Neu erstellen"-Dropdown, Mobile-Bottom-Nav mit 5 Slots, 3 px farbige linke Kante an Feed-Cards, KPI-Cards mit Mono-Numbers, Card-Pattern flächig auf alle Templates (#663 / PR #667).
- **Klientel-Liste responsive** — Single-Loop und CSS-Grid statt Doppel-Renderpfad für Desktop/Mobile.
- **MFA-Setup-Seite** zeigt Secret in Base32 (statt Hex) für Authenticator-Apps an.
- **MFA-Backup-Codes** als zweiten Faktor bei verlorenem Authenticator-Gerät, mit eigenem Limit pro Stunde und Audit-Log-Eintrag bei Verwendung.
- **Composite-Indexes** auf AuditLog (3×), Case, Event und WorkItem (Migration `0066`) für Listen-Filter mit Status + Datum.
- **Attachment-Versionierung Stufe B** — pro Datei-Feld eine Liste von Versionen statt Single-Slot, mit Vorversionen-Anzeige im Event-Detail.
- **Default-Werte für Feldvorlagen** (`FieldTemplate.default_value`) — Quick-Templates können Standardvorgaben befüllen.
- **FAQ-Erweiterungen** — `Hinweis` vs. `Aufgabe`, Bedeutung der `Wiedervorlage`, Grenzen des Wizards/Hausverbot-Flows.
- **Übergabe-Seite** mit 7 neuen E2E-Tests (Schicht-Wechsel, KPI-Cards, Highlights).
- **Audit-Tiefenanalysen** — drei systematische Code-Audits (`docs/audits/2026-04-{21,23,25,26}-*.md`) mit Belegscreenshots, vollständig adressiert.
- **`make ssl-cert` LAN-IP** — `SSL_HOST_IP=192.168.x.y make ssl-cert` für PWA-Tests von Mobilgeräten.

### Changed

- **Alpine-Komponenten registriert** — alle 26 inline `x-data="{ ... }"` zu `Alpine.data()`-Komponenten in [`src/static/js/alpine-components.js`](https://github.com/anlaufstelle/app/blob/main/src/static/js/alpine-components.js) extrahiert; Architektur-Test verbietet neue Inline-Verstöße. Vorbereitung für späteren Wechsel auf den `@alpinejs/csp`-Build (siehe #672).
- **EventUpdateView/EventCreateView** schlanker — neue Service-Funktionen (`apply_attachment_changes`, `attach_files_to_new_event`, `split_file_and_text_data`, `build_field_template_lookup`); `build_event_detail_context` mit `select_related` (kein N+1).
- **Magic Numbers in `core.constants`** — Pagination-Defaults, Rate-Limit-Konstanten, Cache-TTLs zentralisiert.
- **`seed.py` modularisiert** in 15 Domänen-Module (Clients, Events, Audit, Retention etc.) — vorher monolithisch.
- **Anonyme Pages auf Default-Locale forcieren** — Login/Password-Reset/MFA-Login rendern unabhängig von `Accept-Language` immer in `LANGUAGE_CODE` (de). Authentifizierte User behalten ihre Profil-Sprache als Override (Refs #670).
- **Feed-Card-Preview** für File-Marker — `__file__` und `__files__` werden als „[Datei]"/„[N Dateien]" angezeigt statt als rohes Dict-Repr (Refs #670).
- **WorkItem.item_type help_text** korrigiert — passt jetzt zu den tatsächlichen Choices (`hint`/`task`).

### Fixed

- **Alpine-Komponenten-Bootstrap** — `alpine-components.js` lädt vor `alpine.min.js`, sodass `alpine:init` die `Alpine.data()`-Registrierungen sieht; behebt 27–43 `ReferenceError`s pro Seite (Refs #670).
- **CSP-Inline-Handler** — Attachment-Entfernung im Event-Edit nutzt eigenen JS-Listener statt `onchange`-Attribut (Refs #662).
- **Offline-Queue ACK-Protokoll** — `MessageChannel`-basiertes ACK/NACK statt naivem Success-Banner; korrekte Rückmeldung an die UI bei IndexedDB-Fehlern (Refs #662).
- **File-Vault Cleanup** — Direct-Cleanup bei DB-Exception plus periodischer Orphan-Cleanup-Command (Refs #662).
- **MIME-Validierung für DOCX/OOXML** — Container-Formate werden als äquivalent zu `application/zip` erkannt und nicht mehr fälschlich als unsicher abgelehnt (Refs #662).
- **i18n f-Strings** — alle `_(f"...")` durch `_("...%(name)s...") % {"name": value}` ersetzt; Architektur-Test verbietet Rückfälle (Refs #662).
- **AuditLog für Case-Aktionen** — `close_case`, `reopen_case` und `delete_milestone` schreiben jetzt einen `AuditLog`-Eintrag (vorher silent).
- **Vorlage-entfernen-Link** löscht den Autosave-Draft mit, sonst bestand der alte Draft-Stand weiter.
- **Seed-Coverage-Pin** — `coverage.json` ignoriert; Ruff in CI auf `0.15.11` gepinnt.
- **Service-Worker Offline-POST-Handling** — Multipart-Antwort beginnt jetzt konsistent mit „Offline — Datei-Uploads erfordern eine Internetverbindung" (Präfix-Konsistenz mit Standard-Queue-Pfad). Cache-Version v6→v7. xfail-Test in [`test_pwa_offline.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/e2e/test_pwa_offline.py) durch echten URL-encoded-POST-Test ersetzt; Multipart-Pipeline wartet auf #574.
- **E2E-Browser-State-Cleanup** — neue `_cleanup_browser_state(page)`-Hilfsfunktion in [`src/tests/e2e/conftest.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/e2e/conftest.py) leert vor `context.close()` Service-Worker, IndexedDB, Cache-API und Storage, um Cross-Test-State-Pollution in der parallelen Suite zu reduzieren (#668).

### Security

- **Rate-Limits flächig** — 19 fehlende `@ratelimit`-Decorators auf POST-Handlern ergänzt (Cases, Clients, Episodes, Events, Retention, MFA-Disable, WorkItem-Update, Bulk-Aktionen). Architektur-Test `TestRateLimitOnAllMutations` verbietet neue ungeschützte Mutationen (Refs #670).
- **Account-Lockout** nach 10 Login-Fehlversuchen, Admin-Unlock im Profil.
- **MFA-Backup-Codes** als zweiter Faktor mit eigenem 5/m-Limit, Audit-Log bei Verwendung.

### Accessibility

- **`aria-hidden="true"` auf 90 dekorativen SVG-Icons** in 23 Templates — Screen Reader liest keine Path-Daten mehr vor (WCAG 2.1 SC 1.1.1). Architektur-Test `TestSvgAccessibilityGuard` verbietet künftige Verstöße (Refs #670).

### Performance

- **Doppel-Rendering Klientel-Liste** auf Single-Loop reduziert (responsive Grid statt Desktop+Mobile-Render) (Refs #643).
- **`enrich_events_with_preview`** N+1 entfernt — `select_related("field_template")` statt pro Event eigene Query (Refs #662).
- **WorkItemInbox-Pagination** auf 50 Einträge pro Liste begrenzt; Querysets nicht mehr pauschal in Templates evaluiert (Refs #639, #640).

## [0.10.0] - 2026-04-19

### Added

- **Encrypted File Vault** — verschlüsselte Datei-Anhänge an Events (AES-GCM, RFC-5987 Content-Disposition, zentraler `safe_download_response`).
- **ClamAV-Virenscan** für Datei-Uploads vor der Verschlüsselung — fail-closed, Healthcheck integriert (Refs #524).
- **Sicherer Offline-Modus (M6A)** — Offline-erfasste Events und Autosave-Drafts werden client-seitig mit AES-GCM-256 verschlüsselt in IndexedDB gespeichert. Der Schlüssel wird beim Login per PBKDF2 (600 000 Iterationen, SHA-256) aus dem Passwort + User-Salt abgeleitet, lebt nur in memory und ist `extractable: false`. Logout, Password-Change und Tab-Close machen alle Offline-Daten unlesbar (Refs #573, #576, schließt #567).
- **Offline-Queue Multipart-Schutz** — Events mit File-Anhängen werden offline mit explizitem UI-Hinweis abgelehnt statt naiv als Text zu queuen.
- **Service-Worker UUID-Pattern** — Event-/WorkItem-Edit-Routen werden jetzt korrekt mit UUID-Regex statt `\d+` gematcht.
- **Offline-Queue Replay-Sicherheit** — `response.ok`-Check verhindert stilles Löschen von Queue-Einträgen bei 4xx/5xx; exponentielles Backoff bei 5xx.
- **Streetwork-Offline Stufen 2+3** — Read-Cache für mitgenommene Klientel + Offline-Edit mit Konfliktauflösung (Side-by-Side-Diff, 3 Resolve-Actions) (Refs #575, #572).
- **TOTP-2FA** via django-otp.
- **Token-basierter Invite-Flow** — kein Klartext-Initialpasswort mehr.
- **Retention Dashboard & Legal Hold** — DSGVO-Löschfristen-UI mit Bulk-Approve/Defer/Reject und Defer-Folgeverhalten (Refs #514, #515).
- **K-Anonymisierung** als Alternative zu Hard-Delete (Refs #535).
- **Optimistic Locking** für Client, Case, Workitem, Settings (vorher nur Event) — gemeinsamer Helper `core.services.locking.check_version_conflict` (Refs #531).
- **Workitems:** „Mir zugewiesen"-Filter, Bulk-Edit für Status/Priorität/Zuweisung, `remind_at` getrennt von `due_date`, wiederkehrende Fristen mit Auto-Duplizierung bei Done (Refs #265, #266, #267, #552).
- **Quick-Templates** für vorbefüllte Event-Eingaben (Refs #494).
- **Fuzzy Search** via PostgreSQL `pg_trgm` (Tippfehler-tolerant), Threshold pro Facility konfigurierbar (Refs #536).
- **FAQ** unter `docs/faq.md` (Refs #474).
- **Coolify-Deployment-Leitfaden** + GHCR-Image (`ghcr.io/anlaufstelle/app`) (Refs #554).
- **Vendored: Dexie.js 4.2.0** als `src/static/js/dexie.min.js` (Apache-2.0). Wrapper für IndexedDB-Operationen im Offline-Modus.
- **Dateianhänge im Seed-Command** für realistische E2E-Demos.

### Changed

- **FieldTemplate.sensitivity** — Sichtbarkeit von Verschlüsselung entkoppelt (Daten-Migration), inkl. Löschschutz bei vorhandenen Daten (Refs #356).
- **Update-Views** (`ClientUpdate`, `WorkItemUpdate`) laufen jetzt über die Service-Schicht.
- **Statistik-Aggregate** als Materialized View (Refs #544).
- **CSP-Header** in Django konsolidiert (Caddyfile-CSP entfernt, Inline-Skripte externalisiert).
- **Pip-Tools Lock-Files** — `requirements.txt` / `requirements-dev.txt` werden aus `.in`-Files generiert; CI prüft Drift (Refs #526).
- **Coverage-Report** als CI-Artifact (14 Tage Retention) (Refs #469).
- **README** ergänzt um Sektion „Unterstützung bei der Einführung" (Refs #460).

### Security

- **PostgreSQL Row Level Security** als Defense-in-Depth auf 16 facility-scoped Tabellen, fail-closed bei fehlender Session-Variable (Refs #542).
- **RLS-Variable session-weit** statt per `SET LOCAL` gesetzt — pro Request neu gesetzt, für anonyme/facility-lose Requests explizit geleert; robust auch ohne `ATOMIC_REQUESTS` (Refs #586).
- **Bulk-WorkItem-Endpoints** prüfen Ownership pro Item; ein gemischter Batch ohne Berechtigung wird komplett mit 403 abgelehnt (Refs #583).
- **Django-Admin** (`/admin-mgmt/`) unterliegt MFA- und Force-Password-Change-Gates (Refs #582).
- **Prod-Settings fail-closed** für SECRET_KEY, ALLOWED_HOSTS, ENCRYPTION_KEYS.
- **Zentraler Event-Access-Loader** mit 404-Semantik statt Permission-Leak.
- **Service-Invarianten** für `create_event` und `assign_event_to_case`.
- **PasswordResetView** rate-limited.
- **EventHistory-Interpretationsstabilität** — Feldmetadaten eingefroren.
- **Sensitivity-Filter** in Suche, Aktivitätsfeed, `compute_diff`, Profilseite, Attachments-Übersicht (Slicing-Reihenfolge korrigiert).

### Fixed

- **Atomare Event+Attachment-Persistierung** — Create- und Update-Flows in `transaction.atomic`; alte Datei wird im Update erst per `transaction.on_commit` gelöscht, sonst Datenverlust bei fehlschlagendem Upload (Refs #584).
- **Fuzzy-Suche:** alle `icontains`-Treffer (auch Display-Cap-Overflow) werden aus der Similar-Sektion ausgeschlossen, damit echte Fuzzy-Kandidaten nicht verdrängt werden (Refs #580).
- **`search_trigram_threshold`** validiert auf `0.0–1.0` per Validator + DB-Constraint (Refs #581).
- **Retention:** `create_proposal` erkennt `DEFERRED` als aktiven Status, vermeidet `IntegrityError`-Fallback nach Re-Run (Refs #585).
- **RLS-Middleware** öffnet DB-Cursor nur bei authentifizierten Requests — Anonymous-Routes (Login, Health, Static) brauchen den Hit nicht (Refs #586).
- `Client.anonymize()` deckt Cases, Episodes und alle Workitems ab.
- `SETTINGS_CHANGE` + Update-Actions werden im Audit-Log geschrieben.
- `TRUSTED_PROXY_HOPS` — `get_client_ip` korrekt für Multi-Proxy.
- `password_change`-Middleware exemptet `/admin-mgmt/` statt `/admin/`.
- `dexie.min.js` sourceMappingURL für `collectstatic` entfernt.
- E2E-Test `test_inbox_shows_sections` härter selektiert (vermied falschen Match auf neues Bulk-Status-Dropdown).
- CI: E2E-Ordner vom Unit-Test-Collection ausgeschlossen, `filelock` als Dev-Dependency.

## [0.9.1] - 2026-04-05

### Added

- **Standardsprache** persistent im Nutzerprofil speichern (DE/EN)
- **Analytics Charts** — Trend-Diagramme im Statistik-Dashboard mit monatlicher Aufschlüsselung nach Dokumentationstyp, inkl. User-Guide (DE + EN)
- **Sentry-Integration** — automatische Fehlererfassung in Produktion
- **JSON-Logging** — strukturiertes Logging für Produktionsumgebung
- **Coverage-Infrastruktur** — pytest-cov mit CI-Gates für Testabdeckung
- **Test-Parallelisierung** — pytest-xdist mit Worker-Isolation + Smoke-Marker

### Fixed

- CSP `unsafe-eval` für Alpine.js — behebt kaputtes Frontend
- Kontakt ohne Klientel wird automatisch als anonym markiert
- Anonym-Checkbox entfernt — Anonymität aus fehlender Klientel ableiten
- Chart.js Registry-Konflikt bei HTMX-Swap behoben
- E2E-Tests für xdist-Parallelisierung stabilisiert
- Autocomplete-E2E-Tests: Debounce-Race-Condition & nicht-deterministische Seed-Reihenfolge behoben

### Changed

- **Produktionshärtung** — CSP-Header, Docker-Konfiguration
- **Go-Live-Vorbereitung** — Runbook, Checkliste, Staging-Pipeline, E2E-Workflow
- **Testabdeckung** erweitert: Scope, RBAC-Matrix, Deletion-Requests, Management-Commands
- **Seed-Daten** finalisiert: realistische Tagesverteilung, Heute-Logik, Mitarbeiter-Zuordnung

## [0.9.0] - 2026-03-28

Initial public release.
