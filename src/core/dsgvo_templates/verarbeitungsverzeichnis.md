# Verzeichnis von Verarbeitungstätigkeiten (Art. 30 DSGVO)

**Verantwortlicher:** {{ facility_name }}
**Datenschutzbeauftragte/r:** [Name und Kontakt eintragen]
**Erstellt:** {{ date }}
**Version:** 1.1 (Softwarestand v0.89, 2026-08-28)

---

## 1. Dokumentation sozialer Kontakte

### 1.1 Zweck der Verarbeitung
- Dokumentation von Kontakten mit begleiteten Personen
- Nachverfolgung von Beratungsgesprächen und Leistungen
- Statistische Auswertung für Fördergeber (öffentliche Fördermittelgeber/Behörden)
- Qualitätssicherung der sozialen Arbeit

### 1.2 Kategorien betroffener Personen
- Begleitete Personen der Einrichtung (Jugendliche und junge Erwachsene)
- Mitarbeitende der Einrichtung

### 1.3 Kategorien personenbezogener Daten
| Kategorie | Daten | Verschlüsselt |
|-----------|-------|---------------|
| Pseudonym | Selbstgewählter Name | Nein |
| Altersgruppe | Unter 18, 18–26, 27+, Unbekannt | Nein |
| Kontaktstufe | Nur Pseudonym, Mit Zusatzangaben | Nein |
| Kontaktdaten | Dokumentation einzelner Kontakte | Teilweise |
| Sensible Notizen | Freitextnotizen mit sensiblen Inhalten | Ja (AES/Fernet) |
| Sperrvermerk (Art. 18) | Kennzeichen, Rechtsgrund (Art. 18 Abs. 1 lit. a–d), Zeitpunkt, setzende Person | Nein |

### 1.4 Rechtsgrundlage
- Art. 6 Abs. 1 lit. e DSGVO (öffentliches Interesse)
- Art. 9 Abs. 2 lit. g DSGVO (erhebliches öffentliches Interesse)
- Einrichtungsspezifische Grundlage: einschlägiges Fach- und Sozialrecht der Einrichtung (konkrete Rechtsgrundlage vor Freigabe ergänzen)

### 1.5 Empfänger
- Mitarbeitende der Einrichtung (je nach Rolle)
- Öffentliche Fördermittelgeber/Behörden (anonymisierte Statistik)
- IT-Dienstleister (Auftragsverarbeitung, siehe AVV)

### 1.6 Übermittlung in Drittländer
Keine.

### 1.7 Löschfristen
| Kontaktstufe | Frist | Beginn der Frist |
|--------------|-------|------------------|
| Anonym | {{ retention_anonymous_days }} Tage | ab dem jeweiligen Kontakt (Ereignis) |
| Nur Pseudonym | {{ retention_identified_days }} Tage | ab dem jeweiligen Kontakt (Ereignis) |
| Mit Zusatzangaben | {{ retention_qualified_days }} Tage | ab Abschluss des Falls, dem das Ereignis zugeordnet ist |

Die Fristen gelten je Ereignis; ein neuer Kontakt verlängert ältere Ereignisse
nicht. Eine Frist am Dokumentationstyp gilt zusätzlich (es gilt die zuerst
ablaufende). Ereignisse der Stufe „Mit Zusatzangaben“, die keinem Fall
zugeordnet sind oder zu einem noch offenen Fall gehören, verfallen nicht über
diese Frist, sondern nur über eine Frist am Dokumentationstyp; solange sie
bestehen, wird auch die Person nicht anonymisiert. Der tägliche
Aufbewahrungslauf löscht abgelaufene Ereignisse ohne Freigabeschritt; Personen
werden anonymisiert, sobald alle ihre Ereignisse gelöscht sind. Ausgenommen sind Daten unter einer Aufbewahrungssperre (Legal
Hold). Weitere Fristen (Aktivitäten, Papierkorb, Audit-Log, Ereignis-Historie,
Sicherungsstände) und die Statistik-Aggregate nennt das Löschkonzept.

### 1.8 Technische und organisatorische Maßnahmen
Siehe separates TOM-Dokument.

### 1.9 Einschränkung der Verarbeitung (Art. 18 DSGVO)
Bestreitet eine betroffene Person die Richtigkeit ihrer Daten (Art. 18 Abs. 1 lit. a) oder liegt einer der übrigen Tatbestände des Art. 18 Abs. 1 vor, kann an der Person ein **Sperrvermerk** gesetzt werden. Wirkung in der Software:

- Die Daten bleiben **gespeichert** (Art. 18 Abs. 2) — der Vermerk ist kein Löschsperrvermerk.
- Inhaltliche Verarbeitung ist gesperrt: Die Person kann nicht mehr bearbeitet und keinem neuen Kontakt, Fall oder Aufgabe zugeordnet werden.
- **Ausgenommen** ist der Berichtigungsworkflow (Art. 16) — er ist der Weg, auf dem die bestrittene Richtigkeit geprüft wird.
- Gespeichert wird nur der Rechtsgrund als Werteliste, nicht der strittige Sachverhalt; dieser liegt verschlüsselt am Berichtigungsantrag.
- Setzen und Aufheben werden protokolliert. Das Aufheben verlangt eine zweite berechtigte Person oder die Leitung; die Unterrichtungspflicht nach Art. 18 Abs. 3 (Hinweis an die betroffene Person vor dem Aufheben) ist **organisatorisch** zu erfüllen — die Software erzwingt sie nicht.

**Abgrenzung:** Der Sperrvermerk ist **nicht** der Legal Hold. Der Legal Hold blockiert die *Löschung* (Art. 17 Abs. 3), der Sperrvermerk die *Nutzung*. Beide können unabhängig voneinander bestehen.

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
- Aufbewahrung: {{ auditlog_retention_months }} Monate rollierend, danach automatische Löschung (`0` schaltet die Löschung **ab**)
- Ebenfalls dauerhaft: der Nachweis je Charge der Historien-Redaktion (er belegt, dass die Unveränderlichkeit für bestimmte Zeilen legitim geöffnet wurde, und enthält Anzahl, Fristgrenze und Chargengrenzen — keine Inhalte)
- Dauerhaft ausgenommen (wortgleich zur DSFA § 2.3, Frist 7): protokollierte Sicherheitsverstöße, System-Marker des Aufbewahrungslaufs und der Nachweis herausgegebener Berichtszeiträume. Letzterer enthält Zeitraum, Format und die auslösende Person; Empfänger und Zweck sind ungeprüfter Freitext (organisatorische Vorgabe: Organisation statt Ansprechperson eintragen)
