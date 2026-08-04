# Datenschutz-Folgenabschätzung (Art. 35 DSGVO)

**Verantwortlicher:** {{ facility_name }}
**Erstellt:** {{ date }}
**Version:** 1.4 (Softwarestand v0.23, 2026-08-04)

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
- Kooperation mit öffentlichen Fördermittelgebern/Behörden

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

Die Anwendung kennt sieben konfigurierbare Aufbewahrungsfristen. Alle sieben sind unten mit dem für diese Einrichtung **tatsächlich konfigurierten** Wert eingesetzt.

| # | Frist | Wert | Einstellung | Wirkung bei Ablauf |
|---|-------|------|-------------|--------------------|
| 1 | Kontakte anonymer Personen | {{ retention_anonymous_days }} Tage | `retention_anonymous_days` | Ereignis wird soft-gelöscht |
| 2 | Kontakte identifizierter Personen | {{ retention_identified_days }} Tage | `retention_identified_days` | Ereignis wird soft-gelöscht |
| 3 | Qualifizierte Fälle (nach Fallabschluss) | {{ retention_qualified_days }} Tage | `retention_qualified_days` | Löschvorschlag; Soft-Delete erst nach Freigabe im 4-Augen-Prinzip |
| 4 | Abweichende Frist einzelner Dokumentationstypen | {{ retention_document_type_overrides }} | `DocumentType.retention_days` | Überschreibt die Fristen 1–3 für Ereignisse dieses Typs |
| 5 | Aktivitäten / Zeitstrom-Einträge | {{ retention_activities_days }} Tage | `retention_activities_days` | **Hart**-Löschung (kein Papierkorb) |
| 6 | Papierkorb-Frist für gelöschte Personen | {{ client_trash_days }} Tage | `client_trash_days` | Person wird automatisch anonymisiert; bis dahin wiederherstellbar |
| 7 | Audit-Log | {{ auditlog_retention_months }} Monate | `auditlog_retention_months` | Einträge werden gelöscht. `0` schaltet die Löschung **ab**; Sicherheitsverstöße, System-Marker und der Nachweis herausgegebener Berichtszeiträume sind generell ausgenommen. Letztere enthalten Zeitraum, Format und die auslösende Person; Empfänger und Zweck sind ungeprüfter Freitext (organisatorische Vorgabe: Organisation statt Ansprechperson eintragen) |

Ergänzende Regeln:

- **Abgeleitete Anonymisierung der Person:** Sobald **alle** Ereignisse einer Person soft-gelöscht sind, wird die Person beim nächsten Aufbewahrungslauf anonymisiert — auch ohne abgelaufene Papierkorb-Frist.
- **Kein eigener Löschhorizont für Fälle, Episoden, Aufgaben, Wirkungsziele und Meilensteine.** Sie hängen am Lebenszyklus der Person: ihre personenbeziehbaren Freitexte werden bei deren Anonymisierung mitgetilgt.
- **Sperrvermerke (Legal Hold, Art. 18)** halten jede automatische Löschung und Anonymisierung an, bis der Vermerk aufgehoben wird.
- **4-Augen-Prinzip** für manuelle Löschungen personenbezogener Daten.
- **Restrisiko:** Das Audit-Log ist unveränderlich (Nachweispflicht, Art. 5 Abs. 2) und wird von einer Anonymisierung **nicht** mitredigiert. Vorhandene Einträge verschwinden erst mit Frist 7 — bei `auditlog_retention_months = 0`, bei protokollierten Sicherheitsverstößen oder beim Nachweis herausgegebener Berichtszeiträume gar nicht — Letztere führen die auslösende Person und die Freitextfelder Empfänger/Zweck dauerhaft und unveränderbar mit.
- Die Fristen werden durch den täglich laufenden Aufbewahrungs-Job durchgesetzt; ist der Job nicht eingerichtet, greift **keine** der sieben Fristen.

### 2.4 Dokumentenablage an Person und Fall

Dateien lassen sich nicht nur an einzelnen Ereignissen hinterlegen, sondern auch unmittelbar an einer **Person** und an einem **Fall** (z. B. Ausweiskopie, Mietvertrag, Bescheid). Datenschutzrechtlich gelten dafür dieselben Zusagen wie für Ereignis-Anhänge, mit einer eigenen Aufbewahrungslogik:

- **Verschlüsselung at rest:** Der Dateiinhalt wird serverseitig verschlüsselt gespeichert (Fernet/MultiFernet, chunk-weise), der Originaldateiname ebenfalls; auf dem Datenträger liegt nur ein zufälliger Speichername. Die Datei durchläuft vor der Verschlüsselung dieselbe Prüfkette wie ein Ereignis-Anhang (Dateityp-Freigabe, Größenbegrenzung, Virenprüfung, Abgleich der Dateisignatur).
- **Aufbewahrung nach dem Lebenszyklus der Person (Owner-Lebenszyklus):** Personen- und fallbezogene Dateien haben **keine eigene Zeitfrist**. Sie werden — Datei **und** Datenbankeintrag — mit der Anonymisierung der Person endgültig gelöscht, also nach Ablauf der Papierkorb-Frist (Frist 6) bzw. sobald alle Ereignisse der Person gelöscht sind. Das gilt auch für Dateien an den Fällen dieser Person; einen eigenen Löschlauf für Fälle gibt es nicht. Die Fristen 1–4 wirken ausschließlich auf Ereignisse und damit nur auf ereignisbezogene Anhänge.
- **Entfernen in der Oberfläche ist noch keine Löschung:** Nimmt eine Fachkraft eine Datei aus einem Feld heraus oder ersetzt sie durch eine neuere Fassung, bleibt die bisherige Fassung als frühere Version gespeichert und wird lediglich als entfernt bzw. ersetzt gekennzeichnet. Auch ein Wechsel des Dokumentationstyps verwirft die Dateien nicht, sondern markiert sie nur.
- **Restrisiko:** Aus beidem folgt, dass eine personen- oder fallbezogene Datei bis zur Anonymisierung der Person auf dem Server verschlüsselt vorhanden bleibt — im ungünstigsten Fall also über die Papierkorb-Frist hinweg, obwohl sie in der Oberfläche längst entfernt wurde. Wo das nicht hinnehmbar ist, bleibt die manuelle Sofort-Anonymisierung der Person durch die Anwendungsbetreuung; sie löscht alle zugehörigen Dateien physisch.
- **Auskunftsrecht (Art. 15):** Die Datenauskunft weist zu jeder Datei die **Angaben** aus — Feld, Dateiname, Größe, Dateityp, Zeitpunkt und hochladende Person sowie den Stand (aktuell, ersetzt, entfernt), nicht jedoch den Dateiinhalt. Die Dateien selbst werden auf Anforderung über den regulären Download herausgegeben.

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
