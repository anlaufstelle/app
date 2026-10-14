# Informationspflichten (Art. 13/14 DSGVO)

**Datenschutzhinweise für begleitete Personen der Einrichtung**

**Verantwortlicher:** {{ facility_name }}
**Erstellt:** {{ date }}
**Version:** 1.2 (Softwarestand v0.99.6, 2026-10-07)

---

## 1. Verantwortlicher

{{ facility_name }}
[Adresse eintragen]
[Kontaktdaten eintragen]

---

## 2. Datenschutzbeauftragte/r

[Name und Kontaktdaten eintragen]

---

## 3. Zwecke der Verarbeitung

Wir verarbeiten Ihre Daten zu folgenden Zwecken:
- Dokumentation der Kontakte und Beratungsgespräche
- Sicherstellung der Betreuungskontinuität
- Nachweis der Leistungserbringung gegenüber Fördergebern
- Statistische Auswertung (anonymisiert)

---

## 4. Rechtsgrundlage

- Art. 6 Abs. 1 lit. e DSGVO (Wahrnehmung einer Aufgabe im öffentlichen Interesse)
- Art. 9 Abs. 2 lit. g DSGVO (erhebliches öffentliches Interesse)
- Einrichtungsspezifische Grundlage: einschlägiges Fach- und Sozialrecht der Einrichtung (konkrete Rechtsgrundlage vor Freigabe ergänzen)

---

## 5. Verarbeitete Daten

| Datum | Beschreibung |
|-------|-------------|
| Pseudonym | Selbstgewählter Name (kein Klarname erforderlich) |
| Altersgruppe | Grobe Einordnung (Unter 18, 18–26, 27+, Unbekannt) |
| Kontaktstufe | Nur Pseudonym oder Mit Zusatzangaben |
| Kontaktdokumentation | Dokumentation von Kontakten und Beratungsinhalten; einzelne sensible Felder werden je nach Konfiguration der Einrichtung verschlüsselt gespeichert, die übrigen Angaben nicht |

---

## 6. Empfänger

- Mitarbeitende der Einrichtung (je nach Rolle und Berechtigung)
- Öffentliche Fördermittelgeber/Behörden (nur anonymisierte Statistik)
- IT-Dienstleister (im Rahmen der Auftragsverarbeitung)

Eine Übermittlung in Drittländer erfolgt nur, wenn die Einrichtung sie selbst
konfiguriert, z. B. durch einen Dienst außerhalb der EU bzw. des EWR für die
Fehlerüberwachung, den E-Mail-Versand oder die externe Datensicherung.
Eingesetzte Dienste: [keine / Dienst, Land und geeignete Garantie eintragen].

---

## 7. Speicherdauer

| Kontaktstufe | Aufbewahrung | Beginn der Frist |
|--------------|--------------|------------------|
| Anonym | {{ retention_anonymous_days }} Tage | ab dem jeweiligen Kontakt |
| Nur Pseudonym | {{ retention_identified_days }} Tage | ab dem jeweiligen Kontakt |
| Mit Zusatzangaben | {{ retention_qualified_days }} Tage | ab Fallabschluss; ohne Fall ab Kontakt; offener Fall ab letzter fachlicher Aktivität (ersatzweise Anlage) |

Bei anonymen Kontakten und „Nur Pseudonym“ hat jeder Kontakt seine eigene
Frist; ein späterer Kontakt verlängert frühere Fristen nicht. Bei „Mit
Zusatzangaben“ gilt im offenen Fall die letzte fachliche Fallaktivität als
gemeinsamer Fristbeginn. Ein neuer Kontakt im offenen Fall kann deshalb auch
die Frist älterer Kontakte verschieben. Eine kürzere Typ-Höchstfrist ab dem
jeweiligen Kontakt bleibt wirksam.

Für die Stufe „Mit Zusatzangaben“ schlägt der tägliche Lauf eine Löschung vor.
Im Regelfall geben zwei verschiedene berechtigte Personen mit dem Recht „Löschbestätigung“ sie frei; auch eine
kürzere Typ-Frist ersetzt diese Freigabe nicht. Zurückstellen oder Ablehnen
schützt vor der Löschung, bis der Vorschlag erneut geprüft wird.

Den Vorschlag legt der Lauf an. Die manuelle Freigabe braucht zwei verschiedene aktive Personen der eigenen Einrichtung, jeweils mit Rolle Leitung oder Anwendungsbetreuung und dem Recht „Löschbestätigung“. Die erste Freigabe lässt den Vorschlag ausstehend (PENDING, „1 von 2 Freigaben“); erst die zweite andere Person gibt ihn endgültig frei (APPROVED). Mit nur einem berechtigten Konto bleibt es bei „1 von 2“; die erste Freigabe allein erlaubt keine Löschung. Zurückstellen, Legal Hold (auch aus der Personen-, Fall- oder Ereignisakte) und Ablehnung verwerfen die erste Freigabe mit Audit; bei einem erneuten manuellen Freigabeweg sind wieder zwei Freigaben nötig. Die Einrichtung benennt mindestens zwei berechtigte Konten. Die Compliance-Karte „Genehmiger für den Aufbewahrungslauf“ prüft Rolle und Anzahl (0 = kritisch, 1 = Warnung, mindestens 2 = ok). Die Karte „Genehmiger für Löschanträge“ zählt weiterhin aktive Löschantragsberechtigte unabhängig von ihrer Rolle. Ausnahme: Die Einrichtung kann die Auto-Freigabe nach mehrfacher Zurückstellung ausdrücklich einschalten (Opt-in, standardmäßig aus); nach der konfigurierten maximalen Zahl von Zurückstellungen und Ablauf der Wiedervorlage genehmigt der Lauf ohne zweite Person. Hold und Art. 18 schützen auch diesen Weg.

Abweichend davon kann die Einrichtung die Auto-Freigabe ausdrücklich aktivieren (Opt-in, standardmäßig aus): Nach der konfigurierten maximalen Zahl von Zurückstellungen und Ablauf der Wiedervorlage genehmigt der Lauf den Vorschlag ohne zweite Person. Hold und Art. 18 schützen auch diesen Weg.

Bei anonymen Kontakten und „Nur Pseudonym“ entfernt der Lauf die Inhalte nach
Fristablauf automatisch. Sind alle Kontakte gelöscht, werden die Angaben zu
Ihrer Person anonymisiert.

Ohne Kontakt gilt die Inaktivitätsfrist von {{ retention_identified_days }} Tagen nur für „Nur Pseudonym“ ohne offene Fälle. Maßgeblich ist der jüngste Zeitpunkt aus der letzten Änderung der Personenangaben, der letzten fachlichen Aktivität in einem Fall, der letzten Änderung einer Aufgabe und der letzten Änderung eines Anhangs an Person oder Fall (Anlage, Ersetzung oder Entfernung). Personen „Mit Zusatzangaben“ ohne Ereignis sind von der automatischen Anonymisierung ausgenommen; der Vorschlagsweg unterstützt bisher nur Ereignisse, für diese Personen fehlt eine automatische Fristvollstreckung. Personen „Nur Pseudonym“ ohne Ereignis mit offenem Fall sind vom Inaktivitätszweig ausgenommen. Angaben zu Personen „Mit Zusatzangaben“ ohne Kontakt werden nicht automatisch gelöscht; die Einrichtung prüft sie [Prüfintervall eintragen] und stellt bei Bedarf einen Löschantrag. Aufbewahrungssperre und Art.-18-Sperrvermerk stellen
die automatische Löschung, Anonymisierung und Historien-Redaktion zurück.
Frühere Historienfassungen werden regulär im Nachtlauf redigiert; offene
Fassungen bleiben im Personenstatus als zurückgestellt sichtbar.
In Off-Site-Sicherungen können gelöschte Daten bis zum tatsächlichen Ablauf der Sicherung erhalten bleiben. Die Einrichtung legt am Ziel eine Höchstfrist von 12 Monaten ab Erstellung des Sicherungsstands einschließlich Versionen und Snapshots fest und weist den Ablauf nach. Die konkret eingerichtete Frist beträgt [Off-Site-Aufbewahrungsfrist eintragen] (höchstens 12 Monate). `backup.sh` löscht am Off-Site-Ziel nichts; der App-Host erhält dort keine Löschrechte.

Statistische Zählwerte ohne Bezug zu Ihrer Person bleiben erhalten. Die
Monatsaggregate haben keine Frist. Abgeschlossene
Löschanträge verfallen nach {{ deletion_request_retention_days }} Tagen; Audit
folgt seiner eigenen Frist ({{ auditlog_retention_months }} Monate in der
Einrichtung, {{ auditlog_system_retention_months }} Monate ohne Einrichtung;
0 = keine Bereinigung, Holds und dauerhafte Ausnahmen bleiben geschützt).
In Sicherungskopien bleiben Daten bis zu deren Ablauf erhalten; die Fristen
dafür regelt das Löschkonzept der Einrichtung.

---

## 8. Ihre Rechte

Sie haben folgende Rechte bezüglich Ihrer personenbezogenen Daten:

- **Auskunftsrecht (Art. 15 DSGVO):** Sie können Auskunft über Ihre gespeicherten Daten verlangen.
- **Berichtigungsrecht (Art. 16 DSGVO):** Sie können die Berichtigung unrichtiger Daten verlangen.
- **Löschungsrecht (Art. 17 DSGVO):** Sie können die Löschung Ihrer Daten verlangen.
- **Einschränkungsrecht (Art. 18 DSGVO):** Sie können die Einschränkung der Verarbeitung verlangen.
- **Datenübertragbarkeit (Art. 20 DSGVO):** Sie können Ihre Daten in einem maschinenlesbaren Format erhalten.
- **Widerspruchsrecht (Art. 21 DSGVO):** Sie können der Verarbeitung widersprechen.

---

## 9. Beschwerderecht

Sie haben das Recht, sich bei einer Datenschutz-Aufsichtsbehörde zu beschweren:

[Zuständige Aufsichtsbehörde eintragen]

---

## 10. Automatisierte Entscheidungsfindung

Eine automatisierte Entscheidungsfindung oder Profiling findet nicht statt.
