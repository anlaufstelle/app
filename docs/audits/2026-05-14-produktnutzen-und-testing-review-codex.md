# Produktnutzen- und Testing-Doku-Review: Anlaufstelle

Datum: 2026-05-14
Scope:
- direkte Produktbeobachtungen aus der aktuellen Codebasis und Dokumentation
- Review der Dateien unter `docs/testing/*`
- Vorschläge für Ergänzungen, keine Umsetzung in Produktcode

Basis:
- `docs/testing/manual-test-matrix.md`
- `docs/testing/test-matrix-index.md`
- ergänzende statische Code-/Docs-Inspektion
- kein vollständiger Testlauf

## 1. Kurzfazit

Anlaufstelle wirkt technisch schon stark in Richtung Datenschutz, Auditierbarkeit und Facility-Isolation entwickelt. Was weiterhin direkt auffällt: Viele gute Fähigkeiten sind vorhanden, aber sie wirken noch wie interne Infrastruktur. Der größte nächste Produktnutzen entsteht, wenn diese Fähigkeiten für Betreiber:innen und Einrichtungen stärker geführt, geprüft und erklärt werden.

Die Software hat damit zwei unterschiedliche Reifegrad-Aufgaben:

1. Technisch weiter konsolidieren: Audit-Helper, Settings-Audit, Systembereich, DB-Rollen, FieldType-Logik.
2. Produktseitig operationalisieren: Einrichtung, Datenschutzbetrieb, Release-Tests und Betreiber-Checks so führen, dass kleine soziale Einrichtungen keine internen Architekturdetails verstehen müssen.

Größter kurzfristiger Benefit:

- ein Einrichtungs- und Dokumentationsassistent,
- ein Betriebs-/Compliance-Dashboard,
- Datenschutz-Review für sensible Freitexte,
- klare Release-Testprofile statt einer einzigen sehr großen manuellen Matrix.

## 2. Was weiterhin direkt an der Software auffällt

### 2.1 Die Software ist technisch reif, aber noch nicht ausreichend geführt

Viele Schutzmechanismen existieren bereits: MFA, Sudo-Mode, RLS, AuditLog, Retention, Legal Holds, DSGVO-Paket, File Vault, Offline-Mechanik, dynamische Dokumentationstypen. Für Entwickler:innen ist das nachvollziehbar. Für Betreiber:innen bedeutet es aber: Viele sicherheitsrelevante Entscheidungen liegen an Konfiguration, Seed-Daten, Admin-Oberflächen, Runbooks und manuellem Wissen.

Das ist der zentrale Produkt-Spannungspunkt.

Die Zielgruppe sind kleine oder mittelgroße soziale Einrichtungen. Diese brauchen keine maximale Konfigurationsfreiheit als Erstkontakt, sondern sichere Voreinstellungen, klare Warnungen und geführte Entscheidungen. Das System sollte stärker sagen: "Diese Konfiguration ist sicher", "diese Einstellung ist riskant", "dieser Datenschutzprozess wurde seit X Tagen nicht geprüft".

### 2.2 Konfiguration ist ein Kernfeature, aber noch kein Produktflow

Die Codebasis enthält ein starkes Modell für:

- Facilities,
- Rollen,
- DocumentTypes,
- FieldTemplates,
- Settings,
- Retention-Fristen,
- Sensitivity,
- Upload-Policies.

Aktuell wirkt diese Konfiguration aber noch stark als Admin-/Seed-/Django-Admin-nahe Infrastruktur. Das ist für Entwicklung und Pilotbetrieb ausreichend, aber nicht ideal für reale Betreiber:innen.

Großer Benefit:

- Ersteinrichtung als geführter Flow statt als Admin-Arbeit.
- Einrichtungstyp auswählen: Kontaktladen, Streetwork, Beratungsstelle, Notschlafstelle.
- Vorkonfigurierte Dokumentationstypen und Feldvorlagen übernehmen.
- Sichtbarkeit und Sensitivität vor dem Speichern erklären.
- Retention und MFA mit empfohlenen Defaults setzen.

Das würde Supportaufwand und Fehlkonfigurationen wahrscheinlich stärker senken als ein weiteres internes Refactoring.

### 2.3 Datenschutz ist stark modelliert, aber Freitext bleibt die praktische Schwachstelle

Die bestehenden Datenschutzmechanismen greifen besonders gut bei Event-Feldern und Attachments. Gleichzeitig bleibt die reale Nutzung niedrigschwelliger Sozialarbeit freitextlastig: Notizen, Fallbeschreibungen, Aufgabenbeschreibungen, Übergabetexte.

Freitext ist dort gefährlich, wo Nutzer:innen in Stresssituationen schnell Klarnamen, Aufenthaltsorte, Diagnosen, Gewaltvorfälle, Telefonnummern oder Drittpersonen eintragen. Ein Warnhinweis allein reicht erfahrungsgemäß nicht.

Großer Benefit:

- Datenschutz-Review für Freitextfelder.
- Liste potenziell sensibler Freitexte.
- Hinweise: "In verschlüsseltes Dokumentationsfeld verschieben", "Sensitivity erhöhen", "Text neutralisieren", "Review erledigt".
- Optional später: lokale Regel-/Pattern-Prüfung für Telefonnummern, E-Mail, Namen, Diagnosen, Orte.

Wichtig: Das sollte nicht als automatische Wahrheit wirken, sondern als Review-Workflow.

### 2.4 Betriebssicherheit ist produktrelevant, nicht nur Ops

Self-Hosting ist nur dann glaubwürdig, wenn Betreiber:innen den Systemzustand sehen können. Aktuell gibt es Healthchecks und Systembereiche, aber der größte Nutzen läge in einem klaren Betriebsbereitschafts-Dashboard.

Wichtige Fragen:

- Läuft die App mit einer `NOSUPERUSER`-DB-Rolle?
- Hat die App-Rolle wirklich kein `BYPASSRLS`?
- Gibt es eine getrennte Admin-/Maintenance-DB-Rolle?
- Ist das letzte Backup aktuell?
- Wurde ein Restore-Test dokumentiert?
- Läuft ClamAV und ist die Signatur aktuell?
- Läuft der Retention-Job?
- Ist MFA für privilegierte Rollen aktiv?
- Stimmen App-Version, Image-Tag und Migrationstand überein?

Großer Benefit:

- Betreiber:innen sehen nicht nur "Health ok", sondern "betriebsbereit für sensible Sozialdaten".
- Support kann Fehlkonfigurationen schneller erkennen.
- Security-Versprechen werden überprüfbar.

### 2.5 Es fehlt eine priorisierte Arbeitszentrale

Die Anwendung hat viele operative Objekte:

- offene Aufgaben,
- Wiedervorlagen,
- Retention-Proposals,
- DeletionRequests,
- Legal Holds,
- Audit-/Security-Ereignisse,
- Offline-Sync-Konflikte,
- unvollständige Dokumentationen.

Der große Produktnutzen läge in einer klaren Arbeitszentrale je Rolle:

- Fachkraft: heutige Kontakte, offene Aufgaben, Offline-Konflikte, zuletzt bearbeitete Klient:innen.
- Leitung: Retention-Entscheidungen, Löschanträge, überfällige Aufgaben, Berichtspflichten.
- Facility-Admin: Benutzer, MFA-Status, Konfigurationswarnungen.
- Super-Admin: Systemzustand, Mandanten, Backups, Security-Ereignisse.

Das wäre keine neue Domäne, sondern eine bessere Oberfläche für vorhandene Domänen.

## 3. Features mit großem Benefit

### NF-001: Einrichtungs- und Dokumentationsassistent

Priorität: **sehr hoch**

Ziel:

Eine neue Einrichtung soll ohne direkte Django-Admin-Konfiguration sicher startfähig werden.

Möglicher Umfang:

- Einrichtungstyp auswählen.
- Stammdaten erfassen.
- Benutzerrollen anlegen.
- MFA-Empfehlung aktivieren.
- Dokumentationstypen aus Bibliothek auswählen.
- FieldTemplates prüfen und anpassen.
- Retention-Defaults setzen.
- Datei-Upload-Policy setzen.
- Zusammenfassung mit Risikohinweisen anzeigen.

Großer Nutzen:

- reduziert Fehlkonfiguration,
- verkürzt Onboarding,
- macht Domänenbibliotheken produktfähig,
- senkt Support,
- macht das Projekt für-/Pilot-/Self-Hosting-Kontexte greifbarer.

Akzeptanzkriterien:

- Eine neue Facility kann ohne Shell/Seed-Spezialwissen konfiguriert werden.
- Nach Abschluss existieren Settings, Rollen, Dokumentationstypen und sichere Defaults.
- Der Assistent erzeugt einen AuditLog-Eintrag mit den gesetzten Konfigurationsbereichen, nicht mit sensiblen Werten.
- Unsichere Entscheidungen werden sichtbar markiert.

### NF-002: Datenschutz-Review für Freitextfelder

Priorität: **hoch**

Ziel:

Sensible Inhalte in klassischen Freitextfeldern sollen sichtbar und bearbeitbar werden, bevor sie dauerhaft als Klartext-Risiko liegen bleiben.

Möglicher Umfang:

- Review-Liste für `Client.notes`, `Case.description`, `Episode.description`, `WorkItem.description` und nicht verschlüsselte Event-Freitexte.
- Filter nach Alter, Modell, Facility, Bearbeiter:in, Risiko-Hinweis.
- Markierung "geprüft bis Datum".
- Vorschlag "in verschlüsseltes Event-Feld überführen".
- Optional: Pattern-Erkennung für Telefonnummern, E-Mail-Adressen, Geburtsdaten, Adressen.

Großer Nutzen:

- greift das wahrscheinlich praxisrelevanteste Datenschutzrisiko an,
- verhindert nicht die niedrigschwellige Arbeit,
- schafft einen realistischen Review-Prozess statt nur einer Policy.

Akzeptanzkriterien:

- Review zeigt keine Inhalte, die die Rolle nicht sehen darf.
- Alle Aktionen werden auditiert.
- Markierung als "geprüft" ist reversibel oder erneut prüfbar.
- Keine externe KI-/API-Abhängigkeit für sensible Inhalte.

### NF-003: Betriebs-/Compliance-Dashboard

Priorität: **hoch**

Ziel:

Betreiber:innen sehen, ob die Installation für sensible Daten korrekt betrieben wird.

Mögliche Checks:

- App-DB-Rolle: `NOSUPERUSER`.
- App-DB-Rolle: kein `BYPASSRLS`.
- Admin-/Maintenance-Rolle vorhanden.
- letzter Backup-Zeitpunkt.
- letzter Restore-Test.
- ClamAV erreichbar und aktuell.
- Retention-Job zuletzt erfolgreich.
- Pending migrations.
- App-Version/Image-Tag.
- MFA-Quote bei Admin/Lead/Super-Admin.
- Anzahl kritischer Audit-/Security-Ereignisse seit letztem Check.

Großer Nutzen:

- macht Security-Zustand sichtbar,
- verbessert Self-Hosting-Verlässlichkeit,
- reduziert Blindflug im Betrieb.

Akzeptanzkriterien:

- Dashboard unterscheidet `ok`, `warning`, `critical`, `unknown`.
- Kritische Checks enthalten konkrete Handlungsanweisung.
- Keine geheimen Werte werden ausgegeben.
- System-Checks sind testbar und im Runbook referenziert.

### NF-004: Rollenbezogene Arbeitszentrale

Priorität: **mittel/hoch**

Ziel:

Die wichtigsten Handlungen sollen pro Rolle an einer Stelle sichtbar werden.

Möglicher Umfang:

- "Heute" für Fachkräfte: eigene Aufgaben, letzte Klient:innen, offene Dokumentationen, Offline-Konflikte.
- "Leitung" für Entscheidungen: DeletionRequests, Retention-Proposals, Legal Holds, Statistik-Fälligkeiten.
- "Admin" für Betrieb: Nutzer ohne MFA, riskante Settings, offene Konfigurationswarnungen.
- "Super-Admin" für Installation: Systemstatus, Facilities, globale Audit-Ereignisse.

Großer Nutzen:

- weniger Navigation,
- weniger vergessene Datenschutzentscheidungen,
- klarere tägliche Arbeit.

### NF-005: Datenschutzfreundliche externe Berichte

Priorität: **mittel**

Ziel:

Interne Statistik und externe Berichtspflichten sollten deutlicher getrennt werden.

Möglicher Umfang:

- Berichtsvorlagen mit Datenschutzprofil.
- Keine Pseudonym-Rankings in externen Berichten.
- Kleine Gruppen unterdrücken oder aggregieren.
- K-Anonymity-Schwelle sichtbar machen.
- Export enthält Metadaten zu Zeitraum, Filter, Schwellenwerten.

Großer Nutzen:

- reduziert Re-Identifikationsrisiko,
- erleichtert Förder-/Trägerberichte,
- passt zur bestehenden Statistik-/Reporting-Domäne.

## 4. Review von `docs/testing/*`

### 4.1 Aktueller Zustand

`docs/testing/manual-test-matrix.md` ist sehr umfassend. Sie enthält:

- 3 Zielgruppen-Sektionen: Anwender-Smoke, Entwickler-Komplett, Auditor-DSGVO/Security.
- Setup-Blöcke für dev und lokal.
- Browser-/Mobile-Konventionen.
- Status-Legende.
- TC-ID-Schema.
- viele konkrete manuelle Schritte mit erwarteten Ergebnissen.
- Anhänge zu Browser-Matrix, Risiken/Test-Lücken, E2E-Coverage und Testdaten.

`docs/testing/test-matrix-index.md` ist ein generierter Index aus der Matrix. Er enthält aktuell:

- 227 Tests,
- 187 mit E2E-Coverage,
- 40 ohne E2E,
- E2E-Quote 82 %.

Bewertung:

Die Testing-Doku ist ungewöhnlich detailliert und grundsätzlich wertvoll. Die größte Schwäche ist nicht fehlende Tiefe, sondern Pflegeaufwand und fehlende Trennung zwischen Testkatalog, Testlauf und Release-Gate.

### 4.2 Konkrete Inkonsistenz: 222 vs. 227 Tests

Im generierten Index stehen 227 Tests. In der manuellen Matrix stehen an mehreren Stellen noch ältere Zahlen:

- Setup-Text: "26 der 222 Cases"
- Anhang C: Gesamt 222
- Sektion-B-/Sektion-C-Summen wirken ebenfalls veraltet

Empfehlung:

- Alle manuellen Summen aus der Matrix entfernen oder als generierte Werte markieren.
- Anhang C automatisch aus derselben Quelle befüllen wie `test-matrix-index.md`.
- `scripts/build_test_matrix_index.py` optional erweitern, sodass es auch Coverage-Bilanz und Manuell-only-Liste aktualisiert.

Benefit:

- verhindert schleichende Doku-Drift,
- macht die Matrix auditierbarer,
- spart manuelle Pflege.

### 4.3 Testkatalog und Testlauf trennen

Aktuell enthält jeder Case eine `Status`-Zeile. Das ist praktisch für einen einzelnen Durchlauf, aber problematisch als Single-Source-of-Truth im Git-Repo: Statusänderungen eines Release-Laufs würden den Testkatalog selbst verändern.

Empfehlung:

- Matrix bleibt Testkatalog.
- Testläufe bekommen eigene Dateien, z. B.:
 - `docs/testing/runs/2026-05-14-rc-v0.12.md`
 - `docs/testing/runs/template.md`
- Run-Datei enthält:
 - App-Version,
 - Commit-SHA,
 - Umgebung,
 - Tester:in,
 - Browser,
 - Seed-/Datenstand,
 - Ergebnis je TC-ID,
 - offene Issues,
 - Blocker,
 - Freigabeentscheidung.

Benefit:

- Testhistorie bleibt nachvollziehbar.
- Matrix bleibt stabil.
- Release-Freigaben sind prüfbar.

### 4.4 Release-Testprofile ergänzen

Die Matrix ist zu groß, um sie immer komplett auszuführen. Sie braucht explizite Profile.

Empfohlene Profile:

| Profil | Ziel | Umfang |
|---|---|---|
| PR-Smoke | schnelle Regression | Auth, Dashboard, Client, Event, WorkItem, ein Security-Smoke |
| RC-Smoke | Release Candidate | komplette Sektion A + kritische P1-Cases aus B/C |
| Security-RC | sicherheitsrelevanter Release | MFA, Sudo, RLS, Audit, Retention, File Vault, DSGVO |
| Mobile/PWA-RC | UI-/Offline-Release | Mobile, PWA, Offline, Service Worker, Upload |
| Ops-RC | Deployment-/Self-Hosting-Release | Health, DB-Rollen, Backup, Restore, ClamAV, Retention-Cron |
| Major-Release | v1.0 oder Datenmodelländerung | volle Matrix plus echte Browser-/Mobile-Stichprobe |

Benefit:

- realistische Release-Disziplin,
- weniger "alles oder nichts",
- bessere Priorisierung bei wenig Zeit.

### 4.5 Fehlende Tests für neue High-Benefit-Funktionen ergänzen

Wenn die oben empfohlenen Features umgesetzt werden, sollten direkt Testbereiche ergänzt werden.

Neue Bereichscodes:

- `SETUP` für Einrichtungs-/Konfigurationsassistent.
- `COMP` für Betriebs-/Compliance-Dashboard.
- `PRIV` für Datenschutz-Review/Freitext.
- `REPORT` für datenschutzfreundliche externe Berichte.

Vorgeschlagene neue Testfälle:

| TC-ID | Titel | Zweck |
|---|---|---|
| ENT-SETUP-01 | Neue Facility per Assistent vollständig anlegen | End-to-End-Konfiguration ohne Shell |
| ENT-SETUP-02 | Dokumentationsbibliothek auswählen | Template-Übernahme und Anpassung |
| ENT-SETUP-03 | Riskante Einstellung wird gewarnt | Guardrails im Setup |
| ENT-SETUP-04 | Setup schreibt AuditLog | Nachvollziehbarkeit |
| ENT-COMP-01 | DB-Rollencheck zeigt sichere App-Rolle | Self-Hosting-Härtung |
| ENT-COMP-02 | Backup veraltet erzeugt Warnung | Betriebsfähigkeit |
| ENT-COMP-03 | ClamAV down ist critical | Upload-Sicherheit |
| ENT-COMP-04 | Retention-Job überfällig ist warning/critical | Datenschutzbetrieb |
| ENT-PRIV-01 | Freitext-Review listet riskante Inhalte | Datenschutz-Review |
| ENT-PRIV-02 | Review respektiert Rollen-Sichtbarkeit | Kein neuer Leak |
| ENT-PRIV-03 | Review-Aktion wird auditiert | Compliance |
| ENT-REPORT-01 | Externer Bericht unterdrückt kleine Gruppen | Re-Identifikationsschutz |
| ENT-REPORT-02 | Externer Bericht enthält keine Pseudonym-Rankings | Datenschutzfreundliches Reporting |

### 4.6 Operations- und Self-Hosting-Cases stärker ausbauen

Die Matrix hat Health-, ClamAV- und Header-Cases, aber die neuen Betriebsrisiken sollten expliziter werden.

Empfohlene Ergänzungen:

- DB-Rolle ist `NOSUPERUSER`.
- App-Rolle hat kein `BYPASSRLS`.
- Super-Admin-/Systembereich funktioniert ohne Facility.
- Backup ist vorhanden und jünger als X Stunden.
- Restore-Test wurde erfolgreich gegen frische DB durchgeführt.
- Media-Volume ist persistent gemountet.
- `collectstatic`/Staticfiles funktionieren nach Image-Pull.
- Migrationen sind angewendet.
- Retention-Cron läuft und meldet Ergebnis.
- Healthcheck unterscheidet `ok`, `degraded`, `critical`.

Diese Cases sollten nicht nur in `SYS`, sondern teilweise auch in Sektion C auftauchen, weil sie DSGVO-relevant sind.

### 4.7 Accessibility als eigener Testbereich

Aktuell gibt es einzelne Accessibility-Hinweise: Touch Targets, Tab-Reihenfolge, Tooltip/`aria-describedby`. Das reicht als Stichprobe, aber nicht als wiederholbares Testprofil.

Empfehlung:

Neuen Bereich `A11Y` ergänzen:

- Tastaturbedienung für Hauptnavigation.
- Fokus-Reihenfolge in Forms.
- sichtbarer Fokuszustand.
- Dialog/Modal-Fokusfalle.
- Screenreader-Labels für Icon-Buttons.
- Fehlermeldungen mit Formularfeldern verknüpft.
- Farbkontrast für Badges, Alerts, Buttons.
- Reduced-Motion-Verhalten.
- Mobile Zoom 200 % ohne Layoutbruch.

Benefit:

- deutlich bessere Bedienbarkeit,
- weniger Regressionen in UI-Refactorings,
- wichtig für soziale Einrichtungen mit heterogener Nutzung.

### 4.8 Performance-Budgets ergänzen

Performance wird punktuell geprüft, z. B. Statistik mit >1000 Events und Audit-Pagination. Sinnvoll wäre eine kleine Budget-Tabelle.

Empfohlene Budgets:

| Bereich | Budget |
|---|---|
| Dashboard/Home | TTI < 2 s bei Medium-Seed |
| Client-Detail | < 1 s Serverzeit bei 100 Events |
| Event-Edit mit 10 Attachments | keine N+1-Queries, < 500 ms Serverzeit |
| Suche | < 500 ms bei 1.000 Clients |
| AuditLog-Liste | < 500 ms pro Seite bei 10.000 Einträgen |
| Retention-Dashboard | < 2 s bei 1.000 Proposals |
| Statistik | < 3 s bei Large-Seed |

Zusätzlich:

- Query-Count-Tests für bekannte N+1-Risiken.
- "Performance-Fail" als eigener Status in Testläufen.
- Hinweise, welche Tests nur lokal/mit Debug-Toolbar sinnvoll sind.

### 4.9 Security-Negativtests ausbauen

Die Matrix enthält viele gute Security-Cases. Ergänzen würde ich:

- Settings-Audit: Änderung jedes auditpflichtigen Settings erzeugt genau einen Audit-Diff mit Feldnamen.
- AuditLog-Payload enthält keine PII bei Settings-/Security-Ereignissen.
- Export-Service filtert Zeilen und Felder nach Rolle.
- Externe Berichte enthalten keine Pseudonyme.
- Sudo-Mode nach Passwortwechsel/MFA-Änderung invalidiert.
- User-Deaktivierung beendet bestehende Sessions.
- Super-Admin-Systembereich loggt jeden Zugriff.
- CSP-Report-Endpoint nimmt keine riesigen Payloads an.
- CSV-/PDF-Exports sind gegen Formula Injection und Dateinamen-Leaks abgesichert.

Diese Fälle passen in `AUDIT`, `STAT`, `SYS`, `DSGVO` und Sektion C.

### 4.10 Offline-/PWA-Cases um Privacy-Failure-Modes erweitern

Offline ist bereits stark vertreten. Ergänzende Fälle mit hohem Datenschutzwert:

- Logout löscht lokale Offline-Daten zuverlässig.
- Passwortwechsel erzwingt Re-Bootstrap und macht alte Offline-Daten unlesbar.
- Rollen-/Sensitivity-Änderung invalidiert oder reduziert Offline-Bundle.
- Device verliert Lease: lokale Daten bleiben verschlüsselt und werden nicht angezeigt.
- Offline-Sync-Konflikt mit inzwischen gelöschtem/retention-betroffenem Event.
- Offline-Upload mit später serverseitig abgelehntem Dateityp.

Benefit:

- deckt nicht nur Happy Path, sondern echte Streetwork-/Geräteverlust-Risiken ab.

### 4.11 Index funktional erweitern

Der generierte Index ist nützlich. Er könnte mehr Wert liefern.

Empfehlung:

- Liste "Top Automatisierungskandidaten" aus Manuell-only + häufiger Release-Relevanz.
- Liste "🔧 LOKAL/SSH-Cases" separat.
- Liste "Security/DSGVO ohne E2E".
- Diff-Check: Index ist aktuell gegenüber Matrix.
- Optional CI-Check: Wenn Matrix geändert wurde, muss Index aktualisiert sein.

Benefit:

- schnellerer Überblick,
- weniger manuelle Suche,
- bessere Priorisierung.

## 5. Konkrete Ergänzungsvorschläge für `docs/testing`

### Neue Dateien

1. `docs/testing/run-template.md`

Inhalt:

- Release/Commit/Umgebung
- Tester:innen
- Browser/Mobile-Geräte
- Seed-/Datenstand
- ausgeführtes Testprofil
- Ergebnis-Tabelle pro TC-ID
- Blocker
- offene Issues
- Freigabeentscheidung

2. `docs/testing/release-test-profiles.md`

Inhalt:

- PR-Smoke
- RC-Smoke
- Security-RC
- Mobile/PWA-RC
- Ops-RC
- Major-Release
- je Profil: verpflichtende TC-IDs, optionale Stichproben, Exit-Kriterien

3. `docs/testing/automation-candidates.md`

Inhalt:

- Manuell-only-Cases,
- Priorität,
- Automatisierbarkeit,
- benötigtes Test-Setup,
- vorgeschlagene Testdatei.

### Änderungen an `manual-test-matrix.md`

- Statische Summen durch generierte Werte ersetzen oder entfernen.
- Neue Bereichscodes `SETUP`, `COMP`, `PRIV`, `A11Y`, `REPORT` aufnehmen.
- Anhang B um Produkt-/Ops-Lücken ergänzen:
 - DB-Rollencheck,
 - Settings-Audit-Vollständigkeit,
 - Restore-Test,
 - Freitext-Review,
 - externe Berichtsk-Anonymität,
 - Offline-Rollenänderung.
- Anhang C automatisch befüllen oder auf `test-matrix-index.md` verweisen.
- Status-Zeilen als "nur für lokale Kopie/Testlauf" markieren, wenn kein Run-Log eingeführt wird.

### Änderungen an `test-matrix-index.md`

- Generierung um folgende Abschnitte erweitern:
 - Manuell-only nach Priorität,
 - lokale/SSH-Cases,
 - Security/DSGVO ohne E2E,
 - Tests pro Profil,
 - Stale-Warnung, wenn Matrix neuer als Index ist.

## 6. Priorisierte Umsetzung

### P1: Sofort hoher Nutzen

1. Testzählungen konsistent machen: 222 vs. 227 bereinigen.
2. Run-Template einführen.
3. Release-Testprofile definieren.
4. Ops-/DB-Rollen-/Backup-/Restore-Cases ergänzen.
5. Settings-Audit-Vollständigkeit als Testfall ergänzen.

### P2: Produktfeatures testbar vorbereiten

1. SETUP-Cases für Einrichtungsassistent ergänzen.
2. COMP-Cases für Betriebs-/Compliance-Dashboard ergänzen.
3. PRIV-Cases für Freitext-Review ergänzen.
4. REPORT-Cases für datenschutzfreundliche externe Berichte ergänzen.

### P3: Qualitätsbreite erhöhen

1. A11Y-Bereich ausbauen.
2. Performance-Budgets dokumentieren.
3. Offline-Privacy-Failure-Modes ergänzen.
4. Index um Automatisierungskandidaten erweitern.

## 7. Gesamtbewertung

Die Testing-Doku ist bereits sehr stark und deutlich besser als bei vielen vergleichbaren Projekten. Der nächste Qualitätssprung liegt nicht in "noch mehr Testfälle" an sich, sondern in Struktur:

- Testkatalog bleibt stabil.
- Testläufe werden separat dokumentiert.
- Release-Profile machen den Umfang ausführbar.
- Index und Coverage-Bilanz werden automatisch konsistent gehalten.
- Neue Produktfeatures bekommen vorab eigene Testbereiche.

Produktseitig ist der größte Hebel, vorhandene technische Sicherheitsmechanismen in geführte Betreiber- und Einrichtungsflows zu übersetzen. Einrichtungsassistent, Betriebs-/Compliance-Dashboard und Freitext-Review würden voraussichtlich mehr praktischen Nutzen bringen als ein weiteres rein internes Refactoring.
