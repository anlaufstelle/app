# Technische und Organisatorische Maßnahmen (Art. 32 DSGVO)

**Verantwortlicher:** {{ facility_name }} — Dokumentationssystem „Anlaufstelle"
**Erstellt:** {{ date }}
**Version:** 1.2 (Softwarestand v0.89, 2026-08-28)

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

### 1.2 Zugangskontrolle
*Maßnahmen, die Unbefugte an der Nutzung der Systeme hindern*

**Authentifizierung:**
- [x] Benutzername und Passwort erforderlich
- [x] Passwort-Mindestlänge: 12 Zeichen
- [x] Passwort-Komplexitätsanforderungen
- [x] Kontosperrung nach 10 Fehlversuchen (30 Min)
- [x] Erzwungener Passwortwechsel bei Erstanmeldung
- [x] Zwei-Faktor-Authentifizierung (TOTP) pro Benutzer oder facility-weit erzwingbar; zeitbasierte Einmalcodes (30 s) via Authenticator-App (Ref. #521)
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
| Admin | Alle | Alle | 4-Augen | Ja | Ja |
| Leitung | Alle | Alle | 4-Augen | Ja | Nein |
| Fachkraft | Alle | Eigene | Eigene (identifiziert) | Nein | Nein |
| Assistenz | Nicht-sensibel | Eigene | Nein | Nein | Nein |

**Technische Umsetzung:**
- [x] Serverseitige Berechtigungsprüfung
- [x] Template-seitige Sichtbarkeitssteuerung
- [x] 4-Augen-Prinzip für manuelle Löschungen von Personen sowie von Ereignissen und Fällen der Stufe „Mit Zusatzangaben“ (der automatische Aufbewahrungslauf löscht ohne Freigabe)
- [x] Sensitivitätsstufen pro Dokumentationstyp und Feld
- [x] PostgreSQL Row Level Security (RLS) als Defense-in-Depth für alle direkt oder über Beziehungen einrichtungsbezogenen Tabellen; Policies verwenden `app.current_facility_id` und arbeiten ohne gesetzten Kontext fail-closed

### 1.4 Trennungskontrolle
*Maßnahmen zur getrennten Verarbeitung verschiedener Zwecke*

- [x] Mandantenfähigkeit: Logische Trennung durch Facility-Scoping
- [x] Separate Audit-Log-Tabelle
- [x] Getrennte Datenbanken für Entwicklung, Test und Produktion

### 1.5 Restrisiko nicht feldverschlüsselter Freitexte

> Diese Freitext-Felder folgen **nicht** dem Sensitivity/Encryption/Retention-Modell der Event-`data_json`-Felder. Klartext liegt facility-gescoped in der DB und im Backup (Backup ist AES-256-CBC verschlüsselt; DB selbst nicht).

Betroffen sind `Client.notes`, `Case.description`, `Episode.description`,
`WorkItem.description` und `AuditLog.detail`. Die fachlichen Eingabefelder
warnen deshalb davor, Klarnamen oder Art.-9-Daten dort einzutragen, und die
Anonymisierungs-Kaskade säubert die vier Fach-Freitexte.
`AuditLog.detail` bleibt als Teil des unveränderlichen, HMAC-verketteten
Protokolls bestehen und wird beim Schreiben minimiert. Eine feldweise Verschlüsselung ist bis post-v1.0 vertagt.
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
- [x] Schlüsselrotation ohne Datenverlust (MultiFernet)
- [x] Browser-seitige Verschlüsselung offline erfasster Daten mit AES-GCM-256; Schlüsselableitung per PBKDF2 (600 000 Iterationen, SHA-256) aus Passwort und User-Salt; Schlüssel ist non-extractable und existiert ausschließlich in-memory. Logout, Passwortwechsel oder Schließen des Tabs machen alle offline gehaltenen Daten unlesbar (Ref. #573)

### 2.2 Eingabekontrolle
*Nachvollziehbarkeit von Dateneingaben*

- [x] Audit-Log für sicherheitsrelevante Aktionen
- [x] Event-History (append-only) für Änderungsnachverfolgung
- [x] Benutzer- und Zeitstempel bei allen Datensätzen
- [x] Malware-Scanning aller Datei-Uploads (ClamAV) vor der Verschlüsselung — fail-closed bei nicht erreichbarem Scanner (Ref. #524)
- [x] Protokollierung aller Datei-Uploads, -Downloads und -Ersetzungen im AuditLog (Action-Codes `FILE_UPLOAD`, `FILE_DOWNLOAD`, `FILE_REPLACE`)
- [x] Optimistic Locking für Client, Case, WorkItem, Settings und Event — verhindert stilles Überschreiben bei parallelen Bearbeitungen (Ref. #531)

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
