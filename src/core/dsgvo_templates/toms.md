# Technische und Organisatorische Maßnahmen (Art. 32 DSGVO)

**Verantwortlicher:** {{ facility_name }} — Dokumentationssystem „Anlaufstelle"
**Erstellt:** {{ date }}
**Version:** 1.1 (Softwarestand v0.10, 2026-04-19)

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
- [x] Zwei-Faktor-Authentifizierung (TOTP) pro Benutzer oder facility-weit erzwingbar; zeitbasierte Einmalcodes (30 s) via Authenticator-App (Ref. )

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
- [x] 4-Augen-Prinzip für Löschungen qualifizierter Daten
- [x] Sensitivitätsstufen pro Dokumentationstyp und Feld
- [x] PostgreSQL Row Level Security (RLS) als Defense-in-Depth: 18 Facility-gescopte Tabellen mit Policies, Session-Variable `app.current_facility_id`, fail-closed bei NULL (Ref. )

### 1.4 Trennungskontrolle
*Maßnahmen zur getrennten Verarbeitung verschiedener Zwecke*

- [x] Mandantenfähigkeit: Logische Trennung durch Facility-Scoping
- [x] Separate Audit-Log-Tabelle
- [x] Getrennte Datenbanken für Entwicklung, Test und Produktion

---

## 2. Integrität (Art. 32 Abs. 1 lit. b DSGVO)

### 2.1 Weitergabekontrolle
*Schutz bei Übertragung und Transport*

- [x] HTTPS/TLS für alle Verbindungen
- [x] HSTS aktiviert
- [x] Feldverschlüsselung für sensible Daten (AES/Fernet)
- [x] Schlüssel getrennt von Datenbank (Umgebungsvariable)
- [x] Schlüsselrotation ohne Datenverlust (MultiFernet)
- [x] Browser-seitige Verschlüsselung offline erfasster Daten mit AES-GCM-256; Schlüsselableitung per PBKDF2 (600 000 Iterationen, SHA-256) aus Passwort und User-Salt; Schlüssel ist non-extractable und existiert ausschließlich in-memory. Logout, Passwortwechsel oder Schließen des Tabs machen alle offline gehaltenen Daten unlesbar (Ref. )

### 2.2 Eingabekontrolle
*Nachvollziehbarkeit von Dateneingaben*

- [x] Audit-Log für sicherheitsrelevante Aktionen
- [x] Event-History (append-only) für Änderungsnachverfolgung
- [x] Benutzer- und Zeitstempel bei allen Datensätzen
- [x] Malware-Scanning aller Datei-Uploads (ClamAV) vor der Verschlüsselung — fail-closed bei nicht erreichbarem Scanner (Ref. )
- [x] Protokollierung aller Datei-Uploads, -Downloads und -Ersetzungen im AuditLog (Action-Codes `FILE_UPLOAD`, `FILE_DOWNLOAD`, `FILE_REPLACE`)
- [x] Optimistic Locking für Client, Case, WorkItem, Settings und Event — verhindert stilles Überschreiben bei parallelen Bearbeitungen (Ref. )

---

## 3. Verfügbarkeit und Belastbarkeit (Art. 32 Abs. 1 lit. b, c DSGVO)

- [ ] Regelmäßige Backups (Datenbank + Medien)
- [ ] Backup-Verschlüsselung
- [ ] Disaster-Recovery-Plan
- [ ] Redundante Infrastruktur

---

## 4. Verfahren zur regelmäßigen Überprüfung (Art. 32 Abs. 1 lit. d DSGVO)

- [ ] Jährliche Überprüfung der TOMs
- [ ] Penetrationstests
- [ ] Schulungen Mitarbeitende (jährlich)
- [ ] Überprüfung der Zugriffsrechte (quartalsweise)
