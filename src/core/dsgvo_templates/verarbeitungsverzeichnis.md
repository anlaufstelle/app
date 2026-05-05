# Verzeichnis von Verarbeitungstätigkeiten (Art. 30 DSGVO)

**Verantwortlicher:** {{ facility_name }}
**Datenschutzbeauftragte/r:** [Name und Kontakt eintragen]
**Erstellt:** {{ date }}
**Version:** 1.0 (Softwarestand v0.10, 2026-04-19)

---

## 1. Dokumentation sozialer Kontakte

### 1.1 Zweck der Verarbeitung
- Dokumentation von Kontakten mit Klientel
- Nachverfolgung von Beratungsgesprächen und Leistungen
- Statistische Auswertung für Fördergeber (Jugendamt)
- Qualitätssicherung der sozialen Arbeit

### 1.2 Kategorien betroffener Personen
- Klientel der Einrichtung (Jugendliche und junge Erwachsene)
- Mitarbeitende der Einrichtung

### 1.3 Kategorien personenbezogener Daten
| Kategorie | Daten | Verschlüsselt |
|-----------|-------|---------------|
| Pseudonym | Selbstgewählter Name | Nein |
| Alterscluster | Unter 18, 18–26, 27+, Unbekannt | Nein |
| Kontaktstufe | Identifiziert, Qualifiziert | Nein |
| Kontaktdaten | Dokumentation einzelner Kontakte | Teilweise |
| Sensible Notizen | Freitextnotizen mit sensiblen Inhalten | Ja (AES/Fernet) |

### 1.4 Rechtsgrundlage
- Art. 6 Abs. 1 lit. e DSGVO (öffentliches Interesse)
- Art. 9 Abs. 2 lit. g DSGVO (erhebliches öffentliches Interesse)
- SGB VIII (Kinder- und Jugendhilfe)

### 1.5 Empfänger
- Mitarbeitende der Einrichtung (je nach Rolle)
- Jugendamt (anonymisierte Statistik)
- IT-Dienstleister (Auftragsverarbeitung, siehe AVV)

### 1.6 Übermittlung in Drittländer
Keine.

### 1.7 Löschfristen
| Kontaktstufe | Frist nach letztem Kontakt |
|--------------|---------------------------|
| Anonym | {{ retention_anonymous_days }} Tage |
| Identifiziert | {{ retention_identified_days }} Tage |
| Qualifiziert | {{ retention_qualified_days }} Tage |

### 1.8 Technische und organisatorische Maßnahmen
Siehe separates TOM-Dokument.

---

## 2. Mitarbeiterverwaltung

### 2.1 Zweck der Verarbeitung
- Benutzerkonten für Systemzugang
- Nachvollziehbarkeit von Änderungen (Audit-Log)

### 2.2 Kategorien personenbezogener Daten
- Benutzername, Anzeigename
- Rolle (Admin, Leitung, Fachkraft, Assistenz)
- Anmeldezeitpunkte (Audit-Log)

### 2.3 Rechtsgrundlage
- Art. 6 Abs. 1 lit. b DSGVO (Arbeitsvertrag)

### 2.4 Löschfristen
- Bei Ausscheiden: Deaktivierung, Löschung nach 3 Jahren

---

## 3. Audit-Log

### 3.1 Zweck der Verarbeitung
- Nachvollziehbarkeit von Datenzugriffen (DSGVO-Compliance)
- Sicherheitsüberwachung

### 3.2 Kategorien personenbezogener Daten
- Benutzername, IP-Adresse, Zeitstempel
- Art der Aktion (Anmeldung, Export, Löschung etc.)

### 3.3 Rechtsgrundlage
- Art. 6 Abs. 1 lit. c DSGVO (rechtliche Verpflichtung)
- Art. 6 Abs. 1 lit. f DSGVO (berechtigtes Interesse an Sicherheit)

### 3.4 Löschfristen
- Aufbewahrung: 1 Jahr, danach automatische Löschung
