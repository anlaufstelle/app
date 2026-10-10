# Verzeichnis von Verarbeitungstätigkeiten (Art. 30 DSGVO)

**Verantwortlicher:** {{ facility_name }}
**Datenschutzbeauftragte/r:** [Name und Kontakt eintragen]
**Erstellt:** {{ date }}
**Version:** 1.2 (Softwarestand v0.99.6, 2026-10-07)

---

## 1. Dokumentation sozialer Kontakte

### 1.1 Zweck der Verarbeitung
- Dokumentation von Kontakten mit begleiteten Personen
- Nachverfolgung von Beratungsgesprächen und Leistungen
- Statistische Auswertung für Fördergeber (öffentliche Fördermittelgeber/Behörden)
- Qualitätssicherung der sozialen Arbeit

### 1.2 Kategorien betroffener Personen
- Begleitete Personen der Einrichtung; Zielgruppe: [Zielgruppe eintragen, z. B. Jugendliche und junge Erwachsene, Ratsuchende einer Beratungsstelle, Bewohnerinnen eines Frauenhauses, Gäste einer Veranstaltung]
- Mitarbeitende der Einrichtung

### 1.3 Kategorien personenbezogener Daten
| Kategorie | Daten | Verschlüsselt |
|-----------|-------|---------------|
| Pseudonym | Selbstgewählter Name | Nein |
| Altersgruppe | Unter 18, 18–26, 27+, Unbekannt | Nein |
| Kontaktstufe | Nur Pseudonym, Mit Zusatzangaben | Nein |
| Kontaktdaten | Dokumentation einzelner Kontakte | Teilweise |
| Sensible Felder | Felder, die die Einrichtung als verschlüsselt konfiguriert (z. B. Krisen- oder Gesundheitsnotizen) | Ja (Fernet) |
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
Eine Übermittlung in Drittländer erfolgt nur, wenn die Einrichtung sie selbst
konfiguriert, z. B. durch einen Dienst außerhalb der EU bzw. des EWR für die
optionale Fehlerüberwachung, den E-Mail-Versand oder die Off-Site-Sicherung.
Eingesetzte Dienste: [keine / Dienst, Land und geeignete Garantie eintragen];
siehe AVV § 6.

### 1.7 Löschfristen
| Kontaktstufe | Frist | Beginn der Frist |
|--------------|-------|------------------|
| Anonym | {{ retention_anonymous_days }} Tage | ab dem jeweiligen Kontakt (Ereignis) |
| Nur Pseudonym | {{ retention_identified_days }} Tage | ab dem jeweiligen Kontakt (Ereignis) |
| Mit Zusatzangaben | {{ retention_qualified_days }} Tage | ab Fallabschluss; ohne Fall ab Ereignisdatum; offener Fall ab letzter fachlicher Fallaktivität (Fallback Anlage) |

Die Fristen gelten je Ereignis. „Höchstfrist (verkürzt nur)“ am
Dokumentationstyp ergänzt den Stichtag; eine längere Frist verlängert nichts.
Der Standardlauf erzeugt für „Mit Zusatzangaben“ Vorschläge und löscht erst nach
Freigabe durch eine andere Person mit „Löschbestätigung“. Zurückstellen schützt
bis zum Datum; Ablehnen erlaubt frühestens nach {{ retention_rejected_retry_days }}
Tagen eine neue Vorlage. Ohne Ereignis anonymisiert der Lauf nach
{{ retention_identified_days }} Tagen ab letzter Änderung. Hold und Art. 18
schützen im Lauf Ereignisse, Person und Historieninhalte. Die Soll-Matrix und
weitere Fristen (Aktivitäten, Papierkorb, Audit-Log, Ereignis-Historie und
Übergabenotizen) stehen im Löschkonzept.

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
- Benutzername, Anzeigename, E-Mail-Adresse
- Rolle (Systemadministration, Anwendungsbetreuung, Leitung, Fachkraft, Assistenz), Berechtigung zur Bestätigung von Löschanträgen, Einrichtungszuordnung
- Telefonnummer, Sprache und interne Kontonotiz, sofern diese Felder genutzt werden
- Passwort-Hash, MFA-Pflicht und -Geräte, verschlüsselte TOTP-Secrets, gehashte Wiederherstellungscodes und Sicherheitsfristen
- Anmelde-, Sperr- und Audit-Daten wie Zeitpunkt, Aktion, Konto- und Einrichtungskennung sowie bei protokollierten Vorgängen die IP-Adresse

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
- Dauerhaft ausgenommen (wie in der DSFA § 2.3, Frist 7): protokollierte Sicherheitsverstöße, System-Marker des Aufbewahrungslaufs und der Nachweis herausgegebener Berichtszeiträume. Letzterer enthält Zeitraum, Format und die auslösende Person; Empfänger und Zweck sind ungeprüfter Freitext (organisatorische Vorgabe: Organisation statt Ansprechperson eintragen)
- Einträge ohne Einrichtung: {{ auditlog_system_retention_months }} Monate installationsweit (0 = aus); dieselben dauerhaften Ausnahmen gelten
- Aufbewahrungssperren (Legal Hold) schützen Audit-Zeilen mit Einrichtung zu gesperrten Personen, Fällen und Ereignissen

Abgeschlossene Lösch-, Zusammenführungs- und Herabstufungsanträge werden nach
{{ deletion_request_retention_days }} Tagen hart gelöscht. Berichtigungsanträge
haben weiterhin keine technische Frist; Inhaltsredaktion folgt der Anonymisierung.
Monatsaggregate bleiben unbefristet als statistischer Restbestand erhalten.
