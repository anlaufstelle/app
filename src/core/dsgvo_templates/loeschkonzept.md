# Löschkonzept (Art. 5 Abs. 1 lit. e und Art. 17 DSGVO)

**Verantwortlicher:** {{ facility_name }}
**Erstellt:** {{ date }}
**Version:** 1.1 (Softwarestand v0.99.6, 2026-10-07)

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
| Ereignisse der Stufe „Mit Zusatzangaben" in abgeschlossenen Fällen | {{ retention_qualified_days }} Tage | `retention_qualified_days` | Ereignis wird soft-gelöscht (Frist ab Abschluss des verknüpften Falls). Ereignisse ohne Fall oder in offenen Fällen verfallen nicht über diese Frist, sondern nur über eine Dokumentationstyp-Frist |
| Abweichende Frist einzelner Dokumentationstypen | {{ retention_document_type_overrides }} | `DocumentType.retention_days` | Zusätzliche Frist für Ereignisse dieses Typs (ab Zeitpunkt des Ereignisses): Es gilt die zuerst ablaufende Frist. Eine längere Typ-Frist verlängert die kontaktstufenbezogene Frist nicht |
| Aktivitäten / Zeitstrom-Einträge | {{ retention_activities_days }} Tage | `retention_activities_days` | Harte Löschung |
| Papierkorb für gelöschte Personen und Fälle | {{ client_trash_days }} Tage | `client_trash_days` | Automatische Anonymisierung; bis dahin Wiederherstellung möglich |
| Audit-Log | {{ auditlog_retention_months }} Monate | `auditlog_retention_months` | Rollierende Löschung; `0` deaktiviert sie, gesetzliche und technische Ausnahmen siehe § 3.3 |
| Inhalte früherer Fassungen eines Ereignisses | {{ eventhistory_detail_months }} Monate | `eventhistory_detail_months` | Die Werte der Fassung werden gelöscht, die Fassung selbst (Zeitpunkt, handelnde Person, Feldnamen) bleibt; die jüngste Fassung eines nicht gelöschten Ereignisses behält ihre Werte. `0` deaktiviert diese Frist. Bei einem **gelöschten** Ereignis und bei Ereignissen einer anonymisierten Person geschieht dasselbe für alle Fassungen unabhängig von dieser Frist, regulär mit dem nächsten Aufbewahrungslauf (Ausnahmen § 3.1) |
| Chronologie früherer Fassungen (nach ihrer Inhalts-Löschung) | {{ eventhistory_chronicle_months }} Monate | `eventhistory_chronicle_months` | Die inhaltsleere Fassung wird gelöscht; gemessen ab dem Zeitpunkt ihrer Inhalts-Löschung. `0` deaktiviert die Löschung — dann wird die Chronologie unbefristet aufbewahrt |
| Übergabenotizen (Schichtnotizen) | {{ retention_handover_notes_days }} Tage | `retention_handover_notes_days` | Die Notiz wird endgültig gelöscht (Frist ab ihrer letzten Änderung); kein Legal Hold — verfahrensrelevanter Inhalt ist vorher als Vermerk am Fall zu dokumentieren |
| Monatliche Statistik-Aggregate | unbefristet | — | Bleiben bestehen: Der Aufbewahrungslauf rechnet die Zählwerte jedes gelöschten Ereignisses zusammen mit dessen Löschung dem Aggregat seines Monats hinzu, auch im laufenden Monat; spätere Neuberechnungen behalten sie (siehe § 3.5) |
| Backup-Sicherungsstände | {{ backup_retention_days }} Tage (Altersgrenze, keine Höchstfrist) | Längste lokale Rotation im ausgelieferten `backup.sh` | Die Rotation entfernt ein lokales verschlüsseltes Archiv beim ersten erfolgreichen Lauf von `backup.sh`, in dem es mindestens 8 (täglich), 29 (wöchentlich) bzw. 91 volle Tage (monatlich) alt ist; keine feste Höchstfrist. Die Rotation ist ausgesetzt, solange der Off-Site-Abgleich fehlschlägt |
| Off-Site-Kopien der Sicherungsstände | [eintragen] | Aufbewahrungsregel am Off-Site-Ziel (von der Einrichtung festzulegen) | `backup.sh` löscht am Off-Site-Ziel nichts; Kopien bleiben bis zur Löschung durch die Regel am Ziel bestehen |

Die Werte stammen beim Erzeugen dieses Dokuments aus den Einstellungen der
Einrichtung. Der Backup-Wert ist die Altersgrenze der längsten lokalen
Rotation des ausgelieferten `backup.sh` (`find -mtime +90`); er dokumentiert
die Rotation, er steuert das Skript nicht. Er ist keine Höchstfrist: `find`
zählt nur volle Tage, und gelöscht wird erst beim nächsten erfolgreichen Lauf
von `backup.sh`, nachdem ein Sicherungsstand die Grenze überschritten hat.
Weicht die betriebliche Rotation davon ab, muss die Einrichtung den Wert im
freigegebenen Löschkonzept organisatorisch anpassen. Die Frist der
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
  gelöscht markiert bestehen; die Werte früherer Fassungen löscht regulär der
  nächste Aufbewahrungslauf. Ereignisse unter einem aktiven Legal Hold und
  Ereignisse, deren Datensatz oder Person in diesem Moment von einer anderen
  Bearbeitung gesperrt ist, überspringt er; ein späterer Lauf holt sie nach,
  sobald Sperre bzw. Bearbeitung beendet sind.
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
  Dateien vernichten Leitung und Anwendungsbetreuung über den Sofortlöschweg
  ohne zweite Person (§ 3.2).
- Sind alle Ereignisse einer Person gelöscht, anonymisiert ein nachfolgender
  Lauf ihre identifizierenden Inhalte, sofern kein aktiver Legal Hold auf der
  Person, einem ihrer Fälle oder Ereignisse liegt. Eine Person, zu der nie ein
  Ereignis dokumentiert wurde, erfasst dieser Weg nicht. Er wartet nicht auf
  die Papierkorb-Frist: Auch eine Person im Papierkorb, deren Ereignisse alle
  gelöscht sind, anonymisiert der nächste Lauf. Andere manuell gelöschte
  Personen sowie Fälle im Papierkorb anonymisiert der Lauf nach Ablauf der oben
  ausgewiesenen Papierkorb-Frist.
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
Datensatzes. Daneben gibt es einen Sofortlöschweg: Leitung und
Anwendungsbetreuung beseitigen eine Datei samt Versionen ersatzlos und ohne
zweite Person — anders als die Löschung einer Person, die dem
Vier-Augen-Prinzip folgt. Gesperrt ist der Weg, solange ein aktiver Legal Hold
auf dem Datensatz liegt oder für die Person ein Sperrvermerk nach Art. 18
DSGVO besteht. Die Vernichtung wird im Audit-Log protokolliert und lässt sich
nicht rückgängig machen.

Bereits beim Hochladen greift die Datenminimierung nach Art. 5 Abs. 1 lit. c
DSGVO: Bilddateien werden vor der Verschlüsselung neu kodiert und verlieren
dabei ihre eingebetteten Metadaten — insbesondere den GPS-Aufnahmeort, die
Gerätekennung, den Aufnahmezeitpunkt und das eingebettete Vorschaubild. Diese
Angaben entstehen dadurch gar nicht erst als aufzubewahrender Bestand. Für
Dokumentformate (PDF, Office) gilt das nicht; deren Dokumenteigenschaften
können Angaben über die bearbeitende Beschäftigte enthalten und sind
organisatorisch zu behandeln.

### 3.3 Audit-Log

Das Audit-Log wird rollierend nach `auditlog_retention_months` bereinigt; der
Aufbewahrungslauf bereinigt dabei je Einrichtung nur deren eigene Einträge. Der
Wert `0` schaltet die reguläre Löschung ab. Von der regulären Frist ausgenommen
sind:

- Sicherheitsverstöße, System-Marker des Aufbewahrungslaufs (Laufabschluss und
  Kontrollpunkte der Audit-Bereinigung), Nachweise der Redaktion der
  Ereignis-Historie und Nachweise herausgegebener Berichtszeiträume;
- alle Einträge ohne Einrichtung: Aufrufe und Aktionen der
  Systemadministration (mit Benutzer und IP-Adresse), Anmelde- und
  Sicherheitsereignisse von Konten ohne Einrichtung, fehlgeschlagene
  Anmeldungen unter unbekanntem Benutzernamen (mit dem eingegebenen
  Benutzernamen und der IP-Adresse) sowie installationsweite Lauf-Marker. Kein
  Lauf löscht sie; sie bleiben unbefristet erhalten.

Aufbewahrungssperren (Legal Hold) wirken nicht auf das Audit-Log, auch nicht
auf Einträge zu einer gesperrten Person, einem gesperrten Fall oder Ereignis. Soll ein Audit-Eintrag länger
erhalten bleiben, ist er vor Ablauf der Frist organisatorisch zu sichern (etwa
über den Audit-Export der Systemadministration) oder die Frist anzuheben.
Diese Ausnahmen sind bei der Festlegung und Prüfung des Löschkonzepts
gesondert zu bewerten.

### 3.4 Sicherungsstände

Anwendungsseitige Löschung entfernt Daten aus dem aktiven System, ändert aber
keine bereits erzeugten, verschlüsselten und per HMAC integritätsgesicherten
Backup-Archive. Es gilt: **„unverzüglich ≠ Zerstörung integritätsgesicherter Sicherungsstände"**. Eine nachträgliche Manipulation einzelner Inhalte würde
die Wiederherstellbarkeit und den Integritätsnachweis der Sicherung aufheben.

Die betroffenen Inhalte verschwinden aus den lokalen Sicherungsständen mit
deren Rotation. Eine feste Höchstfrist ergibt sich daraus nicht: `backup.sh`
entfernt einen Sicherungsstand erst bei seinem nächsten erfolgreichen Lauf,
nachdem der Stand seine Altersgrenze überschritten hat — monatliche Stände
also frühestens, wenn sie 91 volle Tage alt sind. Wann das geschieht, hängt
davon ab, wann `backup.sh` läuft. Schlägt der Off-Site-Abgleich fehl, setzt
`backup.sh` die lokale Rotation aus, bis ein Abgleich wieder gelingt.
Off-Site-Kopien erreicht diese Rotation nicht — `backup.sh` löscht am
Off-Site-Ziel nichts. Dort verschwinden die Inhalte erst durch eine eigene
Aufbewahrungsregel am Ziel (etwa eine Lebenszyklus-Regel des Speicherdienstes),
deren Frist die Einrichtung in § 2 einträgt.

In der empfohlenen Zeitplanung (Sicherung 02:00, Aufbewahrungslauf 03:00)
läuft der Aufbewahrungslauf nach der nächtlichen Sicherung. Eine Sicherung
enthält deshalb auch die Daten, die der Lauf kurz danach löscht; sie bleiben bis
zur Rotation dieses Sicherungsstands erhalten. Backups dürfen nur zur
Wiederherstellung verwendet werden, nicht als Facharchiv. Nach einem Restore
sind die seit Sicherungszeitpunkt fälligen Aufbewahrungs- und Löschläufe vor
der erneuten Freigabe des Systems auszuführen.

### 3.5 Statistik-Aggregate

Der Aufbewahrungslauf löscht jedes Ereignis in einer eigenen Transaktion und
rechnet in derselben Transaktion dessen Zählwerte dem monatlichen
Statistik-Aggregat des Monats hinzu, in dem das Ereignis stattfand — auch dem
laufenden Monat, etwa wenn eine kurze Dokumentationstyp-Frist Ereignisse noch
im selben Monat löscht. Löschung und Übertrag werden nur gemeinsam
gespeichert: Bricht der Lauf ab, fehlt kein bereits gelöschtes Ereignis im
Aggregat, und überschneiden sich zwei Läufe, zählt jedes Ereignis genau
einmal. Kann die Anwendung ein Ereignis nicht in das Aggregat übernehmen
(etwa weil ein Berichtsmodul einen Fehler meldet), löscht der Lauf dieses
Ereignis nicht, protokolliert den Fehler im Anwendungs-Log und versucht es
beim nächsten Lauf erneut; die Löschung verzögert sich dann, bis der Fehler
behoben ist.

Die Zählwerte eines bestehenden Aggregats ändert ein Löschlauf nicht; er
erweitert nur den Übertrag. Jede Neuberechnung des Aggregats (der monatliche
Statistiklauf) zählt die noch vorhandenen Ereignisse und addiert die Zählwerte
aller vom Aufbewahrungslauf gelöschten Ereignisse dieses Monats. Personenzahlen
werden dabei nicht addiert, sondern als Untergrenze geführt. Die Aggregate sind
dennoch keine unveränderliche Archivkopie: Nachgetragene Ereignisse kommen bei
der nächsten Neuberechnung hinzu, manuell gelöschte Ereignisse fallen heraus,
und noch vorhandene Ereignisse werden nach dem aktuellen Stand der Person einer
Alters- oder Kontaktstufe zugeordnet.

Die Aggregate enthalten Zählwerte, keine Einzeldatensätze, und haben keine
Frist; sie bleiben nach der Löschung der Ereignisse bestehen. Gespeichert
werden die ungekürzten Zählwerte; erst in Berichten und Exporten werden Werte
unterhalb der k-Anonymitäts-Schwelle der Einrichtung unterdrückt. Die
Aggregate entfallen erst mit der Einrichtung selbst.

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
  ältere Sicherungsstände über ihre Altersgrenze hinaus bestehen.
- Dieses Konzept wird mindestens jährlich sowie nach Änderungen an Fristen,
  Datenkategorien oder Backup-Verfahren überprüft.
