# Löschkonzept (Art. 5 Abs. 1 lit. e und Art. 17 DSGVO)

**Verantwortlicher:** {{ facility_name }}
**Erstellt:** {{ date }}
**Version:** 1.0 (Softwarestand v0.89, 2026-08-28)

---

## 1. Ziel und Geltungsbereich

Dieses Konzept beschreibt die in „Anlaufstelle" technisch durchgesetzten
Aufbewahrungs- und Löschregeln. Es gilt für die Datenbank, den verschlüsselten
File-Vault und die mit `scripts/ops/backup.sh` erzeugten Sicherungsstände. Die
Einrichtung prüft die eingesetzten Fristen vor Freigabe des Dokuments und nach
jeder Änderung ihrer Einstellungen oder Backup-Rotation.

## 2. Fristen und Löschwirkung

| Datenkategorie | Frist | Quelle | Wirkung bei Ablauf |
|----------------|-------|--------|--------------------|
| Kontakte anonymer Personen | {{ retention_anonymous_days }} Tage | `retention_anonymous_days` | Ereignis wird soft-gelöscht (Frist ab dem Zeitpunkt des Ereignisses) |
| Kontakte der Stufe „Nur Pseudonym" | {{ retention_identified_days }} Tage | `retention_identified_days` | Ereignis wird soft-gelöscht (Frist ab dem Zeitpunkt des jeweiligen Ereignisses; ein neuer Kontakt verlängert ältere Ereignisse nicht) |
| Fälle der Stufe „Mit Zusatzangaben" nach Fallabschluss | {{ retention_qualified_days }} Tage | `retention_qualified_days` | Ereignis wird soft-gelöscht (Frist ab Abschluss des verknüpften Falls) |
| Abweichende Frist einzelner Dokumentationstypen | {{ retention_document_type_overrides }} | `DocumentType.retention_days` | Zusätzliche Frist für Ereignisse dieses Typs (ab Zeitpunkt des Ereignisses): Es gilt die zuerst ablaufende Frist. Eine längere Typ-Frist verlängert die kontaktstufenbezogene Frist nicht |
| Aktivitäten / Zeitstrom-Einträge | {{ retention_activities_days }} Tage | `retention_activities_days` | Harte Löschung |
| Papierkorb für gelöschte Personen und Fälle | {{ client_trash_days }} Tage | `client_trash_days` | Automatische Anonymisierung; bis dahin Wiederherstellung möglich |
| Audit-Log | {{ auditlog_retention_months }} Monate | `auditlog_retention_months` | Rollierende Löschung; `0` deaktiviert sie, gesetzliche und technische Ausnahmen siehe § 3.4 |
| Inhalte früherer Fassungen eines Ereignisses | {{ eventhistory_detail_months }} Monate | `eventhistory_detail_months` | Die Werte der Fassung werden gelöscht, die Fassung selbst (Zeitpunkt, handelnde Person, Feldnamen) bleibt; `0` deaktiviert diese Frist. Bei einem **gelöschten** Ereignis geschieht dasselbe unabhängig von dieser Frist, mit dem nächsten Aufbewahrungslauf |
| Chronologie früherer Fassungen (nach ihrer Inhalts-Löschung) | {{ eventhistory_chronicle_months }} Monate | `eventhistory_chronicle_months` | Die inhaltsleere Fassung wird gelöscht; gemessen ab dem Zeitpunkt ihrer Inhalts-Löschung. `0` deaktiviert die Löschung — dann wird die Chronologie unbefristet aufbewahrt |
| Monatliche Statistik-Aggregate | unbefristet | — | Bleiben bestehen: Vor jeder Löschung durch den Aufbewahrungslauf sichert die Anwendung die betroffenen Monate als Zählwerte ohne Einzeldatensätze (siehe § 3.5) |
| Backup-Sicherungsstände | {{ backup_retention_days }} Tage | Längste lokale Rotation im ausgelieferten `backup.sh` | Lokales verschlüsseltes Archiv wird durch die Rotation entfernt (täglich nach 7, wöchentlich nach 28, monatlich nach 90 Tagen); ausgesetzt, solange der Off-Site-Abgleich fehlschlägt |
| Off-Site-Kopien der Sicherungsstände | [eintragen] | Aufbewahrungsregel am Off-Site-Ziel (von der Einrichtung festzulegen) | `backup.sh` löscht am Off-Site-Ziel nichts; Kopien bleiben bis zur Löschung durch die Regel am Ziel bestehen |

Die Werte stammen beim Erzeugen dieses Dokuments aus den Einstellungen der
Einrichtung. Die Backup-Frist entspricht der längsten lokalen Rotation des
ausgelieferten `backup.sh`; der Wert dokumentiert die Rotation, er steuert das
Skript nicht. Weicht die betriebliche Rotation davon ab, muss die Einrichtung
den Wert im freigegebenen Löschkonzept organisatorisch anpassen. Die Frist der
Off-Site-Kopien trägt die Einrichtung selbst ein: `backup.sh` überträgt
Sicherungsstände an ein Off-Site-Ziel nur hinzufügend und entfernt dort keine
älteren Stände.

## 3. Technische Löschregeln

### 3.1 Ereignisse, Personen und Fälle

- Der tägliche Aufbewahrungslauf (`enforce_retention`) setzt abgelaufene
  Ereignisse gemäß den Kontaktstufen- und Dokumentationstyp-Fristen auf
  gelöscht — für alle Kontaktstufen unmittelbar, ohne Löschvorschlag und
  ohne Freigabe durch eine Person. Ausgenommen sind nur Daten unter einem
  aktiven Legal Hold.
- Mit der Löschung werden die Inhalte des Ereignisses (Zusatzfelder, Suchtext)
  sofort geleert und seine Dateianhänge gelöscht. Die Ereigniszeile bleibt als
  gelöscht markiert bestehen; die Werte früherer Fassungen fallen spätestens
  mit dem nächsten Aufbewahrungslauf weg.
- Optional legt ein gesonderter Vorschlagslauf (`enforce_retention --propose`)
  für abgelaufene Ereignisse Löschvorschläge im Löschfristen-Dashboard an; die
  ausgelieferte Zeitplanung ruft ihn nicht auf. Leitung und
  Anwendungsbetreuung können einen Vorschlag freigeben, zurückstellen,
  verwerfen oder dort einen Legal Hold setzen. Die Entscheidung hält den
  täglichen Lauf nicht auf: Er löscht abgelaufene Ereignisse unabhängig vom
  Vorschlagsstatus. Weder Zurückstellen noch Verwerfen schützt das Ereignis —
  nur ein Legal Hold.
- Manuelle Löschungen folgen dem Vier-Augen-Prinzip: Die Löschung einer Person
  braucht immer die Bestätigung einer zweiten berechtigten Person, die eines
  Ereignisses oder Falls nur bei Personen der Stufe „Mit Zusatzangaben".
- Sind alle Ereignisse einer Person gelöscht, anonymisiert ein nachfolgender
  Lauf ihre identifizierenden Inhalte. Eine Person, zu der nie ein Ereignis
  dokumentiert wurde, erfasst dieser Weg nicht. Für manuell gelöschte Personen
  und Fälle gilt zuvor die oben ausgewiesene Papierkorb-Frist.
- Fälle haben keine zusätzliche eigene Aufbewahrungsfrist. Episoden,
  Wirkungsziele, Meilensteine und Aufgaben folgen dem Lebenszyklus ihres Falls
  beziehungsweise ihrer Person.
- Aktive Aufbewahrungssperren (Legal Hold) halten automatische Löschung und
  Anonymisierung bis zu ihrer Aufhebung an; eine Sperre auf eine Person oder
  einen Fall schützt auch deren Ereignisse. In der Oberfläche lässt sich eine
  Sperre nur auf einem Löschvorschlag setzen, also nur, wenn der
  Vorschlagslauf eingerichtet ist. Rechtsgrundlage ist Art. 17 Abs. 3
  DSGVO (Ausnahme von der Löschpflicht) — **nicht** Art. 18. Der Sperrvermerk
  nach Art. 18 ist das Gegenstück: Er lässt die Daten gespeichert, sperrt aber
  ihre inhaltliche Nutzung und Änderung.

### 3.2 Dateien

Dateien an Ereignissen, Personen und Fällen liegen im File-Vault verschlüsselt.
Ein bloßes Entfernen oder Ersetzen in der Oberfläche löscht eine Datei noch
nicht physisch. Die endgültige Löschung folgt dem Lebenszyklus des zugehörigen
Datensatzes; der dafür vorgesehene Sofortlöschweg beseitigt Datei und Versionen
ersatzlos, sofern kein Legal Hold greift.

Bereits beim Hochladen greift die Datenminimierung nach Art. 5 Abs. 1 lit. c
DSGVO: Bilddateien werden vor der Verschlüsselung neu kodiert und verlieren
dabei ihre eingebetteten Metadaten — insbesondere den GPS-Aufnahmeort, die
Gerätekennung, den Aufnahmezeitpunkt und das eingebettete Vorschaubild. Diese
Angaben entstehen dadurch gar nicht erst als aufzubewahrender Bestand. Für
Dokumentformate (PDF, Office) gilt das nicht; deren Dokumenteigenschaften
können Angaben über die bearbeitende Beschäftigte enthalten und sind
organisatorisch zu behandeln.

### 3.3 Audit-Log

Das Audit-Log wird rollierend nach `auditlog_retention_months` bereinigt. Der
Wert `0` schaltet die reguläre Löschung ab. Sicherheitsverstöße,
System-Marker des Aufbewahrungslaufs und Nachweise herausgegebener
Berichtszeiträume bleiben von der regulären Frist ausgenommen. Diese Ausnahmen
sind bei der Festlegung und Prüfung des Löschkonzepts gesondert zu bewerten.

### 3.4 Sicherungsstände

Anwendungsseitige Löschung entfernt Daten aus dem aktiven System, ändert aber
keine bereits erzeugten, verschlüsselten und per HMAC integritätsgesicherten
Backup-Archive. Es gilt: **„unverzüglich ≠ Zerstörung integritätsgesicherter Sicherungsstände"**. Eine nachträgliche Manipulation einzelner Inhalte würde
die Wiederherstellbarkeit und den Integritätsnachweis der Sicherung aufheben.

Die betroffenen Inhalte verschwinden aus den lokalen Sicherungsständen mit
deren Rotation (spätestens nach {{ backup_retention_days }} Tagen). Das gilt nur,
solange die Rotation läuft: Schlägt der Off-Site-Abgleich fehl, setzt
`backup.sh` die lokale Rotation aus, bis ein Abgleich wieder gelingt.
Off-Site-Kopien erreicht diese Rotation nicht — `backup.sh` löscht am
Off-Site-Ziel nichts. Dort verschwinden die Inhalte erst durch eine eigene
Aufbewahrungsregel am Ziel (etwa eine Lebenszyklus-Regel des Speicherdienstes),
deren Frist die Einrichtung in § 2 einträgt.

Der Aufbewahrungslauf läuft nach der nächtlichen Sicherung. Eine Sicherung
enthält deshalb auch die Daten, die der Lauf kurz danach löscht; sie bleiben bis
zur Rotation dieses Sicherungsstands erhalten. Backups dürfen nur zur
Wiederherstellung verwendet werden, nicht als Facharchiv. Nach einem Restore
sind die seit Sicherungszeitpunkt fälligen Aufbewahrungs- und Löschläufe vor
der erneuten Freigabe des Systems auszuführen.

### 3.5 Statistik-Aggregate

Bevor der Aufbewahrungslauf Ereignisse löscht, sichert die Anwendung die
betroffenen Monate als monatliche Statistik-Aggregate. Sie enthalten Zählwerte,
keine Einzeldatensätze, und haben keine Frist: Sie bleiben nach der Löschung
der Ereignisse bestehen, damit Auswertungen vergangener Zeiträume erhalten
bleiben. Gespeichert werden die ungekürzten Zählwerte; erst in Berichten und
Exporten werden Werte unterhalb der k-Anonymitäts-Schwelle der Einrichtung
unterdrückt. Die Aggregate entfallen erst mit der Einrichtung selbst.

## 4. Zuständigkeiten und Nachweis

- Die Anwendungsbetreuung prüft die einrichtungsbezogenen Fristen und —
  sofern der Vorschlagslauf eingerichtet ist — offene Löschvorschläge.
- Die Systemadministration überwacht Aufbewahrungslauf, Backup-Rotation und
  Restore-Tests sowie die sichere Vernichtung abgelaufener Sicherungsstände.
  Für Off-Site-Kopien richtet sie die Aufbewahrungsregel am Ziel ein und prüft
  deren Wirkung.
- Abweichungen, Legal Holds und fehlgeschlagene Läufe werden dokumentiert und
  zeitnah behoben. Als Abweichung gilt auch eine ausgesetzte lokale Rotation
  nach einem fehlgeschlagenen Off-Site-Abgleich (Meldung „Rotation ausgesetzt“
  im Protokoll von `backup.sh`): Bis zum nächsten erfolgreichen Abgleich bleiben
  ältere Sicherungsstände über ihre Frist hinaus bestehen.
- Dieses Konzept wird mindestens jährlich sowie nach Änderungen an Fristen,
  Datenkategorien oder Backup-Verfahren überprüft.
