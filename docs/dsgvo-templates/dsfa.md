# Datenschutz-Folgenabschätzung (Art. 35 DSGVO)

**Verantwortlicher:** {{ facility_name }}
**Erstellt:** {{ date }}
**Version:** 1.0

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
