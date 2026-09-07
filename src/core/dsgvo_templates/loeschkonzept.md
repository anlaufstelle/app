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
| Kontakte anonymer Personen | {{ retention_anonymous_days }} Tage | `retention_anonymous_days` | Ereignis wird soft-gelöscht |
| Kontakte der Stufe „Nur Pseudonym" | {{ retention_identified_days }} Tage | `retention_identified_days` | Ereignis wird soft-gelöscht |
| Fälle der Stufe „Mit Zusatzangaben" nach Fallabschluss | {{ retention_qualified_days }} Tage | `retention_qualified_days` | Löschvorschlag; Löschung erst nach Prüfung |
| Abweichende Frist einzelner Dokumentationstypen | {{ retention_document_type_overrides }} | `DocumentType.retention_days` | Überschreibt für Ereignisse dieses Typs die kontaktstufenbezogene Frist |
| Aktivitäten / Zeitstrom-Einträge | {{ retention_activities_days }} Tage | `retention_activities_days` | Harte Löschung |
| Papierkorb für gelöschte Personen und Fälle | {{ client_trash_days }} Tage | `client_trash_days` | Automatische Anonymisierung; bis dahin Wiederherstellung möglich |
| Audit-Log | {{ auditlog_retention_months }} Monate | `auditlog_retention_months` | Rollierende Löschung; `0` deaktiviert sie, gesetzliche und technische Ausnahmen siehe § 3.4 |
| Inhalte früherer Fassungen eines Ereignisses | {{ eventhistory_detail_months }} Monate | `eventhistory_detail_months` | Die Werte der Fassung werden gelöscht, die Fassung selbst (Zeitpunkt, handelnde Person, Feldnamen) bleibt; `0` deaktiviert diese Frist. Bei einem **gelöschten** Ereignis geschieht dasselbe unabhängig von dieser Frist, mit dem nächsten Aufbewahrungslauf |
| Chronologie früherer Fassungen (nach ihrer Inhalts-Löschung) | {{ eventhistory_chronicle_months }} Monate | `eventhistory_chronicle_months` | Die inhaltsleere Fassung wird gelöscht; gemessen ab dem Zeitpunkt ihrer Inhalts-Löschung. `0` deaktiviert die Löschung — dann wird die Chronologie unbefristet aufbewahrt |
| Backup-Sicherungsstände | {{ backup_retention_days }} Tage | Längste Rotation im ausgelieferten `backup.sh` | Verschlüsseltes Archiv wird durch die Backup-Rotation entfernt |

Die Werte stammen beim Erzeugen dieses Dokuments aus den Einstellungen der
Einrichtung. Die Backup-Frist entspricht der längsten Rotation des
ausgelieferten `backup.sh`; der Wert dokumentiert die Rotation, er steuert das
Skript nicht. Weicht die betriebliche Rotation davon ab, muss die Einrichtung
den Wert im freigegebenen Löschkonzept organisatorisch anpassen.

## 3. Technische Löschregeln

### 3.1 Ereignisse, Personen und Fälle

- Der tägliche Aufbewahrungslauf setzt abgelaufene Ereignisse gemäß den
  Kontaktstufen- und Dokumentationstyp-Fristen auf gelöscht.
- Sind alle Ereignisse einer Person gelöscht, anonymisiert ein nachfolgender
  Lauf ihre identifizierenden Inhalte. Für manuell gelöschte Personen und
  Fälle gilt zuvor die oben ausgewiesene Papierkorb-Frist.
- Fälle haben keine zusätzliche eigene Aufbewahrungsfrist. Episoden,
  Wirkungsziele, Meilensteine und Aufgaben folgen dem Lebenszyklus ihres Falls
  beziehungsweise ihrer Person.
- Aktive Aufbewahrungssperren (Legal Hold) halten automatische Löschung und
  Anonymisierung bis zu ihrer Aufhebung an. Rechtsgrundlage ist Art. 17 Abs. 3
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

Die betroffenen Inhalte verschwinden deshalb spätestens mit der planmäßigen
Rotation des letzten sie enthaltenden Sicherungsstands. Backups dürfen nur zur
Wiederherstellung verwendet werden, nicht als Facharchiv. Nach einem Restore
sind die seit Sicherungszeitpunkt fälligen Aufbewahrungs- und Löschläufe vor
der erneuten Freigabe des Systems auszuführen.

## 4. Zuständigkeiten und Nachweis

- Die Anwendungsbetreuung prüft die einrichtungsbezogenen Fristen und offene
  Löschvorschläge.
- Die Systemadministration überwacht Aufbewahrungslauf, Backup-Rotation und
  Restore-Tests sowie die sichere Vernichtung abgelaufener Sicherungsstände.
- Abweichungen, Legal Holds und fehlgeschlagene Läufe werden dokumentiert und
  zeitnah behoben.
- Dieses Konzept wird mindestens jährlich sowie nach Änderungen an Fristen,
  Datenkategorien oder Backup-Verfahren überprüft.
