# Datenschutz-Folgenabschätzung (Art. 35 DSGVO)

**Verantwortlicher:** {{ facility_name }}
**Erstellt:** {{ date }}
**Version:** 1.5 (Softwarestand v0.99.6, 2026-10-07)

---

## 1. Beschreibung der Verarbeitungsvorgänge

### 1.1 Art der Verarbeitung
Die Software „Anlaufstelle" dient der Dokumentation von Kontakten mit begleiteten Personen in niedrigschwelligen sozialen Einrichtungen. Zielgruppe der Einrichtung: [Zielgruppe eintragen, z. B. Jugendliche und junge Erwachsene, Ratsuchende einer Beratungsstelle, Bewohnerinnen eines Frauenhauses, Gäste einer Veranstaltung].

### 1.2 Umfang der Verarbeitung
- Pseudonymisierte Kontaktdokumentation
- Verschlüsselte Speicherung von Dateianhängen und einzelner sensibler Felder (je nach Konfiguration)
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
| Altersgruppe | Statistische Auswertung |
| Kontaktstufe | Anpassung der Löschfristen |
| Kontaktnotizen | Qualität der Betreuung |

### 2.2 Datensparsamkeit
- Keine Klarnamen erforderlich
- Altersangabe nur in Clustern (nicht exakt)
- Sensible Felder je nach Konfiguration verschlüsselt (Dateianhänge stets); die Datenbank selbst ist nicht verschlüsselt

### 2.3 Speicherbegrenzung

Die Anwendung kennt elf konfigurierbare Aufbewahrungsfristen. Alle elf sind unten mit dem für diese Einrichtung **tatsächlich konfigurierten** Wert eingesetzt.

| # | Frist | Wert | Einstellung | Wirkung bei Ablauf |
|---|-------|------|-------------|--------------------|
| 1 | Kontakte anonymer Personen | {{ retention_anonymous_days }} Tage | `retention_anonymous_days` | Ereignis wird soft-gelöscht (Frist ab dem Zeitpunkt des Ereignisses) |
| 2 | Kontakte der Stufe „Nur Pseudonym“ | {{ retention_identified_days }} Tage | `retention_identified_days` | Ereignis wird soft-gelöscht (Frist ab dem Zeitpunkt des jeweiligen Ereignisses; ein neuer Kontakt verlängert ältere Ereignisse nicht) |
| 3 | Ereignisse der Stufe „Mit Zusatzangaben“ | {{ retention_qualified_days }} Tage | `retention_qualified_days` | Frist ab Fallabschluss, ohne Fall ab Ereignisdatum, im offenen Fall ab letzter fachlicher Fallaktivität (ersatzweise Anlage); Vorschlag und im Regelfall Freigabe durch eine Person mit „Löschbestätigung“ vor der Löschung |
| 4 | Höchstfrist (verkürzt nur) einzelner Dokumentationstypen | {{ retention_document_type_overrides }} | `DocumentType.retention_days` | Zusätzliche Frist für Ereignisse dieses Typs (ab Zeitpunkt des Ereignisses): Es gilt die zuerst ablaufende Frist. Eine längere Typ-Frist verlängert die Fristen 1–3 nicht |
| 5 | Aktivitäten / Zeitstrom-Einträge | {{ retention_activities_days }} Tage | `retention_activities_days` | **Hart**-Löschung (kein Papierkorb) |
| 6 | Papierkorb-Frist für gelöschte Personen und Fälle | {{ client_trash_days }} Tage | `client_trash_days` | Person bzw. Fall wird automatisch anonymisiert; bis dahin wiederherstellbar |
| 7 | Audit-Log | {{ auditlog_retention_months }} Monate | `auditlog_retention_months` | Einträge ohne Einrichtung: {{ auditlog_system_retention_months }} Monate installationsweit (0 = aus). Aktiver Legal Hold schützt Audit-Ziele mit Einrichtung. Sicherheitsverstöße, System-Marker, Berichtszeitraum-Nachweise und Nachweise der Historien-Redaktion bleiben dauerhaft |
| 8 | Inhalte früherer Fassungen eines Ereignisses | {{ eventhistory_detail_months }} Monate | `eventhistory_detail_months` | Die Werte der Fassung werden gelöscht (Redaktion), die Fassung selbst (Zeitpunkt, handelnde Person, Feldnamen) bleibt; die jüngste Fassung eines nicht gelöschten Ereignisses behält ihre Werte. Bei gelöschten Ereignissen und bei Ereignissen anonymisierter Personen geschieht dasselbe für alle Fassungen unabhängig von dieser Frist mit dem nächsten Aufbewahrungslauf. Fassungen unter einer Aufbewahrungssperre oder Art. 18 stellt der Lauf zurück. `0` schaltet diese Frist ab |
| 9 | Chronologie früherer Fassungen (nach ihrer Redaktion) | {{ eventhistory_chronicle_months }} Monate | `eventhistory_chronicle_months` | Die redigierte Fassung wird gelöscht, gemessen ab ihrer Redaktion. `0` schaltet die Löschung ab — dann bleibt die Chronologie unbefristet |
| 10 | Übergabenotizen (Schichtnotizen) | {{ retention_handover_notes_days }} Tage | `retention_handover_notes_days` | Die Notiz wird endgültig gelöscht (Frist ab ihrer letzten Änderung); kein Legal Hold — verfahrensrelevanter Inhalt ist vorher als Vermerk am Fall zu dokumentieren |
| 11 | Abgeschlossene Lösch-, Zusammenführungs- und Herabstufungsanträge | {{ deletion_request_retention_days }} Tage | `deletion_request_retention_days` | Harte Löschung ab Abschluss; offene Anträge bleiben |

Ergänzende Regeln:

- **Freigabe im Standardlauf:** Anonym und „Nur Pseudonym“ werden automatisch gelöscht. „Mit Zusatzangaben“ erhält einen Vorschlag; im Regelfall erlaubt erst Freigabe durch eine Person mit „Löschbestätigung“ die Löschung. Hold und Art. 18 stellen zurück. Den Vorschlag legt der Lauf an. Die Einrichtung benennt mindestens ein aktives Konto der eigenen Einrichtung mit Rolle Leitung oder Anwendungsbetreuung und dem Recht „Löschbestätigung“; ohne besetzte Freigabe werden Ereignisse „Mit Zusatzangaben“ im Standardlauf bei ausgeschalteter Auto-Freigabe nicht gelöscht. Das Recht wird rollenunabhängig vergeben, die Aufbewahrungsoberfläche ist jedoch auf Leitung und Anwendungsbetreuung beschränkt. Die Compliance-Karte „Genehmiger“ zählt aktive Löschantragsberechtigte unabhängig von ihrer Rolle; sie belegt nicht, dass ein Konto den Aufbewahrungslauf bedienen kann.
- **Ausnahme der Freigabe:** Abweichend davon kann die Einrichtung die Auto-Freigabe ausdrücklich aktivieren (Opt-in, standardmäßig aus): Nach der konfigurierten maximalen Zahl von Zurückstellungen und Ablauf der Wiedervorlage genehmigt der Lauf den Vorschlag ohne zweite Person. Hold und Art. 18 schützen auch diesen Weg.
- **Wirkung des Soft-Delete:** Mit der Löschung werden die Inhalte des Ereignisses (Zusatzfelder, Suchtext) sofort geleert und seine Dateianhänge gelöscht; die Werte seiner früheren Fassungen löscht regulär der nächste Aufbewahrungslauf. Ereignisse unter einer Aufbewahrungssperre und Ereignisse, deren Datensatz oder Person in diesem Moment von einer anderen Bearbeitung gesperrt ist, überspringt er; ein späterer Lauf holt sie nach, sobald Sperre bzw. Bearbeitung beendet sind. Die Ereigniszeile selbst bleibt als gelöscht markiert bestehen.
- **Löschvorschläge:** Der Standardlauf erzeugt sie für „Mit Zusatzangaben“. Zurückstellen schützt bis zum Datum, Ablehnen bis zu einer neuen Vorlage frühestens nach {{ retention_rejected_retry_days }} Tagen. `--propose` erzeugt zusätzlich Vorschläge für alle Strategien.
- **Anonymisierung der Person:** Nach Löschung aller Ereignisse; ohne Ereignis nur für „Nur Pseudonym“ ohne offene Fälle nach {{ retention_identified_days }} Tagen ab dem jüngsten Zeitpunkt aus Personenänderung, fachlicher Fallaktivität, Aufgabenänderung und Anhangsänderung an Person oder Fall (Anlage, Ersetzung oder Entfernung). Personen „Mit Zusatzangaben“ ohne Ereignis sind von der automatischen Anonymisierung ausgenommen; der Vorschlagsweg unterstützt bisher nur Ereignisse, eine automatische Fristvollstreckung für diese Personen fehlt. Aktiver Hold und Art. 18 schützen.
- **Fälle: eigener Löschweg, aber keine eigene Frist.** Ein Fall lässt sich einzeln löschen — er wandert in einen Papierkorb und wird nach derselben Frist 6 automatisch anonymisiert (Titel, Beschreibung, Zusatzfelder, Episoden, Wirkungsziele, Meilensteine und fall-eigene Dateien werden dabei getilgt, die Fall-Zeile bleibt als anonymes Statistik-Aggregat stehen). Bewusst **keine** zweite, eigene Frist: Sie beantwortete dieselbe Frage wie Frist 6 („wie lange ist eine Löschung noch umkehrbar") und wäre nur eine zweite Gelegenheit, sie falsch zu setzen. Ob die Löschung eine zweite Person braucht, richtet sich nach der Kontaktstufe der zugehörigen Person; zugeordnete Kontakte bleiben in der Personenakte und verlieren erst bei der Anonymisierung ihre Fallzuordnung.
- **Kein eigener Löschhorizont für Episoden, Aufgaben, Wirkungsziele und Meilensteine.** Sie hängen am Lebenszyklus ihres Falls bzw. der Person: ihre personenbeziehbaren Freitexte werden bei deren Anonymisierung mitgetilgt.
- **Aufbewahrungssperren (Legal Hold)** werden von Leitung/Anwendungsbetreuung direkt in Personen-, Fall- und Ereignisakten gesetzt und aufgehoben; Systemadministration kann in der Systemliste aufheben. Person/Fall schützen auch Ereignisse und zugehörige Audit-Zeilen mit Einrichtung. Art. 18 schützt im Lauf zusätzlich die Löschung von Ereignisinhalten, die Anonymisierung von Personen und Fällen sowie die Historien-Redaktion.
- **Einschränkung der Verarbeitung (Art. 18 DSGVO)** ist das Gegenstück und ein eigener Mechanismus: Ein Sperrvermerk an der Person lässt die Daten gespeichert (Art. 18 Abs. 2), sperrt aber ihre inhaltliche Nutzung und Änderung; ausgenommen ist der Berichtigungsworkflow. Setzen und Aufheben werden protokolliert, das Aufheben verlangt eine zweite berechtigte Person oder die Leitung. Die Unterrichtung der betroffenen Person vor dem Aufheben (Art. 18 Abs. 3) ist organisatorisch zu leisten.
- **4-Augen-Prinzip für manuelle Löschungen:** Die Löschung einer Person braucht immer die Bestätigung einer zweiten berechtigten Person; die Löschung eines Ereignisses oder Falls nur, wenn die Person auf der Stufe „Mit Zusatzangaben“ steht. Ereignisse und Fälle anderer Stufen löscht eine berechtigte Person direkt. Dateien vernichten Leitung und Anwendungsbetreuung über den Sofortlöschweg ohne zweite Person (siehe § 2.4).
- **Restrisiko:** Das Audit-Log wird bei Anonymisierung nicht mitredigiert. Einträge ohne Einrichtung folgen der installationsweiten Audit-Frist. Zeilen mit Einrichtung und Ziel-Hold sowie Zeilen bei Frist 0 oder dauerhafter Ausnahme bleiben erhalten. Nachweise herausgegebener Berichtszeiträume enthalten auslösende Person; Empfänger und Zweck sind Freitextfelder mit höchstens 200 Zeichen je Wert. Die Anwendung verwirft E-Mail- und Telefonmuster sowie wahrscheinliche Personennamen durch eine heuristische Prüfung. Vollständige Erkennung personenbezogener Angaben und inhaltliche Richtigkeit sind weiterhin organisatorisch zu prüfen.
- Die Fristen werden durch den täglich laufenden Aufbewahrungs-Job durchgesetzt; ist der Job nicht eingerichtet, greift **keine** der elf Fristen.

### 2.4 Dokumentenablage an Person und Fall

Dateien lassen sich nicht nur an einzelnen Ereignissen hinterlegen, sondern auch unmittelbar an einer **Person** und an einem **Fall** (z. B. Ausweiskopie, Mietvertrag, Bescheid). Datenschutzrechtlich gelten dafür dieselben Zusagen wie für Ereignis-Anhänge, mit einer eigenen Aufbewahrungslogik:

- **Verschlüsselung at rest:** Der Dateiinhalt wird serverseitig verschlüsselt gespeichert (Fernet/MultiFernet, chunk-weise), der Originaldateiname ebenfalls; auf dem Datenträger liegt nur ein zufälliger Speichername. Die Datei durchläuft vor der Verschlüsselung dieselbe Prüfkette wie ein Ereignis-Anhang (Dateityp-Freigabe, Größenbegrenzung, Virenprüfung, Abgleich der Dateisignatur).
- **Aufbewahrung nach dem Lebenszyklus des Datensatzes, an dem die Datei hängt:** Personen- und fallbezogene Dateien haben **keine eigene Zeitfrist**. Sie werden — Datei **und** Datenbankeintrag — mit der Anonymisierung ihres Datensatzes endgültig gelöscht: bei personenbezogenen Dateien mit der Anonymisierung der Person (nach Ablauf der Papierkorb-Frist 6 bzw. sobald alle Ereignisse der Person gelöscht sind), bei fallbezogenen Dateien zusätzlich schon mit der Tilgung **ihres Falls**, wenn dieser einzeln gelöscht wurde. Die Fristen 1–4 wirken ausschließlich auf Ereignisse und damit nur auf ereignisbezogene Anhänge.
- **Entfernen in der Oberfläche ist noch keine Löschung:** Nimmt eine Fachkraft eine Datei aus einem Feld heraus oder ersetzt sie durch eine neuere Fassung, bleibt die bisherige Fassung als frühere Version gespeichert und wird lediglich als entfernt bzw. ersetzt gekennzeichnet. Auch ein Wechsel des Dokumentationstyps verwirft die Dateien nicht, sondern markiert sie nur.
- **Restrisiko:** Aus beidem folgt, dass eine personen- oder fallbezogene Datei bis zur Anonymisierung ihres Datensatzes auf dem Server verschlüsselt vorhanden bleibt — im ungünstigsten Fall also über die Papierkorb-Frist hinweg, obwohl sie in der Oberfläche längst entfernt wurde. Wo das nicht hinnehmbar ist, bleibt die manuelle Sofort-Anonymisierung durch die Anwendungsbetreuung: für die ganze Person oder für einen einzelnen Fall im Fall-Papierkorb; beide löschen die zugehörigen Dateien physisch. Für eine einzelne Datei gibt es zusätzlich den Sofortlöschweg: Leitung und Anwendungsbetreuung vernichten eine Datei samt aller Versionen ersatzlos und ohne zweite Person, anders als bei der Löschung einer Person kein Vier-Augen-Antrag. Eine aktive Aufbewahrungssperre (Legal Hold) und ein Sperrvermerk nach Art. 18 DSGVO an der Person verhindern das; eine Wiederherstellung ist nicht möglich. Die Vernichtung wird im Audit-Log protokolliert.
- **Auskunftsrecht (Art. 15):** Die Datenauskunft weist zu jeder Datei die **Angaben** aus — Feld, Dateiname, Größe, Dateityp, Zeitpunkt und hochladende Person sowie den Stand (aktuell, ersetzt, entfernt) —, nicht jedoch den Dateiinhalt. Die Dateien selbst werden auf Anforderung über den regulären Download herausgegeben.

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
- Kontosperrung nach 10 Fehlversuchen innerhalb von 15 Minuten je Konto und Quell-IP, zusätzlich Ratelimits der Anmeldung (5 Versuche/Minute je IP, 10/Stunde je Benutzername); das Systemadministrationskonto ist von der Sperre ausgenommen; zusätzlich gelten Auth-Alarmierung und Retry-Empfehlung ohne neue Kontosperre (TOM § 1.2)
- Schlüsselrotation ohne Datenverlust (MultiFernet)
- Malware-Schutzschicht: ClamAV scannt sämtliche Datei-Uploads vor der serverseitigen Verschlüsselung; infizierte Dateien werden abgelehnt (fail-closed)
- Defense-in-Depth auf Datenbankebene: PostgreSQL Row Level Security (RLS) bildet eine zweite Verteidigungslinie gegen facility-übergreifende Datenabflüsse. Für Sitzungen der einrichtungsgebundenen Rollen verhindert RLS einen Cross-Tenant-Zugriff auch bei Fehlern im ORM-Layer. Ausnahme: In Sitzungen der Systemadministration setzt die Anwendung `app.is_super_admin`, und die Policies lassen einrichtungsübergreifende Zugriffe zu; SYSTEM_VIEW protokolliert die Aufrufe im Systembereich und die Listen- und Detailansichten für alle registrierten Verwaltungsmodelle einschließlich Löschanträgen, Benutzern, Einstellungen, Audit-Log, Snapshots, Vorlagen, Organisation und Einrichtung; schlägt die Protokollierung fehl, wird die Ausgabe mit HTTP 403 gesperrt. Ereignisse und Ereignis-Historie zeigen im Admin nur Metadaten, keine Feldinhalte.
- Endgeräteschutz durch Offline-Kryptografie: Offline im Browser vorgehaltene Inhalte werden client-seitig mit einem **non-extractable** AES-GCM-256-Schlüssel verschlüsselt. Der Schlüssel wird bei der Anmeldung aus dem Benutzerpasswort abgeleitet (PBKDF2, 600 000 Iterationen, SHA-256) und als `CryptoKey` in einer eigenen IndexedDB (`anlaufstelle-crypto`) gespeichert; er verlässt das Gerät nicht und überdauert Neuladen sowie Tab-, Browser- und Geräteneustart. „Non-extractable“ verhindert den Export der Schlüsselbytes über WebCrypto, nicht aber, dass der gespeicherte Schlüssel im Browser dieses Geräts zum Entschlüsseln benutzt wird: Wer innerhalb der Leerlaufzeit Zugriff auf das entsperrte Gerät und das Browserprofil hat, auch über die Entwicklerwerkzeuge des Browsers, kann die Offline-Inhalte lesen; die Anwendung öffnet in dieser Zeit offline ohne erneute Anmeldung. **Löschwege:** Abmelden löscht Schlüssel und Offline-Speicher einschließlich ungesendeter Arbeit (zusätzlich `Clear-Site-Data` in der Antwort des Servers). Ein Passwortwechsel löscht ebenfalls beides; liegt ungesendete Arbeit vor, fragt die Anwendung vorher nach. Nach Ablauf der Leerlaufzeit (Sitzungsdauer, Default 1800 s, einrichtungskonfigurierbar) greift die Leerlaufprüfung — aber nur, solange oder sobald eine Seite der Anwendung im Browser läuft (beim Laden, im 60-Sekunden-Intervall, bei Rückkehr in den Tab, tab-übergreifend): Liegt ungesendete Arbeit vor, wird nur der Schlüssel gelöscht (Sperre); der verschlüsselte Bestand bleibt erhalten und wird nach erneuter Anmeldung nur wieder lesbar, wenn Passwort und Offline-Salt des Kontos unverändert sind. Den Salt erneuert der Server bei Rollenwechsel, bei Deaktivierung (auch eine spätere Reaktivierung stellt den alten nicht wieder her) und bei einer Anmeldung mit korrektem Passwort an einem gesperrten Konto; eine Passwortänderung (auch das Zurücksetzen) ändert die Schlüsselableitung ohnehin. Danach lässt sich der alte Bestand nicht mehr entschlüsseln: zwischengespeicherte Daten verwirft die Anwendung, ungesendete Einträge zeigt sie als nicht zustellbar an. Ohne ungesendete Arbeit werden Schlüssel und Offline-Speicher gelöscht. Schließen des Tabs oder Browsers und ein Geräteneustart sind **kein** Löschereignis — bewusst, damit das Wiederöffnen im aufsuchenden Einsatz funktioniert (akzeptiertes Restrisiko F-01). Ein Gerät, das nach Ablauf der Leerlaufzeit nicht wieder geöffnet wird, behält Schlüssel und verschlüsselten Bestand; auch abgelaufene Offline-Bestände (48 Stunden) entfernt die Anwendung erst beim nächsten Öffnen mit geladenem Schlüssel. **TOM-relevant:** Die für die Offline-Ansicht nötigen Inhalte einschließlich Art.-9-Daten werden serverseitig entschlüsselt und als vorgefiltertes Bundle in den Browser geliefert (dort erst wieder client-seitig verschlüsselt); die serverseitigen Sichtbarkeits- und Rechte-Filter bleiben autoritativ. **Benanntes Restrisiko:** Verschlüsselt sind die Inhalte. Unverschlüsselt daneben liegen Ordnungsmerkmale: Datensatz- und Personen-IDs, Bearbeitungs- und Abgleichstatus, Zeitstempel (letzter Abgleich, Eingang in die Sende-Warteschlange, Entwurfsstand) und die Ziel-Adressen der Sende-Warteschlange (bewusster Verzicht). DSFA-/TOM-Einordnung und Restrisiken: ADR-022 § Akzeptierte Restrisiken
- **Offline-Schlüssel und Widerrufsprüfung**. Der aus Passwort und Server-Salt abgeleitete non-extractable `CryptoKey` bleibt in der Browser-IndexedDB gespeichert und übersteht Tab-, Browser- und Geräteneustart. „Non-extractable“ verhindert den Export über WebCrypto; der Schutz des gespeicherten Schlüssels gegen Zugriff auf das Browserprofil hängt an **Geräteverschlüsselung und Bildschirmsperre**, die für Offline-Geräte verpflichtend sind. Beim Ableiten wird eine absolute Frist eingefroren: `session_absolute_hours` der Einrichtung (Vorgabe 12 Stunden). Aktivität und Navigation verlängern sie nicht. Die Anwendung prüft beim Laden, vor Entschlüsselung/Speicherung/Replay, bei Tab-Rückkehr und im 60-Sekunden-Intervall. Am Ablauf entzieht sie den Schlüssel und zeigt „Offline-Schlüssel abgelaufen — bitte online anmelden“; ungesendete Arbeit bleibt verschlüsselt erhalten und wird nach erneutem Online-Login mit unverändertem Passwort und Salt wieder lesbar. Das Leerlauffenster gilt zusätzlich. Fehlende oder ungültige Ablaufdaten sperren den Zugriff; Bundles ohne gültiges `expiresAt` gelten als abgelaufen. Ein beobachteter Rücksprung der Geräteuhr sperrt den Schlüssel; eine manipulierbare Geräteuhr bleibt dennoch ein Restrisiko. Bei ausgeschalteter Anwendung erfolgt kein Schlüsselentzug per Fernlöschung.
- Meldet die Widerrufsliste einen HTTP-/Netz-/Verarbeitungsfehler (auch 500/429) oder liefert sie eine ungültige Antwortstruktur, bleiben Queue- und Edit-Replay ausgesetzt: „Synchronisation wartet auf Widerrufsprüfung“. Der Sperrzustand wird in der lokalen IndexedDB gespeichert. Zusätzlich braucht jeder Replay-Lauf eine eigene frische, strukturell gültige, erfolgreich angewandte und persistierte Widerrufsprüfung; eine frühere Freigabe erlaubt keinen späteren Lauf oder anderen Tab, auch wenn Metadatenschreiben und Ersatzlöschung gleichzeitig scheitern. Fehlende oder unlesbare Metadaten sperren den Replay; auch nach einem Lesefehler ist eine neue erfolgreiche Prüfung nötig. Der nächste Sync-Lauf prüft erneut, ebenso ein direktes Speichern aus der Offline-Akte. Bei fehlgeschlagener Prüfung bleibt ungesendete Arbeit verschlüsselt erhalten. Die bestehenden Idempotenz-Schlüssel bleiben erhalten, und die Server-Gates (422 für widerrufene Personen, 409 für Statuskonflikte) bleiben verbindlich. Geräteverschlüsselung ersetzt diese Server-Prüfungen nicht.
- Zwei-Faktor-Authentifizierung: TOTP-basierte 2FA (ergänzt um Passkeys und Backup-Codes) ist in Produktion per Rollen-Default für alle Rollen verpflichtend — für Systemadministration und Anwendungsbetreuung sofort, für Leitung, Fachkraft und Assistenz nach einer Übergangsfrist von 30 Tagen ab der ersten Anmeldung. Pro Benutzer oder einrichtungsweit lässt sich die Pflicht zusätzlich sofort erzwingen; der Betrieb kann die Rollenlisten anpassen (`MFA_ENFORCED_ROLES_IMMEDIATE`, `MFA_ENFORCED_ROLES_GRACE`). Senkt das Risiko bei kompromittierten Passwörtern erheblich

### 4.2 Organisatorische Maßnahmen
- Rollenbasierte Zugriffskontrolle mit fünf Rollen (Systemadministration, Anwendungsbetreuung, Leitung, Fachkraft, Assistenz); was eine Rolle sieht, richtet sich nach der Sensitivität von Dokumentationstyp und Feld, nicht nach der Kontaktstufe
- 4-Augen-Prinzip für manuelle Löschungen von Personen sowie von Ereignissen und Fällen der Stufe „Mit Zusatzangaben“; Dateien vernichten Leitung und Anwendungsbetreuung ohne zweite Person
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
