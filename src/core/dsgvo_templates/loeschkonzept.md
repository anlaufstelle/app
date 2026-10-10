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
| Ereignisse der Stufe „Mit Zusatzangaben" | {{ retention_qualified_days }} Tage | `retention_qualified_days` | Frist ab Fallabschluss, ohne Fall ab Ereignisdatum, im offenen Fall ab letzter fachlicher Fallaktivität (ersatzweise Anlage); Vorschlag und Freigabe durch eine andere Person mit „Löschbestätigung“ vor der Löschung |
| Höchstfrist (verkürzt nur) einzelner Dokumentationstypen | {{ retention_document_type_overrides }} | `DocumentType.retention_days` | Zusätzliche Frist für Ereignisse dieses Typs (ab Zeitpunkt des Ereignisses): Es gilt die zuerst ablaufende Frist. Eine längere Typ-Frist verlängert die kontaktstufenbezogene Frist nicht |
| Aktivitäten / Zeitstrom-Einträge | {{ retention_activities_days }} Tage | `retention_activities_days` | Harte Löschung |
| Papierkorb für gelöschte Personen und Fälle | {{ client_trash_days }} Tage | `client_trash_days` | Automatische Anonymisierung; bis dahin Wiederherstellung möglich |
| Audit-Log | {{ auditlog_retention_months }} Monate | `auditlog_retention_months` | Rollierende Löschung; Einträge ohne Einrichtung: {{ auditlog_system_retention_months }} Monate installationsweit (0 = aus). Zeilen mit Einrichtung zu einem Ziel unter aktivem Legal Hold bleiben erhalten; Ausnahmen siehe § 3.3 |
| Inhalte früherer Fassungen eines Ereignisses | {{ eventhistory_detail_months }} Monate | `eventhistory_detail_months` | Die Werte der Fassung werden gelöscht, die Fassung selbst (Zeitpunkt, handelnde Person, Feldnamen) bleibt; die jüngste Fassung eines nicht gelöschten Ereignisses behält ihre Werte. `0` deaktiviert diese Frist. Bei einem **gelöschten** Ereignis und bei Ereignissen einer anonymisierten Person geschieht dasselbe für alle Fassungen unabhängig von dieser Frist, regulär mit dem nächsten Aufbewahrungslauf (Ausnahmen § 3.1) |
| Chronologie früherer Fassungen (nach ihrer Inhalts-Löschung) | {{ eventhistory_chronicle_months }} Monate | `eventhistory_chronicle_months` | Die inhaltsleere Fassung wird gelöscht; gemessen ab dem Zeitpunkt ihrer Inhalts-Löschung. `0` deaktiviert die Löschung — dann wird die Chronologie unbefristet aufbewahrt |
| Übergabenotizen (Schichtnotizen) | {{ retention_handover_notes_days }} Tage | `retention_handover_notes_days` | Die Notiz wird endgültig gelöscht (Frist ab ihrer letzten Änderung); kein Legal Hold — verfahrensrelevanter Inhalt ist vorher als Vermerk am Fall zu dokumentieren |
| Abgeschlossene Lösch-, Zusammenführungs- und Herabstufungsanträge | {{ deletion_request_retention_days }} Tage ab Abschluss | `deletion_request_retention_days` | Harte Löschung; offene Anträge bleiben, Audit folgt seiner eigenen Frist |
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

- Der tägliche Standardlauf (`enforce_retention`) löscht abgelaufene anonyme
  Ereignisse und Ereignisse der Stufe „Nur Pseudonym“ automatisch. Für „Mit
  Zusatzangaben“ legt er Vorschläge an; gelöscht wird erst nach Freigabe durch
  eine andere Person mit dem Recht „Löschbestätigung“. Auch eine Typ-Höchstfrist
  umgeht diesen Freigabeschritt nicht.
- Zurückstellen schützt bis zum gesetzten Datum. Ablehnen löscht nichts;
  frühestens nach {{ retention_rejected_retry_days }} Tagen kann ein neuer
  Vorschlag entstehen. Auto-Freigabe nach maximalen Zurückstellungen ist
  standardmäßig ausgeschaltet und muss ausdrücklich aktiviert werden.
  `--propose` bleibt ein zusätzlicher Vorschlagslauf für alle Strategien.
- Ereignisinhalte und Suchtext werden bei der Löschung sofort geleert, Dateien
  endgültig gelöscht. Die Ereignishülle bleibt; Ereignisse haben keinen
  Wiederherstellungsweg. Frühere Historienfassungen redigiert regulär der
  nächste Aufbewahrungslauf. Fassungen unter Hold oder Art. 18 sowie
  Ereignisse, die von einer anderen Bearbeitung gesperrt sind, werden
  zurückgestellt; ein späterer Lauf holt sie nach. Der Personenstatus zählt
  noch offene Historienfassungen.
- Manuelle Löschungen folgen dem Vier-Augen-Prinzip: Personen immer, Ereignisse
  und Fälle bei „Mit Zusatzangaben“. Die Herabstufung auf „Nur Pseudonym“ braucht
  bei vorhandenen Ereignissen ebenfalls Antrag und zweite berechtigte Person.
  Datei-Sofortlöschung bleibt eine Einzelentscheidung (§ 3.2).
- Sind alle Ereignisse einer Person gelöscht, anonymisiert derselbe Lauf die
  Person; er wartet nicht auf die Papierkorb-Frist. Ohne Ereignis gilt {{ retention_identified_days }} Tage Inaktivität
  ab `updated_at`. Personen unter Hold oder Art.-18-Sperrvermerk werden
  zurückgestellt. Personen und Fälle im Papierkorb haben zusätzlich die
  Papierkorb-Frist; Episoden, Ziele, Meilensteine und Aufgaben folgen ihrer Akte.
- Leitung und Anwendungsbetreuung setzen und lösen Legal Holds direkt in
  Personen-, Fall- und Ereignisakten mit Grund und optionalem Ablaufdatum.
  Die Systemadministration kann in `/system/legal-holds/` aufheben. Eine
  Sperre auf Person/Fall schützt auch zugehörige Ereignisse. Der Art.-18-
  Sperrvermerk schützt im Lauf vor Löschung, Anonymisierung und Historien-
  Redaktion. Beide Schutzarten werden unabhängig voneinander geprüft.

#### Soll-Fristenmatrix

| Datenart | Fristbeginn | Maßgebliche Frist | Freigabe | Hold / Art. 18 | Vernichtung |
|---|---|---|---|---|---|
| Anonymes Ereignis | Ereignisdatum | min(Stufenfrist, Typ-Höchstfrist) | nein | ja / bei Personenbezug | Inhalt und Dateien weg, Hülle bleibt |
| Ereignis „Nur Pseudonym“ | Ereignisdatum | min(Stufenfrist, Typ-Höchstfrist) | nein | ja / ja | wie oben |
| „Mit Zusatzangaben“, geschlossen | Fallabschluss | min(Stichtag der Stufenfrist, Typ-Stichtag ab Ereignisdatum) | ja | ja / ja | nach Freigabe im Lauf |
| „Mit Zusatzangaben“, ohne Fall | Ereignisdatum | min(Stufenfrist, Typ-Höchstfrist) | ja | ja / ja | nach Freigabe |
| „Mit Zusatzangaben“, offen | letzte fachliche Fallaktivität, Fallback Anlage | frühester Stufen-/Typ-Stichtag | ja | ja / ja | nach Freigabe |
| Person mit Ereignissen | letztes Ereignis gelöscht | abgeleitet | nein | ja / ja | anonymisiert |
| Person ohne Ereignis | letzte Änderung | Frist „Nur Pseudonym“ | nein | ja / ja | anonymisiert |
| Audit mit Einrichtung | Zeitstempel | Audit-Frist | nein | ja, Ziel / keine eigene Art.-18-Prüfung | hart gelöscht, Ausnahmen § 3.3 |
| Audit ohne Einrichtung | Zeitstempel | installationsweite Frist | nein | kein Fachziel / keine eigene Art.-18-Prüfung | hart gelöscht, Ausnahmen § 3.3 |
| Abgeschlossener Antrag | Abschluss | Antragsfrist | nein | keine eigene Prüfung | hart gelöscht, Audit bleibt seiner Frist unterworfen |
| Monatsaggregate | — | unbefristet | — | — | bleiben als statistischer Restbestand |

Als Fallaktivität zählen fachliche Änderungen an Fall, Ereignissen, Aufgaben
und Verlauf; Umschlüsselung zählt nicht. Das Dashboard meldet offene Fälle
nach {{ retention_open_case_inactive_days }} Tagen Ruhe.

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

Einrichtungsbezogene Zeilen verfallen nach `auditlog_retention_months`.
Einträge ohne Einrichtung verfallen nach {{ auditlog_system_retention_months }} Monate installationsweit
(`AUDITLOG_SYSTEM_RETENTION_MONTHS`, Vorgabe 24). Jeweils deaktiviert `0` die
Bereinigung. Ein auf eine Einrichtung eingeschränkter Lauf bereinigt die
installationsweite Kette nicht.

Aufbewahrungssperren (Legal Hold) schützen Audit-Zeilen mit Einrichtung zu gesperrten Personen,
Fällen und Ereignissen einschließlich ihrer geschützten Zuordnungen. Die
HMAC-Checkpoints dokumentieren auch die Kettengrenzen gehaltener Überlebender;
Löschung und Checkpoint werden gemeinsam gespeichert.

Dauerhaft ausgenommen sind Sicherheitsverstöße, System-Marker des Aufbewahrungslaufs,
Kontrollpunkte der Audit-Bereinigung, Nachweise der Redaktion der Ereignis-Historie
und Nachweise herausgegebener Berichtszeiträume. Diese Ausnahmen gelten auch für
die Kette ohne Einrichtung. Audit wird bei Anonymisierung nicht mitredigiert;
bei abgeschalteter Frist, aktivem Hold oder Ausnahmen kann es unbefristet bleiben.

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

Der Aufbewahrungslauf und manuelle Ereignislöschungen sichern Löschung und
Monatsübertrag in derselben Transaktion und
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
aller automatisch oder manuell gelöschten Ereignisse dieses Monats. Personenzahlen
werden dabei nicht addiert, sondern als Untergrenze geführt. Die Aggregate sind
dennoch keine unveränderliche Archivkopie: Nachgetragene Ereignisse kommen bei
der nächsten Neuberechnung hinzu, gelöschte Ereignisse bleiben über ihren Monatsübertrag enthalten,
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
