# Auftragsverarbeitungsvertrag (Art. 28 DSGVO)

**Vorlage für Vereinbarung mit IT-Dienstleistern**

**Verantwortlicher:** {{ facility_name }}
**Erstellt:** {{ date }}
**Version:** 1.1 (Softwarestand v0.89, 2026-08-28)

---

## Präambel

zwischen

**{{ facility_name }}** (nachfolgend „Verantwortlicher")
[Adresse]

und

**[Dienstleister]** (nachfolgend „Auftragsverarbeiter")
[Adresse]

wird folgende Vereinbarung zur Auftragsverarbeitung geschlossen:

---

## § 1 Gegenstand und Dauer

(1) Der Auftragsverarbeiter verarbeitet personenbezogene Daten im Auftrag des Verantwortlichen im Rahmen folgender Leistungen:
- [ ] Hosting der Webanwendung „Anlaufstelle"
- [ ] Datenbank-Hosting (PostgreSQL)
- [ ] Backup-Speicherung
- [ ] Weitere: _______________

(2) Die Verarbeitung beginnt mit Unterzeichnung dieses Vertrags und endet mit Beendigung der Zusammenarbeit.

---

## § 2 Art und Zweck der Verarbeitung

Die Verarbeitung umfasst:
- Speicherung verschlüsselter Datenbankinhalte
- Bereitstellung der Webanwendung über HTTPS
- Erstellung und Speicherung von Backups
- Technischer Support (ohne Zugriff auf Inhalte)

---

## § 3 Art der personenbezogenen Daten

Folgende Datenkategorien werden verarbeitet:
- Pseudonyme (keine Klarnamen)
- Altersgruppe
- Kontaktstufen
- Verschlüsselte Kontaktnotizen
- Mitarbeiter-Zugangsdaten

---

## § 4 Kategorien betroffener Personen

- Begleitete Personen der Einrichtung (pseudonymisiert)
- Mitarbeitende der Einrichtung

---

## § 5 Pflichten des Auftragsverarbeiters

Der Auftragsverarbeiter verpflichtet sich:

(1) Die Daten nur auf dokumentierte Weisung des Verantwortlichen zu verarbeiten.

(2) Die zur Verarbeitung befugten Personen zur Vertraulichkeit zu verpflichten.

(3) Geeignete technische und organisatorische Maßnahmen zu ergreifen (siehe Anlage TOM).

(4) Unterauftragnehmer nur mit vorheriger Genehmigung des Verantwortlichen einzusetzen.

(5) Den Verantwortlichen bei der Erfüllung der Betroffenenrechte zu unterstützen.

(6) Die Daten nach Beendigung zu löschen oder zurückzugeben.

(7) Dem Verantwortlichen alle erforderlichen Nachweise zur Verfügung zu stellen.

---

## § 6 Unterauftragsverarbeitung

(1) Zutreffendes auswählen und die Liste vor Unterzeichnung vervollständigen:

- [ ] **Keine Unterauftragnehmer** werden eingesetzt.
- [ ] Folgende Unterauftragnehmer werden eingesetzt:

| Dienst/Kategorie | Anbieter und Anschrift | Leistung / betroffene Daten | Verarbeitungsort, Drittlandgarantie | AVV geprüft am |
|------------------|-------------------------|------------------------------|--------------------------------------|----------------|
| Tracker-Host (Sentry/GlitchTip) | [eintragen] | Fehler- und optional Performance-Telemetrie | [eintragen] | [Datum] |
| SMTP-Dienst | [eintragen] | Einladungs- und Sicherheits-E-Mails | [eintragen] | [Datum] |
| Offsite-Backup-Speicher | [eintragen] | Verschlüsselte Datenbank- und Medienarchive | [eintragen] | [Datum] |
| Weitere | [eintragen] | [eintragen] | [eintragen] | [Datum] |

Nicht eingesetzte Beispielzeilen sind zu streichen. Eine selbst betriebene
Komponente ist nur dann als Unterauftragnehmer aufzuführen, wenn ein externer
Dienstleister daran personenbezogene Daten im Auftrag verarbeitet.

(2) Neue Unterauftragnehmer bedürfen der schriftlichen Genehmigung.

---

## § 7 Kontrollrechte

(1) Der Verantwortliche hat das Recht, die Einhaltung dieses Vertrags zu überprüfen.

(2) Der Auftragsverarbeiter stellt hierfür alle erforderlichen Informationen bereit.

---

Ort, Datum: ____________________

Verantwortlicher: ____________________

Auftragsverarbeiter: ____________________
