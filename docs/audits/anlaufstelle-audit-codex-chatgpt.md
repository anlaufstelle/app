# Audit: anlaufstelle/app

Stand: `main` bei Commit `35e0f5b` vom 28.04.2026 (`v0.10.2`).

Scope: statische Analyse des Repositories. Lokale Tests konnten in der Umgebung nicht ausgefuehrt werden, weil `pytest` und Docker nicht installiert waren. CI-Konfiguration, Teststruktur und Code wurden geprueft.

Kurzurteil: architektonisch ernst gemeint, fachlich stark, sicherheitlich noch nicht produktionsreif. Kein generischer CRUD-Prototyp, aber auch kein System, dem ich heute echte sensible Sozialdaten als fuehrendes Produktivsystem anvertrauen wuerde.

## 1. Systemarchitektur

Architekturstil: Django Modular Monolith, technisch aber als eine grosse App `core`, nicht als echte fachliche Django-App-Struktur. Die Trennung erfolgt ueber Pakete wie `models`, `services`, `views`, `forms`, `middleware`, `signals`.

Architekturbeschreibung: Web-Monolith mit serverseitigem Django, HTMX/Alpine/Tailwind, PostgreSQL, RLS, File Vault, Offline-PWA und serviceorientierter Domaenenlogik. Zentrale Domaenenobjekte sitzen in `Event`, `Client`, `DocumentType`/`FieldTemplate`, Facility/Organization und WorkItems.

Staerken:

- Klare Entscheidung fuer Self-Hosted-Monolith statt unnoetiger Microservices.
- Services existieren fuer kritische Flows: Events, Retention, File Vault, Offline, Statistics.
- Facility-Scoping plus PostgreSQL-RLS ist deutlich staerker als uebliche Django-Filterdisziplin.
- CI enthaelt Unit-, E2E-, Security- und Architekturtests.

Schwaechen:

- `core` ist faktisch ein fachlicher Grosscontainer. Grenzen sind Konvention, nicht Architektur.
- `services/event.py` und `services/retention.py` sind zu maechtig; Lifecycle, Audit, Historie, Rechte und Persistenz vermischen sich.
- Der Service Layer ist nicht konsequent genug als Policy-Grenze erzwungen. Views, Services und Models tragen alle Fachlogik.
- Django Admin bleibt eine globale Operator-Oberflaeche und ist kein tenant-sicheres Produkt-Backend.

Risiko-Level: mittel. Die Grundform ist richtig, aber die kritischen Datenlebenszyklen sind architektonisch noch nicht sauber gekapselt.

## 2. Domaenenmodell und Konzept

Das Modell ist fachlich ueberdurchschnittlich: pseudonyme Klienten, anonyme Kontakte, frei konfigurierbare Dokumenttypen, Kontaktstufen, Faelle/Episoden/Ziele, WorkItems, Retention, Legal Hold, Offline-Modus. Das ist kein zufaelliges CRUD-Schema.

Elegant:

- `Event` als zentrale Dokumentationseinheit passt zur niedrigschwelligen Arbeit.
- `DocumentType` + `FieldTemplate` ist pragmatisch fuer kleine Teams mit unstrukturierten Prozessen.
- Anonymous Event vs. Client-Pseudonym ist fachlich sinnvoll.
- WorkItems/Hinweise bilden reale Uebergaben ab.

Zufaellig/fragil:

- README/Fachkonzept sprechen von drei Kontaktstufen inklusive anonym; im Code ist anonym ein Event-Flag, waehrend `Client.ContactStage` nur `IDENTIFIED`/`QUALIFIED` kennt. Das ist technisch erklaerbar, aber konzeptionell nicht sauber dokumentiert.
- `contact_stage`-Hilfetext "vollstaendige Identitaet bekannt" kollidiert mit "keine Klarnamen". Das ist gefaehrlich, weil es Organisationen in falsche Erfassungspraktiken schieben kann.
- JSONB-Flexibilitaet wird bei Reporting, Suche, Export und Historie zuerst brechen.

Wo bricht es zuerst: Datenlebenszyklus und Reporting. Sobald Einrichtungen eigene Felder intensiv nutzen und rechtssichere Loeschung, Auswertung und Export brauchen, wird `data_json` plus Historientabelle schwer beherrschbar.

## 3. Sicherheit und DSGVO

Es gibt ernsthafte Sicherheitsarbeit:

- Produktionssettings fail-closed fuer Secret Key, Allowed Hosts und Encryption Key.
- Feldverschluesselung per Fernet/MultiFernet.
- RLS mit `FORCE ROW LEVEL SECURITY` fuer zentrale Tabellen.
- Facility-Context wird pro Request in PostgreSQL gesetzt.
- Rollen-/Sensitivitaetsmodell existiert und wird fuer Events genutzt.
- MFA, CSP, Rate Limits, AuditLog, ClamAV-Integration und Offline-Verschluesselung sind vorhanden.

Trotzdem: Nein, ich wuerde diesem System heute keine echten sensiblen Sozialdaten im Produktivbetrieb anvertrauen.

Top 5 Sicherheitsrisiken:

1. Loeschung ist nicht echte Loeschung. `soft_delete_event` leert `Event.data_json`, laesst aber fruehere `EventHistory`-Snapshots bestehen. Retention erzeugt beim Loeschen sogar nochmals `data_before` mit vollstaendigen Daten.
2. Client-Anonymisierung loescht Event- und History-Inhalte nicht. `Client.anonymize` anonymisiert Client/Faelle/WorkItems, aber nicht die dokumentierten Event-Daten und Historien.
3. Soft-deleted Attachments bleiben abrufbar. Attachment-Download prueft Parent-Event, aber nicht `deleted_at`/`is_current`; die Liste filtert ebenfalls nicht sauber.
4. Produktionsbetrieb verliert oder beschaedigt Dateidaten. `docker-compose.prod.yml` mountet kein persistentes `MEDIA_ROOT`, obwohl `.env.example` `/data/media` vorsieht; Backups sichern nur DB, nicht Medien.
5. Mandantentrennung ist stark, aber nicht absolut. RLS hilft, aber Superuser/Admin/Management-Kommandos und falsch konfigurierte DB-Rollen umgehen die Produktgrenze.

Fail-open/Leak-Flaechen: JSONB-Suche, Exporte, EventHistory, Offline-Bundles, Attachment-Versionen, Admin, Backup/Restore, Healthcheck fuer ClamAV mit HTTP 200 bei Degradation.

## 4. Codequalitaet

Bewertung: kein Wegwerfprototyp, aber auch noch kein reifes Senior-Produkt. Eher: ambitionierter Pre-Release mit seniorigen Sicherheitsinseln und einigen gefaehrlichen Lebenszyklusluecken.

Stark:

- Konsistente Django-Nutzung.
- Viele fokussierte Tests, inklusive RBAC, RLS, Retention, File Vault, Offline und Architekturregeln.
- Gute Benennung zentraler Fachobjekte.
- Sicherheitsentscheidungen sind sichtbar dokumentiert.

Schwach:

- Grosse Service-Dateien: `services/retention.py` ca. 929 LOC, `services/event.py` ca. 661 LOC.
- `admin.py` ist gross und wirkt wie gewachsene Operator-Logik.
- Historie/Retention/Deletion sind nicht als ein harter, testbarer Datenschutz-Kern modelliert.
- Tests pruefen viel, aber uebersehen ausgerechnet physische Datenreste in History und Attachments.

## 5. Komplexitaet und technische Schulden

Top 5 Tech-Debt-Hotspots:

1. `services/retention.py`: Policy, Workflow, Bulk-Loeschung, Audit, Vorschlaege und History in einem Block.
2. `services/event.py`: Erstellung, Update, Datei-Mapping, Verschluesselung, Historie, Soft Delete.
3. `data_json` + `FieldTemplate`: flexibel, aber schwer sauber zu suchen, versionieren, exportieren und loeschen.
4. Offline-PWA: Kryptografie, IndexedDB, Queue, Conflict Handling und CSRF/MFA sind inhaerent komplex.
5. `core` als Gross-App: steigende kognitive Last fuer neue Beitragende.

Was zuerst unwartbar wird: Retention/Loeschung und Reporting, danach Offline-Konfliktlogik.

## 6. Entwicklererfahrung und Betrieb

DX: Mit Docker vermutlich in 1-2 Tagen startbar. Ohne Docker aktuell nicht trivial; in dieser Umgebung fehlten Testabhaengigkeiten und Docker.

Betrieb:

- Positiv: Dockerfile, Compose, Caddy, Migrations-Lock, CI, Backup-Skript, ClamAV.
- Negativ: Produktions-Compose nutzt `latest`, Medienpersistenz fehlt, Backup/Restore ist DB-only, Cron-Jobs fuer Retention/Snapshots sind extern zu organisieren.
- Fuer kleine NGOs ist das nicht realistisch ohne betreuenden Admin oder Managed-Angebot. Self-hosted ja, aber nicht "docker compose up und vergessen".

## 7. Datenmodell und Speicher

Staerken:

- Facility als klare Tenant-Achse.
- PostgreSQL-RLS ist eine starke Wahl.
- Constraints fuer Pseudonym-Eindeutigkeit, Deletion-Reviews und History-Immutability sind sinnvoll.
- JSONB passt zur Feldflexibilitaet kleiner Teams.

Risiken:

- JSONB erschwert belastbares Reporting und konsistente Migrationen.
- EventHistory speichert alte Zustaende und wird damit zum Datenschutzrisiko.
- Suche ueber `data_json__icontains` ist nicht skalierbar und schwer praezise abzusichern.
- Backup ohne Medien und Schluesselstrategie ist operativ unvollstaendig.

Skalierung: Fuer 5-20 Nutzer pro Einrichtung grundsaetzlich ausreichend. Reporting ueber viele Jahre und viele individuelle Felder wird schwierig.

## 8. Produkt- und UX-Denken

Ja: Das wurde von jemandem mit echter Domaenenkenntnis gebaut.

Begruendung:

- Pseudonym statt Name als Grundannahme.
- Anonyme Kontakte sind erstklassig modelliert.
- Zeitstrom/Kladde, WorkItems, flexible Dokumenttypen und Offline-Modus passen zu realer Sozialarbeit.
- Das System denkt nicht primaer in Aktenverwaltung, sondern in Kontakt- und Dokumentationsrealitaet.

Wo es gegen Nutzer arbeitet:

- Zu viel Sicherheits-/Betriebskomplexitaet landet beim Betreiber.
- "Kontaktstufe qualifiziert = vollstaendige Identitaet bekannt" ist sprachlich gefaehrlich.
- Freie Felder geben Teams Freiheit, aber ohne Governance entstehen schlechte Daten und schlechte Auswertungen.
- Offline-Modus ist nuetzlich, aber fuer kleine Teams organisatorisch schwer sicher zu betreiben.

## 9. Langfristige Tragfaehigkeit

Potenzial: ja, ernsthaftes Open-Source-Projekt moeglich.

Aber nur mit harter Priorisierung:

- Datenschutz-Lebenszyklus muss Kernarchitektur werden, nicht Feature.
- `core` muss fachlich zerlegt werden.
- Betrieb muss produktfaehig dokumentiert und automatisiert werden.
- Reporting braucht eine robustere Faktenschicht statt ad-hoc JSON-Auswertung.

Ohne diese Schritte kollabiert es nicht an CRUD-Komplexitaet, sondern an Retention, Offline, Export, Audit und Feldflexibilitaet.

## 10. Schonungslose Gesamtbewertung

Gesamtbewertung: 6,5/10.

- Einsetzen: nicht produktiv mit echten sensiblen Sozialdaten in der aktuellen Form.
- Investieren: ja, bedingt, wenn zuerst Datenschutz und Betrieb finanziert werden, nicht neue Features.
- Darauf aufbauen: ja, aber nur nach Security-/Data-Lifecycle-Sprint.

Einzelbewertung:

- Architektur: 7/10
- Fachmodell: 8/10
- Codequalitaet: 6,5/10
- Security/DSGVO produktiv: 5/10
- Betrieb: 5/10

## Quick Wins

- Attachment-Download/List strikt auf `deleted_at IS NULL` und `is_current=True` begrenzen; Tests ergaenzen.
- Retention so aendern, dass beim Loeschen kein vollstaendiges `data_before` mehr in `EventHistory` geschrieben wird.
- Medienvolume und Medienbackup in `docker-compose.prod.yml`, `backup.sh`, `restore.sh` aufnehmen.
- Healthcheck bei aktivem, nicht verfuegbarem ClamAV mit 503 statt nur "degraded" behandeln.
- Doku korrigieren: Fernet vs. AES-GCM, Kontaktstufen, Admin-Grenzen, Produktionsreife.

## High-Impact-Refactorings

- Fachliche Apps trennen: `tenancy`, `clients`, `documentation`, `files`, `retention`, `reporting`, `offline`.
- Einen zentralen `DataLifecycleService` bauen: create/update/history/delete/anonymize/export/offline aus einer Policy.
- JSONB-Daten fuer Reporting in eine versionierte Faktentabelle oder Materialized Views ueberfuehren.
- Admin als Operator-Konsole klar von Produktrollen trennen.

## Erste 3 Massnahmen bei Uebernahme morgen

1. Security-Freeze fuer Loeschung/Retention/History: Datenfluss kartieren, EventHistory-Scrubbing designen, Retention-Loeschung korrigieren, Tests fuer physische Datenreste schreiben.
2. File Vault produktionsfaehig machen: Attachment-Zugriff fixen, persistentes Medienvolume, Medienbackup, Restore-Test, Schluessel-/Disaster-Recovery-Doku.
3. Architekturgrenzen haerten: Event/Retention/Export/Offline hinter einen gemeinsamen Policy-Layer ziehen und jede Zugriffsstufe gegen Facility, Rolle, Sensitivitaet, Loeschstatus und Offline-Freigabe testen.
