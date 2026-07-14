# Datenschutz-Folgenabschätzung (Art. 35 DSGVO)

**Verantwortlicher:** {{ facility_name }}
**Erstellt:** {{ date }}
**Version:** 1.1 (Softwarestand v0.10, 2026-04-19)

---

## 1. Beschreibung der Verarbeitungsvorgänge

### 1.1 Art der Verarbeitung
Die Software „Anlaufstelle" dient der Dokumentation von Kontakten mit Jugendlichen und jungen Erwachsenen in niedrigschwelligen sozialen Einrichtungen.

### 1.2 Umfang der Verarbeitung
- Pseudonymisierte Kontaktdokumentation
- Verschlüsselte Speicherung sensibler Daten
- Statistische Auswertung für Fördergeber

### 1.3 Kontext der Verarbeitung
- Soziale Arbeit mit vulnerablen Gruppen
- Niedrigschwellige Anlaufstelle (kein Zwang zur Identifikation)
- Kooperation mit Jugendamt

### 1.4 Zweck der Verarbeitung
- Qualitätssicherung der sozialen Arbeit
- Nachweis der Leistungserbringung gegenüber Fördergebern
- Kontinuität in der Betreuung

---

## 2. Notwendigkeit und Verhältnismäßigkeit

### 2.1 Erforderlichkeit der Daten
| Datum | Erforderlich weil |
|-------|------------------|
| Pseudonym | Wiedererkennung ohne Klarnamen |
| Alterscluster | Statistische Auswertung |
| Kontaktstufe | Anpassung der Löschfristen |
| Kontaktnotizen | Qualität der Betreuung |

### 2.2 Datensparsamkeit
- Keine Klarnamen erforderlich
- Altersangabe nur in Clustern (nicht exakt)
- Sensible Daten verschlüsselt

### 2.3 Speicherbegrenzung
- Automatische Löschfristen nach Kontaktstufe:
 - Anonym: {{ retention_anonymous_days }} Tage
 - Identifiziert: {{ retention_identified_days }} Tage
 - Qualifiziert: {{ retention_qualified_days }} Tage
- 4-Augen-Prinzip für manuelle Löschungen

---

## 3. Risikobewertung

### 3.1 Identifizierte Risiken

| Risiko | Eintrittswahrscheinlichkeit | Schwere | Maßnahmen |
|--------|----------------------------|---------|-----------|
| Unbefugter Zugriff auf Daten | Mittel | Hoch | Verschlüsselung, Rollenmodell |
| Datenverlust | Niedrig | Hoch | Backup, Redundanz |
| Identifikation durch Pseudonym | Niedrig | Mittel | Schulung Mitarbeitende |
| Zweckentfremdung | Niedrig | Mittel | Audit-Log, Berechtigungen |

### 3.2 Risiken für Betroffene
- Stigmatisierung bei Bekanntwerden des Kontakts
- Diskriminierung bei Arbeitgeber/Behörden
- Psychische Belastung bei Datenmissbrauch

---

## 4. Abhilfemaßnahmen

### 4.1 Technische Maßnahmen
- AES-Verschlüsselung sensibler Felder (Fernet/MultiFernet)
- TLS für Datenübertragung
- Automatische Session-Timeouts
- Passwort-Policy (min. 12 Zeichen)
- Account-Sperrung nach 10 Fehlversuchen
- Schlüsselrotation ohne Datenverlust (MultiFernet)
- Malware-Schutzschicht: ClamAV scannt sämtliche Datei-Uploads vor der serverseitigen Verschlüsselung; infizierte Dateien werden abgelehnt (fail-closed) (Ref. #524)
- Defense-in-Depth auf Datenbankebene: PostgreSQL Row Level Security (RLS) bildet eine zweite Verteidigungslinie gegen facility-übergreifende Datenabflüsse. Auch bei Fehlern im ORM-Layer verhindert RLS einen Cross-Tenant-Zugriff (Ref. #542)
- Endgeräteschutz durch Offline-Kryptografie: Offline im Browser vorgehaltene Daten werden client-seitig mit einem **non-extractable** AES-GCM-256-Session-Schlüssel verschlüsselt in einer separaten IndexedDB (`anlaufstelle-crypto`) abgelegt. Der Schlüssel wird bei der Anmeldung aus dem Benutzerpasswort abgeleitet (PBKDF2, 600 000 Iterationen, SHA-256), ist nicht exportierbar und verlässt das Gerät nicht — ein gestohlenes Gerät ohne aktive Session liefert nur Chiffretext. Schlüssel **und** verschlüsselter Bestand werden verworfen, sobald das Gerät länger als die Session-Dauer (Default 1800 s, einrichtungskonfigurierbar) untätig war (geprüft bei Boot, im 60-Sekunden-Intervall, bei Tab-Rückkehr und tab-übergreifend); bewusst **kein** Wipe beim Schließen/Verbergen des Tabs, damit das Wiederöffnen im aufsuchenden Einsatz innerhalb des Idle-Fensters weiter funktioniert (akzeptiertes Restrisiko F-01). **TOM-relevant:** Die für die Offline-Ansicht nötigen Inhalte einschließlich Art.-9-Daten werden serverseitig entschlüsselt und als vorgefiltertes Bundle in den Browser geliefert (dort erst wieder client-seitig verschlüsselt); die serverseitigen Sichtbarkeits- und Rechte-Filter bleiben autoritativ. **Benanntes Restrisiko:** Klartext-Index-Metadaten (Datensatz- und Personen-Bezugs-IDs, Kontaktzeitpunkte, Queue-Ziel-URLs) bleiben als Datenbank-Indizes unverschlüsselt lesbar (bewusster Verzicht). DSFA-/TOM-Einordnung und Restrisiken: ADR-022 § Akzeptierte Restrisiken (Ref. ADR-022, #1065)
- Zwei-Faktor-Authentifizierung: TOTP-basierte 2FA ist technisch umgesetzt und kann pro Benutzer oder facility-weit **erzwungen** werden (zuvor ausschließlich optional). Senkt das Risiko bei kompromittierten Passwörtern erheblich (Ref. #521)

### 4.2 Organisatorische Maßnahmen
- Rollenbasierte Zugriffskontrolle (4 Stufen)
- 4-Augen-Prinzip für Löschungen qualifizierter Daten
- Audit-Logging aller sicherheitsrelevanten Aktionen
- Regelmäßige Schulung der Mitarbeitenden
- Automatische Löschfristen pro Kontaktstufe

---

## 5. Stellungnahme des Datenschutzbeauftragten

[Hier Stellungnahme einfügen]

---

## 6. Ergebnis

Die Risiken für die Rechte und Freiheiten der betroffenen Personen werden durch die implementierten technischen und organisatorischen Maßnahmen auf ein akzeptables Niveau reduziert.

Datum: {{ date }}
Unterschrift Verantwortlicher: ____________________
