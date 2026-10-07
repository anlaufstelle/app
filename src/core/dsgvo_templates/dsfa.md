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

Die Anwendung kennt zehn konfigurierbare Aufbewahrungsfristen. Alle zehn sind unten mit dem für diese Einrichtung **tatsächlich konfigurierten** Wert eingesetzt.

| # | Frist | Wert | Einstellung | Wirkung bei Ablauf |
|---|-------|------|-------------|--------------------|
| 1 | Kontakte anonymer Personen | {{ retention_anonymous_days }} Tage | `retention_anonymous_days` | Ereignis wird soft-gelöscht (Frist ab dem Zeitpunkt des Ereignisses) |
| 2 | Kontakte der Stufe „Nur Pseudonym“ | {{ retention_identified_days }} Tage | `retention_identified_days` | Ereignis wird soft-gelöscht (Frist ab dem Zeitpunkt des jeweiligen Ereignisses; ein neuer Kontakt verlängert ältere Ereignisse nicht) |
| 3 | Ereignisse der Stufe „Mit Zusatzangaben“ in abgeschlossenen Fällen | {{ retention_qualified_days }} Tage | `retention_qualified_days` | Ereignis wird soft-gelöscht (Frist ab Abschluss des verknüpften Falls). Ereignisse ohne Fall oder in offenen Fällen verfallen nicht über diese Frist, sondern nur über eine Dokumentationstyp-Frist (Zeile 4) |
| 4 | Abweichende Frist einzelner Dokumentationstypen | {{ retention_document_type_overrides }} | `DocumentType.retention_days` | Zusätzliche Frist für Ereignisse dieses Typs (ab Zeitpunkt des Ereignisses): Es gilt die zuerst ablaufende Frist. Eine längere Typ-Frist verlängert die Fristen 1–3 nicht |
| 5 | Aktivitäten / Zeitstrom-Einträge | {{ retention_activities_days }} Tage | `retention_activities_days` | **Hart**-Löschung (kein Papierkorb) |
| 6 | Papierkorb-Frist für gelöschte Personen und Fälle | {{ client_trash_days }} Tage | `client_trash_days` | Person bzw. Fall wird automatisch anonymisiert; bis dahin wiederherstellbar |
| 7 | Audit-Log | {{ auditlog_retention_months }} Monate | `auditlog_retention_months` | Einträge werden gelöscht. `0` schaltet die Löschung **ab**; Sicherheitsverstöße, System-Marker, die Nachweise der Historien-Redaktion und der Nachweis herausgegebener Berichtszeiträume sind generell ausgenommen, ebenso alle Einträge ohne Einrichtung (Systemadministration, Konten ohne Einrichtung, Anmeldeversuche unter unbekanntem Benutzernamen, installationsweite Lauf-Marker), die kein Lauf löscht. Aufbewahrungssperren (Legal Hold) wirken nicht auf das Audit-Log. Die Berichtszeitraum-Nachweise enthalten Zeitraum, Format und die auslösende Person; Empfänger und Zweck sind ungeprüfter Freitext (organisatorische Vorgabe: Organisation statt Ansprechperson eintragen) |
| 8 | Inhalte früherer Fassungen eines Ereignisses | {{ eventhistory_detail_months }} Monate | `eventhistory_detail_months` | Die Werte der Fassung werden gelöscht (Redaktion), die Fassung selbst (Zeitpunkt, handelnde Person, Feldnamen) bleibt; die jüngste Fassung eines nicht gelöschten Ereignisses behält ihre Werte. Bei gelöschten Ereignissen und bei Ereignissen anonymisierter Personen geschieht dasselbe für alle Fassungen unabhängig von dieser Frist mit dem nächsten Aufbewahrungslauf. Fassungen unter einer Aufbewahrungssperre überspringt der Lauf. `0` schaltet diese Frist ab |
| 9 | Chronologie früherer Fassungen (nach ihrer Redaktion) | {{ eventhistory_chronicle_months }} Monate | `eventhistory_chronicle_months` | Die redigierte Fassung wird gelöscht, gemessen ab ihrer Redaktion. `0` schaltet die Löschung ab — dann bleibt die Chronologie unbefristet |
| 10 | Übergabenotizen (Schichtnotizen) | {{ retention_handover_notes_days }} Tage | `retention_handover_notes_days` | Die Notiz wird endgültig gelöscht (Frist ab ihrer letzten Änderung); kein Legal Hold — verfahrensrelevanter Inhalt ist vorher als Vermerk am Fall zu dokumentieren |

Ergänzende Regeln:

- **Automatische Löschung ohne Freigabe:** Der tägliche Aufbewahrungslauf (`enforce_retention`) setzt abgelaufene Ereignisse aller Kontaktstufen unmittelbar auf gelöscht — ohne Löschvorschlag und ohne Freigabe durch eine Person. Ausgenommen sind nur Daten unter einer aktiven Aufbewahrungssperre (Legal Hold).
- **Wirkung des Soft-Delete:** Mit der Löschung werden die Inhalte des Ereignisses (Zusatzfelder, Suchtext) sofort geleert und seine Dateianhänge gelöscht; die Werte seiner früheren Fassungen löscht regulär der nächste Aufbewahrungslauf. Ereignisse unter einer Aufbewahrungssperre und Ereignisse, deren Datensatz oder Person in diesem Moment von einer anderen Bearbeitung gesperrt ist, überspringt er; ein späterer Lauf holt sie nach, sobald Sperre bzw. Bearbeitung beendet sind. Die Ereigniszeile selbst bleibt als gelöscht markiert bestehen.
- **Löschvorschläge (optional):** Ein gesonderter Vorschlagslauf (`enforce_retention --propose`) legt für abgelaufene Ereignisse Vorschläge im Löschfristen-Dashboard an; die ausgelieferte Zeitplanung ruft ihn nicht auf. Leitung und Anwendungsbetreuung können einen Vorschlag freigeben, zurückstellen, verwerfen oder dort eine Aufbewahrungssperre setzen. Die Entscheidung hält den täglichen Lauf nicht auf: Er löscht abgelaufene Ereignisse unabhängig vom Vorschlagsstatus. Weder Zurückstellen noch Verwerfen schützt das Ereignis — nur eine Aufbewahrungssperre.
- **Abgeleitete Anonymisierung der Person:** Sobald **alle** Ereignisse einer Person soft-gelöscht sind, wird die Person beim nächsten Aufbewahrungslauf anonymisiert — auch ohne abgelaufene Papierkorb-Frist. Eine Person, zu der nie ein Ereignis dokumentiert wurde, wird auf diesem Weg nicht anonymisiert.
- **Fälle: eigener Löschweg, aber keine eigene Frist.** Ein Fall lässt sich einzeln löschen — er wandert in einen Papierkorb und wird nach derselben Frist 6 automatisch anonymisiert (Titel, Beschreibung, Zusatzfelder, Episoden, Wirkungsziele, Meilensteine und fall-eigene Dateien werden dabei getilgt, die Fall-Zeile bleibt als anonymes Statistik-Aggregat stehen). Bewusst **keine** zweite, eigene Frist: Sie beantwortete dieselbe Frage wie Frist 6 („wie lange ist eine Löschung noch umkehrbar") und wäre nur eine zweite Gelegenheit, sie falsch zu setzen. Ob die Löschung eine zweite Person braucht, richtet sich nach der Kontaktstufe der zugehörigen Person; zugeordnete Kontakte bleiben in der Personenakte und verlieren erst bei der Anonymisierung ihre Fallzuordnung.
- **Kein eigener Löschhorizont für Episoden, Aufgaben, Wirkungsziele und Meilensteine.** Sie hängen am Lebenszyklus ihres Falls bzw. der Person: ihre personenbeziehbaren Freitexte werden bei deren Anonymisierung mitgetilgt.
- **Aufbewahrungssperren (Legal Hold)** halten jede automatische Löschung und Anonymisierung an, bis die Sperre aufgehoben wird. Eine Sperre auf eine Person oder einen Fall schützt auch deren Ereignisse. In der Oberfläche lässt sich eine Sperre nur auf einem Löschvorschlag setzen, also nur, wenn der Vorschlagslauf eingerichtet ist. Rechtsgrundlage ist Art. 17 Abs. 3 DSGVO (Ausnahme von der Löschpflicht) — **nicht** Art. 18.
- **Einschränkung der Verarbeitung (Art. 18 DSGVO)** ist das Gegenstück und ein eigener Mechanismus: Ein Sperrvermerk an der Person lässt die Daten gespeichert (Art. 18 Abs. 2), sperrt aber ihre inhaltliche Nutzung und Änderung; ausgenommen ist der Berichtigungsworkflow. Setzen und Aufheben werden protokolliert, das Aufheben verlangt eine zweite berechtigte Person oder die Leitung. Die Unterrichtung der betroffenen Person vor dem Aufheben (Art. 18 Abs. 3) ist organisatorisch zu leisten.
- **4-Augen-Prinzip für manuelle Löschungen:** Die Löschung einer Person braucht immer die Bestätigung einer zweiten berechtigten Person; die Löschung eines Ereignisses oder Falls nur, wenn die Person auf der Stufe „Mit Zusatzangaben“ steht. Ereignisse und Fälle anderer Stufen löscht eine berechtigte Person direkt. Dateien vernichten Leitung und Anwendungsbetreuung über den Sofortlöschweg ohne zweite Person (siehe § 2.4). Der automatische Aufbewahrungslauf fällt nicht unter dieses Prinzip.
- **Restrisiko:** Das Audit-Log ist unveränderlich (Nachweispflicht, Art. 5 Abs. 2) und wird von einer Anonymisierung **nicht** mitredigiert. Vorhandene Einträge verschwinden erst mit Frist 7 — bei `auditlog_retention_months = 0`, bei protokollierten Sicherheitsverstößen und beim Nachweis herausgegebener Berichtszeiträume gar nicht, Einträge ohne Einrichtung in keinem Fall — die Berichtszeitraum-Nachweise führen die auslösende Person und die Freitextfelder Empfänger/Zweck dauerhaft und unveränderbar mit.
- Die Fristen werden durch den täglich laufenden Aufbewahrungs-Job durchgesetzt; ist der Job nicht eingerichtet, greift **keine** der zehn Fristen.

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
- Account-Sperrung nach 10 Fehlversuchen
- Schlüsselrotation ohne Datenverlust (MultiFernet)
- Malware-Schutzschicht: ClamAV scannt sämtliche Datei-Uploads vor der serverseitigen Verschlüsselung; infizierte Dateien werden abgelehnt (fail-closed) (Ref. #524)
- Defense-in-Depth auf Datenbankebene: PostgreSQL Row Level Security (RLS) bildet eine zweite Verteidigungslinie gegen facility-übergreifende Datenabflüsse. Für Sitzungen der einrichtungsgebundenen Rollen verhindert RLS einen Cross-Tenant-Zugriff auch bei Fehlern im ORM-Layer. Ausnahme: In Sitzungen der Systemadministration setzt die Anwendung `app.is_super_admin`, und die Policies lassen einrichtungsübergreifende Zugriffe zu; als `SYSTEM_VIEW` protokolliert werden jeder Aufruf im Systembereich (`/system/`) und in der Verwaltungsoberfläche (`/admin-mgmt/`) die Listen- und Detailansichten von Personen, Fällen, Ereignissen, Ereignis-Historie, Aufgaben und Anhängen. Die übrigen Verwaltungsansichten (z. B. Löschanträge samt Begründung, Benutzer, Einstellungen, Audit-Log) protokollieren Lesezugriffe nicht, und schlägt die Protokollierung fehl, bleibt der Zugriff trotzdem möglich (Ref. #542)
- Endgeräteschutz durch Offline-Kryptografie: Offline im Browser vorgehaltene Inhalte werden client-seitig mit einem **non-extractable** AES-GCM-256-Schlüssel verschlüsselt. Der Schlüssel wird bei der Anmeldung aus dem Benutzerpasswort abgeleitet (PBKDF2, 600 000 Iterationen, SHA-256) und als `CryptoKey` in einer eigenen IndexedDB (`anlaufstelle-crypto`) gespeichert; er verlässt das Gerät nicht und überdauert Neuladen sowie Tab-, Browser- und Geräteneustart. „Non-extractable“ verhindert, dass die Schlüsselbytes ausgelesen oder exportiert werden, nicht aber, dass der gespeicherte Schlüssel im Browser dieses Geräts zum Entschlüsseln benutzt wird: Wer innerhalb der Leerlaufzeit Zugriff auf das entsperrte Gerät und das Browserprofil hat, auch über die Entwicklerwerkzeuge des Browsers, kann die Offline-Inhalte lesen; die Anwendung öffnet in dieser Zeit offline ohne erneute Anmeldung. **Löschwege:** Abmelden löscht Schlüssel und Offline-Speicher einschließlich ungesendeter Arbeit (zusätzlich `Clear-Site-Data` in der Antwort des Servers). Ein Passwortwechsel löscht ebenfalls beides; liegt ungesendete Arbeit vor, fragt die Anwendung vorher nach. Nach Ablauf der Leerlaufzeit (Sitzungsdauer, Default 1800 s, einrichtungskonfigurierbar) greift die Leerlaufprüfung — aber nur, solange oder sobald eine Seite der Anwendung im Browser läuft (beim Laden, im 60-Sekunden-Intervall, bei Rückkehr in den Tab, tab-übergreifend): Liegt ungesendete Arbeit vor, wird nur der Schlüssel gelöscht (Sperre); der verschlüsselte Bestand bleibt erhalten und wird nach erneuter Anmeldung nur wieder lesbar, wenn Passwort und Offline-Salt des Kontos unverändert sind. Den Salt erneuert der Server bei Rollenwechsel, bei Deaktivierung (auch eine spätere Reaktivierung stellt den alten nicht wieder her) und bei einer Anmeldung mit korrektem Passwort an einem gesperrten Konto; eine Passwortänderung (auch das Zurücksetzen) ändert die Schlüsselableitung ohnehin. Danach lässt sich der alte Bestand nicht mehr entschlüsseln: zwischengespeicherte Daten verwirft die Anwendung, ungesendete Einträge zeigt sie als nicht zustellbar an. Ohne ungesendete Arbeit werden Schlüssel und Offline-Speicher gelöscht. Schließen des Tabs oder Browsers und ein Geräteneustart sind **kein** Löschereignis — bewusst, damit das Wiederöffnen im aufsuchenden Einsatz funktioniert (akzeptiertes Restrisiko F-01). Ein Gerät, das nach Ablauf der Leerlaufzeit nicht wieder geöffnet wird, behält Schlüssel und verschlüsselten Bestand; auch abgelaufene Offline-Bestände (48 Stunden) entfernt die Anwendung erst beim nächsten Öffnen mit geladenem Schlüssel. **TOM-relevant:** Die für die Offline-Ansicht nötigen Inhalte einschließlich Art.-9-Daten werden serverseitig entschlüsselt und als vorgefiltertes Bundle in den Browser geliefert (dort erst wieder client-seitig verschlüsselt); die serverseitigen Sichtbarkeits- und Rechte-Filter bleiben autoritativ. **Benanntes Restrisiko:** Verschlüsselt sind die Inhalte. Unverschlüsselt daneben liegen Ordnungsmerkmale: Datensatz- und Personen-IDs, Bearbeitungs- und Abgleichstatus, Zeitstempel (letzter Abgleich, Eingang in die Sende-Warteschlange, Entwurfsstand) und die Ziel-Adressen der Sende-Warteschlange (bewusster Verzicht). DSFA-/TOM-Einordnung und Restrisiken: ADR-022 § Akzeptierte Restrisiken (Ref. ADR-022, #1065)
- Zwei-Faktor-Authentifizierung: TOTP-basierte 2FA ist technisch umgesetzt und kann pro Benutzer oder facility-weit **erzwungen** werden (zuvor ausschließlich optional). Senkt das Risiko bei kompromittierten Passwörtern erheblich (Ref. #521)

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
