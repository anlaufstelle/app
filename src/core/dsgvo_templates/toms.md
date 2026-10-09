# Technische und Organisatorische Maßnahmen (Art. 32 DSGVO)

**Verantwortlicher:** {{ facility_name }} — Dokumentationssystem „Anlaufstelle"
**Erstellt:** {{ date }}
**Version:** 1.3 (Softwarestand v0.99.6, 2026-10-07)

---

## 1. Vertraulichkeit (Art. 32 Abs. 1 lit. b DSGVO)

### 1.1 Zutrittskontrolle
*Maßnahmen, die Unbefugten den Zutritt zu Datenverarbeitungsanlagen verwehren*

**Hosting/Server:**
- [ ] Rechenzentrum mit physischer Zugangskontrolle
- [ ] 24/7 Überwachung
- [ ] Zutrittsprotokollierung

**Büro/Einrichtung:**
- [ ] Abschließbare Räume
- [ ] Besucherregelung
- [ ] Sensibilisierung Mitarbeitende
- [ ] Geräteverschlüsselung und Bildschirmsperre auf allen Geräten mit Offline-Nutzung; keine geteilten Konten auf solchen Geräten

### 1.2 Zugangskontrolle
*Maßnahmen, die Unbefugte an der Nutzung der Systeme hindern*

**Authentifizierung:**
- [x] Benutzername und Passwort erforderlich
- [x] Passwort-Mindestlänge: 12 Zeichen
- [x] Passwort-Komplexitätsanforderungen
- [x] Kontosperrung nach 10 Fehlversuchen innerhalb von 15 Minuten je Konto und Quell-IP; die Sperre endet, sobald weniger als 10 gezählte Fehlversuche in den letzten 15 Minuten liegen (ohne neue Fehlversuche spätestens 15 Minuten nach dem letzten). Zusätzlich Ratelimits der Anmeldung (5 Versuche/Minute je IP, 10/Stunde je Benutzername). Das Systemadministrationskonto ist von der Sperre ausgenommen und nur durch die Ratelimits gebremst.
- [x] Erzwungener Passwortwechsel bei Erstanmeldung
- [x] Zwei-Faktor-Authentifizierung (TOTP, ergänzend Passkeys) per Rollen-Default für alle Rollen verpflichtend (Administration sofort, übrige Rollen nach 30 Tagen Übergangsfrist), zusätzlich pro Benutzer oder einrichtungsweit erzwingbar; zeitbasierte Einmalcodes (30 s) via Authenticator-App
- [x] Passkeys/WebAuthn optional als zusätzlicher zweiter Faktor neben TOTP; Registrierung nur nach bestätigtem TOTP und in bereits 2FA-verifizierter Sitzung
- [x] Passkeys dienen nur als zweiter Faktor; kein passwordless Login: Ein Passkey ersetzt weder Passwort noch TOTP-/Backup-Code-Wiederherstellung
- [ ] Passwordless Login und eine einrichtungsweite Passkey-Pflicht sind bewusst auf eine spätere Ausbaustufe vertagt

**Session-Management:**
- [x] Automatische Abmeldung nach Inaktivität
- [x] Session-Cookies HTTPOnly
- [x] CSRF-Schutz

### 1.3 Zugriffskontrolle
*Maßnahmen zur Beschränkung auf autorisierte Daten*

**Rollenkonzept:**
| Rolle | Lesen | Schreiben | Löschen | Export | Admin |
|-------|-------|-----------|---------|--------|-------|
| Systemadministration | Alle Einrichtungen, nur lesend über die Verwaltungsoberfläche (`/admin-mgmt/`, mit Sudo) und den Systembereich (`/system/`); protokolliert (`SYSTEM_VIEW`) werden jeder Aufruf im Systembereich sowie die Listen- und Detailansichten von Personen, Fällen, Ereignissen, Ereignis-Historie, Aufgaben und Anhängen, nicht die übrigen Verwaltungsansichten (z. B. Löschanträge, Benutzer, Einstellungen, Audit-Log); schlägt die Protokollierung fehl, bleibt der Zugriff möglich | Keine Fachdaten | Nein | Nein | Installationsweit |
| Anwendungsbetreuung | Alle Sensitivitätsstufen (Normal, Erhöht, Hoch) | Alle | Personen: 4-Augen; Ereignisse und Fälle: 4-Augen bei „Mit Zusatzangaben“, sonst direkt; Dateien: direkt (Sofortlöschweg) | Ja | Ja (eigene Einrichtung) |
| Leitung | Alle Sensitivitätsstufen (Normal, Erhöht, Hoch) | Alle | Personen: 4-Augen; Ereignisse und Fälle: 4-Augen bei „Mit Zusatzangaben“, sonst direkt; Dateien: direkt (Sofortlöschweg) | Ja | Nein |
| Fachkraft | Bis Sensitivität „Erhöht“ | Alle für sie sichtbaren Ereignisse | Personen: Antrag (4-Augen); nur eigene Ereignisse und für sie sichtbare Fälle: 4-Augen bei „Mit Zusatzangaben“, sonst direkt; Dateien: kein Sofortlöschweg | Nein | Nein |
| Assistenz | Nur Sensitivität „Normal“, auch bei Personen der Stufe „Mit Zusatzangaben“ | Eigene | Nein | Nein | Nein |

Maßgeblich für das Lesen ist die höhere Sensitivität aus Dokumentationstyp und Feld, verglichen mit der Rolle. Die Kontaktstufe einer Person steuert die Sichtbarkeit nicht; sie wirkt auf die zulässigen Dokumentationstypen, die Vier-Augen-Löschung, die Protokollierung beim Öffnen einer Person der Stufe „Mit Zusatzangaben“ und die Aufbewahrungsfristen.

**Technische Umsetzung:**
- [x] Serverseitige Berechtigungsprüfung
- [x] Template-seitige Sichtbarkeitssteuerung
- [x] 4-Augen-Prinzip für manuelle Löschungen von Personen sowie von Ereignissen und Fällen der Stufe „Mit Zusatzangaben“ (der automatische Aufbewahrungslauf löscht ohne Freigabe); Dateien vernichten Leitung und Anwendungsbetreuung über den Sofortlöschweg ohne zweite Person, ein aktiver Legal Hold oder ein Sperrvermerk nach Art. 18 DSGVO an der Person sperrt ihn
- [x] Sensitivitätsstufen pro Dokumentationstyp und Feld; ein Feld kann die Stufe seines Dokumentationstyps nur anheben
- [x] PostgreSQL Row Level Security (RLS) als Defense-in-Depth für alle direkt oder über Beziehungen einrichtungsbezogenen Tabellen; Policies verwenden `app.current_facility_id` und arbeiten ohne gesetzten Kontext fail-closed. Ausnahme: In Sitzungen der Systemadministration setzt die Anwendung zusätzlich `app.is_super_admin`; die Policies lassen dann einrichtungsübergreifende Zugriffe zu

### 1.4 Trennungskontrolle
*Maßnahmen zur getrennten Verarbeitung verschiedener Zwecke*

- [x] Mandantenfähigkeit: Logische Trennung durch Facility-Scoping
- [x] Separate Audit-Log-Tabelle
- [x] Getrennte Datenbanken für Entwicklung, Test und Produktion

### 1.5 Restrisiko nicht feldverschlüsselter Freitexte

> Diese Freitext-Felder folgen **nicht** dem Sensitivity/Encryption/Retention-Modell der Event-`data_json`-Felder. Klartext liegt facility-gescoped in der DB und im Backup (Backup ist AES-256-CBC verschlüsselt; DB selbst nicht).

Betroffen sind die Freitexte `Client.notes`, `Case.title`, `Case.description`,
`Episode.title`, `Episode.description`, `WorkItem.title`,
`WorkItem.description`, `OutcomeGoal.title`, `OutcomeGoal.description` und
`Milestone.title`, die Begründungen `DeletionRequest.reason` und
`LegalHold.reason` sowie `AuditLog.detail`. Die Notizen der Person und die
Beschreibungen von Fall, Episode und Aufgabe warnen davor, Klarnamen oder
Art.-9-Daten dort einzutragen; die Titelfelder und `OutcomeGoal.description`
stehen ohne Warnhinweis. Die Anonymisierung einer Person (manuell oder durch
den Aufbewahrungslauf, auch in der k-anonymisierenden Variante) überschreibt
alle hier genannten Freitexte außer `AuditLog.detail`: Notizen, Titel und
Beschreibungen von Person, Fall, Episode, Aufgabe, Wirkungsziel und
Meilenstein sowie die Begründungen der Löschanträge und Aufbewahrungssperren
zu dieser Person, ihren Fällen und Ereignissen, die durch „[Anonymisiert]“
ersetzt werden. Die Antrags- und Sperrzeilen selbst (Status, beteiligte
Personen, Zeitpunkte) bleiben als Nachweis bestehen; eine eigene
Aufbewahrungsfrist für sie gibt es nicht. Bis zur Anonymisierung stehen die
Begründungen im Klartext.
`AuditLog.detail` bleibt als Teil des HMAC-verketteten Protokolls unverändert
bestehen, bis die Audit-Frist abläuft (Ausnahmen: Löschkonzept § 3.3), und wird
beim Schreiben minimiert. Eine
feldweise Verschlüsselung ist bis post-v1.0 vertagt.
Das verbleibende organisatorische
Risiko ist bei Einweisung, Datenminimierung und regelmäßiger TOM-Prüfung zu
berücksichtigen.

---

## 2. Integrität (Art. 32 Abs. 1 lit. b DSGVO)

### 2.1 Weitergabekontrolle
*Schutz bei Übertragung und Transport*

- [x] HTTPS/TLS für alle Verbindungen
- [x] HSTS aktiviert
- [x] Feldverschlüsselung für sensible dynamische Daten und TOTP-Secrets mit Fernet/MultiFernet; Schlüssel getrennt von der Datenbank und ohne Datenverlust rotierbar
- [x] File-Vault: Anhänge werden vor der Ablage in Fernet-Chunks verschlüsselt; Originaldateinamen liegen ebenfalls Fernet-verschlüsselt vor
- [x] Schlüssel getrennt von Datenbank (Umgebungsvariable)
- [x] Schlüsselrotation ohne Datenverlust (MultiFernet) für Datenbankfelder (`reencrypt_fields`) und Dateianhänge (`reencrypt_files`); Altschlüssel erst nach ungefilterter Primärschlüssel-Prüfung (`reencrypt_files --verify`), Klärung nicht erfasster Import-/Staging-Dateien und Ablauf betroffener lokaler/Off-Site-Backups entfernen, Prozesse neu starten und ungefiltert erneut prüfen. Ein Schlüsselring je Installation, keine Einrichtungsschlüssel; das Manifest dokumentiert Fortschritt, keine Integrität.
- [x] Browser-seitige Verschlüsselung offline gehaltener Inhalte mit AES-GCM-256; Schlüsselableitung per PBKDF2 (600 000 Iterationen, SHA-256) aus Passwort und User-Salt
- [x] Der Schlüssel ist non-extractable und liegt als `CryptoKey` in einer eigenen IndexedDB des Browsers (`anlaufstelle-crypto`), damit Neuladen sowie Tab-, Browser- und Geräteneustart die Offline-Daten nicht unbrauchbar machen. „Non-extractable“ verhindert das Auslesen der Schlüsselbytes, nicht das Entschlüsseln mit dem gespeicherten Schlüssel im Browser dieses Geräts
- [x] Verschlüsselt sind die Inhalte; Ordnungsmerkmale (Datensatz- und Personen-IDs, Bearbeitungsstatus, Zeitstempel, Ziel-Adressen der Sende-Warteschlange) liegen unverschlüsselt daneben
- [x] Abmelden löscht Schlüssel und Offline-Speicher, auch ungesendete Arbeit; ein Passwortwechsel ebenso (bei ungesendeter Arbeit fragt die Anwendung vorher nach). Nach Ablauf der Leerlaufzeit löscht die Anwendung beim nächsten Ausführen den Schlüssel; ungesendete Arbeit bleibt verschlüsselt erhalten und wird nach erneuter Anmeldung nur wieder lesbar, wenn Passwort und Offline-Salt des Kontos unverändert sind; den Salt erneuert der Server bei Rollenwechsel, Deaktivierung und einer Anmeldung mit korrektem Passwort an einem gesperrten Konto, eine Passwortänderung (auch das Zurücksetzen) ändert die Schlüsselableitung ohnehin — danach ist der alte Bestand nicht mehr entschlüsselbar (zwischengespeicherte Daten werden verworfen, ungesendete Einträge als nicht zustellbar angezeigt). Ohne ungesendete Arbeit wird auch der Offline-Speicher gelöscht. Schließen des Tabs oder Browsers und ein Geräteneustart sind kein Löschweg (Einzelheiten: DSFA § 4.1)

### 2.2 Eingabekontrolle
*Nachvollziehbarkeit von Dateneingaben*

- [x] Audit-Log für sicherheitsrelevante Aktionen
- [x] Ereignis-Historie für Änderungsnachverfolgung: Jede Fassung wird angehängt und im laufenden Betrieb nicht überschrieben. Der Aufbewahrungslauf redigiert die Werte früherer Fassungen nach `eventhistory_detail_months` — die jüngste Fassung eines nicht gelöschten Ereignisses behält ihre Werte, Fassungen gelöschter Ereignisse und anonymisierter Personen redigiert er ohne Frist — und löscht redigierte Fassungen nach `eventhistory_chronicle_months`; Fassungen unter einem Legal Hold überspringt er. Jede Redaktion hinterlässt einen dauerhaften Nachweis (`EVENT_HISTORY_REDACTED`) im Audit-Log. Bereits erzeugte Sicherungsstände erreicht die Redaktion nicht
- [x] Benutzer- und Zeitstempel bei allen Datensätzen
- [x] Malware-Scanning aller Datei-Uploads (ClamAV) vor der Verschlüsselung — fail-closed bei nicht erreichbarem Scanner
- [x] Protokollierung der Datei-Vorgänge im AuditLog: Hochladen und Ersetzen (`ATTACHMENT_UPLOAD`, Modus Hinzufügen bzw. Ersetzen), Herunterladen (`DOWNLOAD`), Entfernen und Vernichten (`ATTACHMENT_DELETE`)
- [x] Optimistic Locking für Client, Case, WorkItem, Settings und Event — verhindert stilles Überschreiben bei parallelen Bearbeitungen

---

## 3. Verfügbarkeit und Belastbarkeit (Art. 32 Abs. 1 lit. b, c DSGVO)

- [x] Betriebsskripte für konsistente Backups von Datenbank und Medien unter einer PostgreSQL-Administrationsrolle, die FORCE RLS vollständig sichern kann
- [x] Datenbank- und Medienarchive werden mit AES-256-CBC/PBKDF2 verschlüsselt und durch ein getrenntes HMAC-SHA256-Sidecar vor Manipulation geschützt; Restore und Verifikation prüfen das HMAC vor der Entschlüsselung
- [x] Lokale Rotation in tägliche, wöchentliche und monatliche Sicherungsstände: `backup.sh` entfernt einen Stand beim nächsten erfolgreichen Lauf, sobald er ein Alter von mindestens 8/29/91 vollen Tagen erreicht hat (Altersgrenze, keine feste Höchstfrist); nach einem fehlgeschlagenen Off-Site-Abgleich setzt `backup.sh` die lokale Rotation bis zum nächsten erfolgreichen Abgleich aus
- [ ] Aufbewahrungsregel am Off-Site-Ziel festgelegt und eingerichtet — Off-Site-Kopien löscht `backup.sh` nicht
- [ ] Einrichtungsspezifische Zeitplanung, Offsite-Kopie und regelmäßige Restore-Übung dokumentiert und geprüft
- [ ] Disaster-Recovery-Plan
- [ ] Redundante Infrastruktur

---

## 4. Verfahren zur regelmäßigen Überprüfung (Art. 32 Abs. 1 lit. d DSGVO)

- [ ] Jährliche Überprüfung der TOMs
- [ ] Penetrationstests
- [ ] Schulungen Mitarbeitende (jährlich)
- [ ] Überprüfung der Zugriffsrechte (quartalsweise)
