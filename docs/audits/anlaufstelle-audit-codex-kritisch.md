# Kritischer System-Audit: `anlaufstelle/app`

**Repository:** `https://github.com/anlaufstelle/app` 
**Prüfstand:** Commit `35e0f5b31e972c7345940433c572372d6b1736ee` (`chore: Release v0.10.2`, `2026-04-28 15:07:57 +0100`) 
**Datum der Analyse:** 2026-04-28 
**Kontext:** Open Source, Self-Hosted, niedrigschwellige Sozialarbeit, pseudonymisierte Klientenverwaltung, Dokumentation, Berichtswesen, DSGVO/Art.-9-nahe Daten.

## Kurzurteil

Das System ist kein liebloser CRUD-Prototyp. Es ist fachlich ernst gemeint, kennt die Domäne sichtbar und hat für ein Pre-Release erstaunlich viel Sicherheits- und Betriebsdenken: Rollenmodell, Sensitivitätsstufen, Feldverschlüsselung, RLS, Audit-Log, ClamAV, Retention, Offline-Krypto, CI, Tests und Dokumentation.

Aber: Für echten Produktivbetrieb mit sensiblen Sozialdaten würde ich es in diesem Zustand **nicht ohne harte Nacharbeit freigeben**. Die größte Schwäche ist nicht Django, nicht HTMX und nicht der Monolith. Die größte Schwäche ist, dass Datenschutzlogik über mehrere Ebenen verteilt ist und nicht überall dieselbe Härte hat. Besonders kritisch sind Klartext-Freitextfelder außerhalb des verschlüsselten dynamischen Feldmodells, Offline-Caches mit entschlüsselten Daten, facility-scoping als Konvention, Betriebsartefakte ohne persistentes Medienvolume und Dokumentation, die an mehreren Stellen stärker klingt als der Code ist.

**Gesamtbewertung:** 6.5 / 10 
**Produktstatus:** starkes Pre-Production-System, pilotfähig unter Aufsicht, nicht beschaffungs- oder produktionsreif für sensible Echtdaten ohne gezielte Härtung.

## Prüfumfang und Grenzen

Geprüft wurden Code, Modelle, Services, Views, Middleware, Einstellungen, Docker-Dateien, CI, Dokumentation und exemplarische Tests. Keine externen Annahmen wurden als Fakt behandelt.

Lokale Verifikation:

- `ruff check src`: bestanden.
- `ruff format --check src`: bestanden, 297 Dateien formatiert.
- `python src/manage.py check --settings=anlaufstelle.settings.dev`: nicht ausführbar auf dem Host, weil WeasyPrint `libpango-1.0-0` nicht laden konnte; zusätzlich weist die Dev-Konfiguration ohne `ENCRYPTION_KEY` auf unverschlüsselte Felder hin.
- `pytest src/tests/test_architecture.py -q`: 12 bestanden, 1 fehlgeschlagen, 1 Fehler. Fehlerursachen: kein lokaler PostgreSQL auf `localhost:5432`, WeasyPrint/Pango fehlt.
- Docker konnte nicht ausgeführt werden (`docker: command not found`), daher kein vollständiger Compose-/E2E-Lauf.

Diese Einschränkungen ändern die Architekturbewertung nicht, begrenzen aber die Aussage zu vollständiger Laufzeitkorrektheit.

---

# 1. Systemarchitektur

## Architekturbeschreibung

Das System ist ein **Django-basierter modularer Monolith** mit serverseitig gerendertem UI:

- Backend: Django 5.1+, Python 3.13, eine dominante App `core`.
- Frontend: Django Templates, HTMX, Alpine.js, Tailwind, PWA-/Offline-JavaScript.
- Datenbank: PostgreSQL 16, UUID-Primärschlüssel, JSONField/JSONB, RLS-Migrationen, Trigram-Suche, Materialized-View-Ansatz für Statistik.
- Sicherheitsgrenzen: Django-Permissions/Mixins, Rollen/Sensitivitätsservices, `FacilityScopeMiddleware`, Postgres-RLS, Audit-Log, Verschlüsselungsservice.
- Betriebsmodell: Docker Compose, Caddy, ClamAV, optionale Sentry-Integration.

Textuelles Architekturmodell:

```text
Browser
  -> Django Templates / HTMX / PWA JS
    -> Views + Forms + Mixins
      -> Services (Events, Retention, Export, File Vault, Offline, Statistics)
        -> Models / Managers
          -> PostgreSQL + RLS + JSONB + encrypted attachment files
```

Das ist für kleine Teams grundsätzlich die richtige Richtung. Ein Microservice-Ansatz wäre hier Overengineering. Der Monolith passt zur Teamgröße, zur Self-Hosted-Anforderung und zur fachlichen Enge der Domäne.

## Stärken

- **Pragmatischer Stack.** Django + PostgreSQL + serverseitiges Rendering ist für kleine NGOs realistischer als eine verteilte SPA/API-Plattform.
- **Sicherheitsmechanismen sind nicht nachträglich dekoriert.** Produktionseinstellungen fail-closed bei `DJANGO_SECRET_KEY`, `ALLOWED_HOSTS` und `ENCRYPTION_KEY(S)` (`src/anlaufstelle/settings/prod.py:37-44`, `src/anlaufstelle/settings/prod.py:94-101`).
- **Defense-in-depth ist sichtbar.** `FacilityScopeMiddleware` setzt `request.current_facility` und die Postgres-Session-Variable `app.current_facility_id` (`src/core/middleware/facility_scope.py:34-55`). RLS ist damit nicht nur Dokumentation, sondern Teil des Datenbankmodells.
- **Service Layer existiert.** Es gibt dedizierte Services für Events, Retention, Exporte, Offline-Bundles, File Vault, Sensitivität, Statistik, DSGVO-Paket usw.
- **Fachliche Konzepte sind kodiert.** `DocumentType`, `FieldTemplate`, `Event`, `Client`, `Case`, `WorkItem`, `RetentionProposal`, `LegalHold`, `AuditLog` bilden reale Begriffe ab.
- **CI ist ernst gemeint.** Tests, Django checks, migration drift check, `pip-audit`, lock checks laufen in GitHub Actions (`.github/workflows/test.yml:33-93`).

## Schwächen

- **`core` ist zu groß.** Praktisch alles liegt in einer App. Die Package-Unterteilung ist nützlich, aber keine echte Domänentrennung. `core` enthält Auth, Klienten, Events, Retention, Statistik, Exporte, Offline, MFA, Admin, Audit, WorkItems und Settings.
- **Service Layer ist inkonsistent.** Manche Pfade sind sauber serviceorientiert, andere lassen Sicherheits- und Facility-Checks in Views, Forms und Services verstreut. Beispiel: `WorkItemStatusUpdateView` nutzt `can_user_mutate_workitem`, `WorkItemUpdateView` tut das nicht (`src/core/views/workitem_actions.py:119-161`).
- **Facility-Scoping ist keine harte typisierte Grenze.** `FacilityScopedManager` bietet nur `.for_facility`, erzwingt aber nichts (`src/core/models/managers.py:6-21`). Jede Query muss daran denken. RLS hilft, ersetzt aber saubere App-Invarianten nicht.
- **Security-Policy ist verteilt.** Rollen, Sensitivität, Kontaktstufen, Retention, Offline, Export, Audit und Facility-Scope sind in vielen Modulen implementiert. Das erhöht das Risiko, dass neue Features an einer Stelle am Policy-System vorbei gebaut werden.
- **Dokumentationsdrift.** Security Notes verweisen auf eine Audit-Datei, die im Repository nicht vorhanden ist (`docs/security-notes.md:1-56`). Admin-/User-Dokumentation behauptet für Dateianhänge mehrfach AES-GCM, der Code nutzt Fernet/MultiFernet-Chunk-Verschlüsselung (`src/core/services/encryption.py`, `docs/admin-guide.md:545-554`).

## Risiko-Level

**Mittel.** Die Grundarchitektur ist für die Zielgruppe richtig. Das Risiko liegt nicht im Architekturstil, sondern in der wachsenden Komplexität innerhalb eines zu breiten `core` und in uneinheitlich durchgesetzten Sicherheitsinvarianten.

---

# 2. Domänenmodell & Konzept

## Kernkonzepte

### Klienten / Pseudonyme

`Client` ist pseudonymisiert modelliert: UUID, Facility, Pseudonym, Kontaktstufe, Alterscluster, Notizen, Aktivstatus (`src/core/models/client.py:13-78`). Das Pseudonym ist je Facility eindeutig (`src/core/models/client.py:83-87`). Es gibt kein Namensfeld im `Client`-Modell.

Das ist fachlich richtig für niedrigschwellige Arbeit. Der Code erzwingt aber nur, dass es kein dediziertes Namensfeld gibt. Er verhindert nicht, dass Nutzer Klarnamen oder identifizierende Details in `Client.notes`, `Case.description`, `WorkItem.description` oder Event-Freitextfelder schreiben.

### Events / Dokumentation

`Event` ist das zentrale Dokumentationsobjekt (`src/core/models/event.py:12-67`). Es verbindet Facility, optionalen Client, Dokumentationstyp, Fall, Episode, Zeitpunkt, JSON-Daten und Löschstatus. Die dynamischen Felder laufen über `DocumentType` und `FieldTemplate`; verschlüsselte Felder werden beim Speichern anhand der Feldvorlage verschlüsselt (`src/core/models/event.py:86-112`).

Das ist fachlich elegant: Die Einrichtung kann ihre Arbeitsrealität konfigurieren, ohne Code zu schreiben.

### Dokumentationstypen / Felder

`DocumentType` besitzt Kategorie, Sensitivität, Mindest-Kontaktstufe, Retention und Systemtyp (`src/core/models/document_type.py:18-92`). `FieldTemplate` besitzt Feldtyp, Required-Flag, Verschlüsselungsflag, Feldsensitivität, Optionen und Statistikzuordnung (`src/core/models/document_type.py:113-191`). Slugs sind stabil und werden geschützt (`src/core/models/document_type.py:269-300`).

Das ist ein guter Kompromiss zwischen Fachlichkeit und Konfigurierbarkeit. Der Preis ist schwächere Datenintegrität und schwierigere Auswertbarkeit, weil reale Nutzdaten in `Event.data_json` liegen.

### Einrichtungen / Multi-Tenancy

Fast alle fachlichen Tabellen haben `facility`. RLS wird per Migration eingerichtet. Das entspricht einem Single-DB-Multi-Tenant-Modell mit Facility als Mandant. Für kleine Teams ist das realistisch.

Schwach ist die Ebene über Facility: `Organization` existiert, wirkt aber nicht als starke fachliche oder technische Grenze. Die Dokumentation spricht teils von Organisationsbezug, der Code bleibt facility-zentriert. Ein Träger mit mehreren Standorten wird daran zuerst Schmerzen haben.

## Fachliche Sauberkeit

**Teilweise sauber, teilweise gewachsen.**

Sauber:

- Ereigniszentrierte Dokumentation passt zur Praxis.
- Anonyme Events ohne Client sind richtig.
- Kontaktstufen `identified` und `qualified` plus anonyme Events treffen die Domäne besser als ein generisches Personenmodell.
- Retention auf Kontaktstufe und Dokumentationstyp ist fachlich sinnvoll.
- WorkItems als Teamkommunikation sind als eigene Domäne vorhanden, nicht in Events versteckt.

Gewachsen:

- `Client.notes` ist ein fachliches Sonderfeld außerhalb des dynamischen, sensitivitäts- und verschlüsselungsfähigen Event-Feldsystems (`src/core/models/client.py:54-58`).
- `Case.description`, `Episode.description` und `WorkItem.description` sind ebenfalls Klartext-Freitexte außerhalb des einheitlichen Datenschutzmodells (`src/core/models/case.py:36-37`, `src/core/models/workitem.py:89-90`).
- WorkItems haben keine Sensitivitätsstufe, obwohl sie in der Praxis häufig genau die sensiblen Hinweise enthalten werden, die nicht in eine formale Doku passen.
- Das Modell verhindert keine falsch verknüpften fachlichen Situationen auf DB-Ebene, etwa Event-Fall-Client-Konsistenz nur über Service/Form-Konventionen.

## Entspricht es realen Arbeitsabläufen?

Ja, sichtbar. Zeitstrom, Kurzereignisse, anonyme Kontakte, Pseudonymregister, Berichtswesen, Hausverbote, Aufgaben, Übergaben, Jugendamtsbericht und Offline-Modus sind keine generischen SaaS-Bausteine. Das wurde von jemandem gebaut, der die Realität niedrigschwelliger Sozialarbeit kennt.

## Elegant oder zufällig gewachsen?

**Konzeptionell elegant, in der Umsetzung zunehmend organisch gewachsen.** Das zentrale `Event`-Modell mit konfigurierbaren Feldern ist stark. Die Nebenmodelle wirken dagegen wie spätere Realitätsanpassungen: Fälle, Episoden, Aufgaben, Outcomes, Milestones, Quick Templates, Offline-Leases, Snapshots, Retention-Proposals. Das ist nicht falsch, aber ohne stärkere Domänenmodule wird es schnell schwer kontrollierbar.

## Wo bricht es zuerst?

1. **Bei sensiblen Freitexten außerhalb von Events.** Nutzer werden kritische Informationen in Notizen, Fallbeschreibungen und Aufgaben schreiben. Diese Daten folgen nicht konsequent demselben Sensitivitäts-/Verschlüsselungs-/Retention-Modell.
2. **Bei Reporting über flexible JSON-Felder.** Was heute flexibel ist, wird bei Förderlogik, Vergleichbarkeit und Auswertungen schnell teuer.
3. **Bei Multi-Facility-Trägern.** Facility ist stark, Organization ist schwach. Standortübergreifende Verantwortlichkeit, Reports und Rechte werden schwieriger.
4. **Beim Offline-Modus.** Offline-Lesen, Offline-Schreiben, Konflikte, Verschlüsselung, Session-Key und UX sind eine eigene Produktlinie innerhalb des Produkts.

---

# 3. Sicherheit & DSGVO

## Vorhandene Mechanismen

### Verschlüsselung

- Feldverschlüsselung über Fernet/MultiFernet; verschlüsselte Werte werden in `Event.save` automatisch behandelt (`src/core/models/event.py:86-112`).
- Produktion erzwingt `ENCRYPTION_KEY` oder `ENCRYPTION_KEYS` (`src/anlaufstelle/settings/prod.py:94-101`).
- Dateianhänge werden vor Ablage geprüft und verschlüsselt gespeichert (`src/core/services/file_vault.py:258-333`).
- Offline-Daten werden serverseitig gefiltert, dann im Browser clientseitig verschlüsselt gespeichert; verschlüsselte Event-Felder werden dafür serverseitig entschlüsselt (`src/core/services/offline.py:86-91`).

### Pseudonymisierung

- Kein dediziertes Namensfeld im `Client`.
- Facility-weite Eindeutigkeit des Pseudonyms.
- Anonymisierung ersetzt Pseudonym und löscht Notizen, Alterscluster, Fälle, Episoden und WorkItems (`src/core/models/client.py:100-140`).

### Zugriffskontrolle

- Rollenmodell und Sensitivitätsmodell.
- `EventQuerySet.visible_to(user)` filtert Dokumentationstypen nach erlaubter Sensitivität (`src/core/models/managers.py:24-39`).
- Facility-Kontext in Middleware plus Postgres-RLS.
- Lead/Admin-only für Exporte und sensible Admin-Funktionen.
- MFA, Rate Limits und Lockout-Services sind vorhanden.

### Audit

- `AuditLog` ist append-only im Python-Modell (`src/core/models/audit.py:104-112`) und laut Migration zusätzlich per DB-Trigger geschützt.
- Audit-Actions sind umfangreich modelliert (`src/core/models/audit.py:15-47`).

## Fail-Open-Szenarien

1. **Dev ohne Encryption Key speichert unverschlüsselt.** Das ist für Entwicklung akzeptabel, aber die Warnung zeigt: Die Anwendung kann grundsätzlich ohne Feldverschlüsselung laufen. In Prod wird das blockiert, in falsch gesetzten Umgebungen muss man aufpassen.
2. **Service-Funktionen sind nicht durchgängig selbstschützend.** `export_client_data` sammelt Events, Cases, History, DeletionRequests und WorkItems aus einem `client`, ohne Facility als harte Query-Grenze in jeder Teilquery zu erzwingen (`src/core/services/client_export.py:41-121`). Der View-Pfad schützt das, der Service selbst nicht vollständig.
3. **Facility-Scoping ist freiwillig im ORM.** `.for_facility` ist eine Convenience-Methode, keine verpflichtende API (`src/core/models/managers.py:6-21`).
4. **WorkItem-Edit-Policy ist inkonsistent.** Status-Updates prüfen `can_user_mutate_workitem`, der volle Edit-Pfad für Staff prüft sie nicht (`src/core/views/workitem_actions.py:29-48`, `src/core/views/workitem_actions.py:119-161`).
5. **Offline-Modus verschiebt Vertrauen auf Browser/Endgerät.** Ein XSS in Origin-Kontext oder ein kompromittiertes Gerät kann während aktiver Session an entschlüsselte Offline-Nutzdaten kommen. Non-extractable WebCrypto-Key reduziert Schlüsselabgriff, verhindert aber nicht API-Missbrauch durch XSS im selben Kontext.

## Datenlecks: Suche, Export, Logs

### Suche

Die Suche filtert nach Sensitivität und überspringt verschlüsselte/restriktive Felder. Das ist gut. Performance-seitig ist JSON-Suche aber fragil und wird mit wachsendem Datenbestand schlechter. Datenschutzseitig ist das Hauptproblem nicht die Suche selbst, sondern dass Klartext-Freitextfelder außerhalb des Feldsystems existieren.

### Export

`export_events_csv` filtert Felder nach User-Sensitivität, holt aber zunächst alle Events der Facility im Zeitraum (`src/core/services/export.py:21-32`, `src/core/services/export.py:131-150`). Der aktuelle View ist Lead/Admin-only, deshalb ist das kein unmittelbarer Rollendurchbruch. Als Service-API ist es dennoch fragil: Wiederverwendung mit niedrigerer Rolle wäre riskant.

`export_client_data` entschlüsselt Event-Felder bewusst für Betroffenenrechte (`src/core/services/client_export.py:24-38`). Das ist fachlich nötig, aber extrem sensibel. Diese Funktion muss als Hochrisiko-Pfad behandelt werden.

### Logs / Audit

`AuditLog.detail` ist ein JSONField im Klartext (`src/core/models/audit.py:83`). Audit-Details enthalten Pseudonyme, Gründe, Dateinamen, Retention-Kategorien, Security-Violations und ggf. menschlich eingegebene Begründungen. Audit-Logs sind unveränderlich, aber nicht automatisch datensparsam. Append-only plus Klartext-Detail ist datenschutzrechtlich ein eigenes Risiko.

## Mandantentrennung

Die Mandantentrennung ist besser als bei vielen kleinen Projekten:

- Facility-FKs sind breit vorhanden.
- Middleware setzt Facility-Kontext.
- Postgres-RLS ist eingebaut.
- Tests für Scoping/RLS existieren.

Aber sie ist nicht wasserdicht auf Anwendungsebene:

- Der Manager erzwingt Scoping nicht.
- Nicht jede Servicefunktion ist self-contained sicher.
- `core_user` bleibt bewusst RLS-frei; das ist für Login/Admin erklärbar, aber eine permanente Ausnahme.
- RLS schützt nur, wenn Produktions-DB-Rollen, Connection-Setup, `FORCE ROW LEVEL SECURITY` und Session-Variable korrekt betrieben werden.

## Würde ich diesem System echte sensible Sozialdaten anvertrauen?

**Heute: nein, nicht im produktiven Regelbetrieb.**

Ich würde es für Pilotbetrieb mit eng begrenztem Datenumfang, geschultem Team, deaktiviertem oder streng kontrolliertem Offline-Modus, geprüften Backups, überprüfter RLS-Konfiguration und manueller Security-Begleitung einsetzen. Aber ich würde es nicht morgen einer kleinen NGO als "DSGVO-sicheres Self-Hosted-System" für echte sensible Langzeitdaten hinstellen.

Der Grund ist nicht, dass keine Security vorhanden ist. Der Grund ist, dass Security zu stark von korrekter Nutzung und korrektem Betrieb abhängt.

## Top 5 Sicherheitsrisiken

### 1. Klartext-Freitext außerhalb des geschützten Feldmodells

Beispiele:

- `Client.notes` (`src/core/models/client.py:54-58`)
- `Case.description` (`src/core/models/case.py:36-37`)
- `WorkItem.description` (`src/core/models/workitem.py:89-90`)
- `AuditLog.detail` (`src/core/models/audit.py:83`)

Diese Felder sind genau dort, wo echte Sozialdaten landen: "hat konsumiert", "psychische Krise", "Gewalt", "Aufenthaltsort", "Kontakt zu Amt", "Schulden", "Name nur intern". Das System sagt "keine Klarnamen", aber diese Felder erlauben jede identifizierende Information.

**Risiko:** hoch.

### 2. Offline-Cache mit entschlüsselten sensiblen Daten

`build_client_offline_bundle` entschlüsselt serverseitig verschlüsselte Felder für den Browser (`src/core/services/offline.py:86-91`) und nimmt je nach Rolle auch Client-Notizen auf (`src/core/services/offline.py:176-195`). Die Browser-Verschlüsselung ist technisch ordentlich, aber das Threat Model ändert sich massiv: Daten liegen auf Endgeräten und sind während aktiver Session entschlüsselbar.

**Risiko:** hoch.

### 3. Facility-Scoping als Konvention plus RLS als Rettungsnetz

`FacilityScopedManager` erzwingt nichts (`src/core/models/managers.py:6-21`). RLS ist gut, aber eine zweite Verteidigungslinie, nicht die primäre Architektur. Services wie `client_export` oder generische Exportpfade zeigen, dass nicht jeder Datenzugriff sauber als Policy-geschützte API modelliert ist.

**Risiko:** mittel bis hoch.

### 4. Betriebsrisiko File Vault / Persistenz

`docker-compose.prod.yml` definiert persistente Volumes für Postgres, Caddy und ClamAV, aber nicht für den Web-Container bzw. `MEDIA_ROOT` (`docker-compose.prod.yml:16-37`, `docker-compose.prod.yml:69-73`). Gleichzeitig werden verschlüsselte Anhänge unter `MEDIA_ROOT/<facility_id>/<uuid>.enc` abgelegt (`src/core/services/file_vault.py:22-24`, `src/core/services/file_vault.py:294-333`).

Wenn ein Betreiber sich an diese Compose-Datei hält, sind Dateianhänge potenziell nicht dauerhaft persistiert. Für Sozialdokumentation ist das ein harter Betriebsfehler.

**Risiko:** hoch.

### 5. Inkonsistente Mutationsrechte bei WorkItems

Die dokumentierte Policy sagt: Lead/Admin, Ersteller oder Zugewiesene dürfen mutieren (`src/core/views/workitems.py:26-33`). Status-Update und Bulk prüfen das, der vollständige WorkItem-Edit-Pfad nur `StaffRequiredMixin` (`src/core/views/workitem_actions.py:119-161`). Damit kann jede Fachkraft potentiell Aufgaben anderer bearbeiten, sofern sie Staff ist.

**Risiko:** mittel. Nicht direkt Mandantendurchbruch, aber Rechte- und Auditmodell sind inkonsistent.

---

# 4. Codequalität

## Lesbarkeit & Konsistenz

Der Code ist überwiegend gut lesbar. Naming ist meist fachlich klar. Es gibt viele erklärende Kommentare, teilweise hilfreich, teilweise Zeichen gewachsener Komplexität. Viele Kommentare enthalten interne Referenzen wie `Refs #...`; das ist gut für Historie, aber ohne Issue-Kontext für neue Maintainer begrenzt nützlich.

Der Stil ist konsistent genug, dass `ruff` und `ruff format --check` sauber durchlaufen.

## Framework-Nutzung

Django wird grundsätzlich solide genutzt:

- Models mit Constraints und Indexen.
- Forms für Validierung.
- Class-based Views und Mixins.
- Management Commands.
- Settings-Splitting für Base/Dev/Prod.
- PostgreSQL-spezifische Features dort, wo sie sinnvoll sind.

Problematisch ist nicht die Framework-Nutzung, sondern die Menge an Policy-Logik oberhalb des Frameworks. Django Permissions sind nicht die zentrale Wahrheit; es gibt Mixins, Servicefunktionen, Manager, Sensitivity-Checks, RLS, Templates und Tests. Das funktioniert nur, solange das Team sehr diszipliniert bleibt.

## Testqualität

Die Testoberfläche ist für ein Pre-Release stark:

- Unit-/Integrationstests für viele Domänen.
- E2E-Tests mit Playwright.
- RLS-, RBAC-, Scope-, Export-, Retention-, Offline-, File-Vault- und Audit-Tests.
- CI mit Postgres, Coverage, Django checks, Migration check, `pip-audit`.

Schwächen:

- Lokale Host-Ausführung ist fragil, weil WeasyPrint-Systemlibs hart beim Import gebraucht werden.
- Viele Tests scheinen implementation-driven und regressionsorientiert. Das ist gut gegen bekannte Bugs, aber kein Ersatz für ein klares Security-Policy-Modell.
- Vollständige Tests brauchen Postgres/Docker/Systemlibs; für neue Beitragende ist das schwerer als README "docker compose up" vermuten lässt.

## Senior-Level oder Prototyp?

**Advanced Prototype / Pre-Production, nicht Wegwerfprototyp.**

Es gibt Senior-Entscheidungen: RLS, append-only Audit, fail-closed Prod, ClamAV fail-closed, Field-Level Sensitivity, Key Rotation, Retention, Lockout, CI. Gleichzeitig gibt es prototypische Brüche: eine übergroße Core-App, inkonsistente Policy-Grenzen, Docs-Drift, Klartext-Sonderfelder und ein sehr komplexer Offline-Modus.

## Wo ist der Code am schwächsten?

1. Security-/Access-Policy verteilt über zu viele Stellen.
2. Retention und Datenschutz-Lifecycle als großer prozeduraler Service.
3. Offline-Subsystem als eigene Komplexitätsinsel.
4. Exporte und PDF-Generierung mit harten Imports und hohem Datenrisiko.
5. WorkItems als praktische, aber datenschutzrechtlich untermodellierte Nebenwelt.

---

# 5. Komplexität & technische Schulden

## Hotspot-Größen

Ausgewählte Dateien:

- `src/core/services/retention.py`: 929 Zeilen.
- `src/core/services/event.py`: 661 Zeilen.
- `src/core/views/events.py`: 472 Zeilen.
- `src/core/services/file_vault.py`: 395 Zeilen.
- `src/tests/test_events.py`: 1305 Zeilen.
- Offline-JS-Kern: `offline-store.js` 346, `offline-edit.js` 215, `crypto.js` 255 Zeilen.

Diese Zahlen sind nicht automatisch schlecht. Aber sie zeigen, wo Risiko und Änderungsangst entstehen werden.

## Top 5 Tech-Debt-Hotspots

### 1. Retention-Lifecycle

`retention.py` kombiniert Dashboard-Kontext, Proposal-Status, Legal Holds, Löschlogik, Anonymisierung, Audit und Query-Strategien. Das ist fachlich kritisch und zu breit. Fehler hier sind DSGVO-relevant.

### 2. Event-Service und dynamische Felder

Events sind der Kern. Dynamische JSON-Felder, FieldTemplate, Sensitivität, Encryption, Files, Cases, Episodes, Histories und Exporte hängen daran. Jede Änderung kann ungewollt Datenschutz, UI, Reporting und Offline berühren.

### 3. Offline-Modus

Offline-Cache, Offline-Edit, Konfliktlösung, Crypto-Session, Service Worker, IndexedDB und Server-Bundles bilden ein zweites Produkt im Produkt. Für Streetwork ist das wertvoll, aber es vervielfacht Security- und Supportaufwand.

### 4. Facility-Scoping-Konvention

Es gibt Tests und RLS, aber keine harte Domänen-API, die Queries zwingend facility-gebunden macht. Das wird bei neuen Features zuerst zu Fehlern führen.

### 5. Dokumentationsdrift

Die Dokumentation ist umfangreich, aber nicht immer deckungsgleich mit dem Code:

- README sagt Pre-Release, Fachkonzept klingt teils wie v1.0.
- Security Notes verweisen auf nicht vorhandenes Audit-Dokument.
- Datei-Vault-Dokumentation sagt AES-GCM, Code nutzt Fernet.
- Dokumentation spricht von NPM-Audit-ähnlicher Vollständigkeit, CI auditiert nur Python-Abhängigkeiten.

## Was wird zuerst unwartbar?

**Das Zusammenspiel aus Event-JSON, Sensitivität, Verschlüsselung, Reporting und Offline.**

Das ist der zentrale Änderungsdruck: Einrichtungen wollen neue Felder, andere Reports, andere Rollen, Offline-Fähigkeit, Löschfristen, Exportformate. Genau dort ist die meiste Magie. Ohne stärkere Modulgrenzen und Policy-APIs wird jede Produktänderung riskant.

---

# 6. Entwicklererfahrung & Betrieb

## Setup

README beschreibt den Einstieg über Docker Compose (`README.md:165-181`). Das ist richtig. Ohne Docker ist lokales Setup deutlich schwerer: Python-Abhängigkeiten reichen nicht, weil PostgreSQL, WeasyPrint-Systemlibs, libmagic und ggf. ClamAV gebraucht werden.

In dieser Umgebung:

- Python-Venv und Dependencies konnten installiert werden.
- Ruff lief.
- Django check scheiterte an fehlender Pango-Library.
- Tests scheiterten ohne lokalen PostgreSQL.
- Docker war nicht verfügbar.

## Ist man in 1-2 Tagen produktiv?

**Als erfahrener Django-Entwickler mit Docker: ja, wahrscheinlich.** 
**Als externe Open-Source-Contributor ohne Domänenwissen: eingeschränkt.** 
**Als kleine NGO ohne technische Betreuung: nein, nicht verantwortungsvoll.**

Der Code ist nicht unlesbar, aber die Security- und Betriebsinvarianten sind zu wichtig, um "einfach mal" zu ändern oder zu hosten.

## Deployment

Positiv:

- Dockerfile mit Runtime-User.
- Prod-Settings fail-closed.
- Caddy im Prod-Compose.
- ClamAV-Service.
- Healthcheck.

Kritisch:

- `docker-compose.prod.yml` nutzt `ghcr.io/anlaufstelle/app:latest`, während das geprüfte Repository `anlaufstelle/app` heißt (`docker-compose.prod.yml:16-18`). Das ist mindestens Branding-/Governance-Drift, potenziell auch Supply-Chain-Verwirrung.
- Kein persistentes Volume für `MEDIA_ROOT` in `docker-compose.prod.yml`.
- Keine eingebaute Backup-/Restore-Automation für DB plus Medien.
- Betrieb von Schlüsselrotation, Backups, Restore-Tests, ClamAV-Ausfällen, Cronjobs und Monitoring wird auf Betreiber verlagert.

## Logging & Monitoring

Positiv:

- Prod setzt Core-Logger auf INFO statt DEBUG (`src/anlaufstelle/settings/prod.py:14-19`).
- Sentry optional mit `send_default_pii=False` (`src/anlaufstelle/settings/prod.py:21-29`).
- AuditLog ist umfangreich.

Fehlt oder bleibt extern:

- Metriken/Monitoring nicht produktisiert.
- Kein sichtbares Admin-Dashboard für Backupstatus, Cronstatus, ClamAV-Status, RLS-Status.
- AuditLog wächst append-only; Archivierungs-/Retentionstrategie für Auditdaten ist nicht klar hart umgesetzt.

## Ist das für kleine NGOs realistisch betreibbar?

**Nur mit technischer Betriebsunterstützung.**

Self-hosted heißt hier nicht "installieren und vergessen". Es braucht:

- Key Management.
- Backups von DB und Medien.
- Restore-Tests.
- TLS/Reverse Proxy.
- ClamAV-Betrieb.
- Cronjobs für Retention, Snapshots, Orphan-Cleanup.
- Monitoring und Patch-Management.

Ohne Managed-Angebot oder klare Ops-Automation ist das für viele kleine NGOs zu viel.

---

# 7. Datenmodell & Speicher

## Datenbankdesign

Das Modell nutzt PostgreSQL angemessen:

- UUIDs.
- Facility-FKs.
- Constraints für Pseudonym-Eindeutigkeit, FieldTemplate-Slugs, DeletionRequests.
- Indizes für Event-/Client-/WorkItem-/Audit-Pfade.
- Trigram-Index für Pseudonym-Suche.
- RLS-Migrationen.
- Materialized-View-/Snapshot-Ansätze für Statistik.

Für die Zielgröße 5-20 Nutzer je Einrichtung ist das skalierbar genug.

## JSON/JSONB-Nutzung

`Event.data_json` ist der zentrale Flexibilitätsanker (`src/core/models/event.py:62-63`). Für die Domäne ist das verständlich: Einrichtungen sollen Felder selbst definieren können.

Risiken:

- Weniger DB-Constraints auf echte Werte.
- Schwierige Migrationen bei Feldsemantik.
- Reporting wird kompliziert und langsam.
- Suche über JSON ist begrenzt.
- Feld-Sensitivität muss außerhalb der DB interpretiert werden.

Die Implementierung mildert das teilweise durch `FieldTemplate`, Slug-Schutz, Statistik-Kategorien und Exporte. Trotzdem bleibt JSONB der Ort, an dem langfristig Fachlichkeit und Datenschutz am meisten Reibung erzeugen.

## Migrationen

Viele Migrationen zeigen aktives Wachstum und nachgezogene Härtung: RLS, Audit-Trigger, File Vault, MFA, Optimistic Locking, K-Anonymisierung, Statistiken, Quick Templates, WorkItem-Rekurrenz. Das ist gut, aber auch ein Signal, dass die Domäne noch stark in Bewegung ist.

## Performance

Für kleine Teams reicht das vermutlich. Schwachstellen:

- JSON-Suche.
- Event-Listen mit dynamischen Feldern.
- Retention über große Querysets und mehrere Strategien.
- Exporte/PDF-Generierung im Request-Kontext, falls nicht streaming/asynchron genug.
- Offline-Bundle-Aufbereitung mit Entschlüsselung.

## Reporting-Fähigkeit

Es gibt echte Reporting-Gedanken: Statistik-Kategorien, Snapshots, Jugendamtsbericht, CSV/PDF. Das ist stärker als generische CRUD-Apps.

Aber Förder- und Trägerberichte werden langfristig standardisierte Fakten brauchen. Dynamische JSON-Felder sind dafür nur bedingt tragfähig. Ein normalisiertes Reporting-Fact-Modell wäre mittelfristig sinnvoll.

## Inkonsistenz-Risiken

- Event kann Client, Case und Episode referenzieren; nicht alle Konsistenzregeln sind DB-hart.
- `DocumentType` hat keinen Unique-Constraint auf `(facility, name)`; `FieldTemplate` schon.
- Cases/WorkItems enthalten sensible Freitexte, aber nicht dieselbe Sensitivitäts- und Retentionlogik wie Events.
- Audit- und Exportpfade können Daten aus mehreren Modellen zusammenführen; kleine Scoping-Fehler hätten hohe Wirkung.

---

# 8. Produkt- & UX-Denken

## Unterstützt die Software echte Arbeitsprozesse?

Ja. Die Produktidee ist stark:

- Zeitstrom als digitales Dienstbuch.
- Mobile Nutzung.
- Pseudonymregister statt Personenakte.
- Anonyme Kurzereignisse.
- Kontaktstufen.
- Aufgaben/Hinweise für Schichtübergabe.
- Zeitfilter passend zu Diensten.
- Berichtswesen inklusive Jugendamtslogik.
- Offline-Modus für Streetwork.
- DSGVO-Vorlagen und Betroffenenexporte.

Das ist nicht aus einem CRUD-Generator gefallen.

## Wo arbeitet sie gegen Nutzer?

- **Datenschutzmodell ist mental komplex.** Rollen, Kontaktstufen, Dokumentationstyp-Sensitivität, Feld-Sensitivität, Verschlüsselungsflag, Retention und Offline-Verfügbarkeit sind viel für kleine Teams.
- **Freitext bleibt Verlockung.** Nutzer werden sensible Details dort schreiben, wo es am schnellsten geht. Das System muss das aktiv begrenzen, nicht nur dokumentieren.
- **Konfiguration kann fachlich überfordern.** Jede Einrichtung kann Dokumentationstypen und Felder bauen. Ohne gute Defaults und Beratung entstehen schlechte Datenmodelle.
- **Offline-Konflikte sind schwer.** In sozialarbeiterischen Schichten will niemand Konfliktauflösung wie in Git. Offline-Funktion muss extrem simpel bleiben.
- **Exporte sind mächtig.** Leitung/Admin kann sehr viel ziehen. Das ist fachlich nötig, aber UX muss Missbrauch und versehentliche Exporte deutlich erschweren.

## Wo ist es besonders gut gedacht?

- Keine Klarnamen als Produktversprechen.
- Ereignis statt Akte als Zentrum.
- Kombination aus Retention, Legal Holds und Löschanträgen.
- Feld-Level Sensitivität und Verschlüsselung.
- Jugendamtsbericht und Statistik nicht als Nachgedanke.
- Offline-Anforderung wird ernst genommen, nicht ignoriert.

## Wurde das von jemandem mit echter Domänenkenntnis gebaut?

**Ja.**

Begründung: Die Fachkonzepte, README-Zielgruppe, Kontaktstufen, Zeitstrom-Metapher, Aufgabenübergabe, Jugendamtsbericht, anonyme Kontakte, Pseudonymisierung und der Fokus auf kleine Einrichtungen zeigen reale Domänenkenntnis. Die Schwächen liegen weniger im Produktverständnis als in der technischen Absicherung dieses Produktverständnisses.

---

# 9. Langfristige Tragfähigkeit

## Wartbarkeit über 3-5 Jahre

Mit diszipliniertem Maintainer-Team: möglich. 
Mit Ein-Personen-Busfaktor und Featuredruck: riskant. 
Als Open-Source-Projekt mit externen Beiträgen: nur nach Modularisierung und klarerer Security-Governance realistisch.

Die Anwendung hat bereits viele Features, die jeweils eigene Wartung erzeugen: Offline, MFA, Retention, File Vault, Reporting, RLS, Exporte, PWA, Audit, DSGVO-Paket, WorkItems, Cases, Outcomes. Das ist viel für ein Projekt dieser Größe.

## Beitragbarkeit

Positiv:

- AGPL.
- README/CONTRIBUTING/Dokumentation umfangreich.
- Tests breit vorhanden.
- Stack bekannt.

Negativ:

- Viel Domänenwissen erforderlich.
- Security-Regeln sind verteilt.
- Lokales Setup braucht Docker/Systemlibs/Postgres.
- Interne Issue-Referenzen und fehlende Audit-Dokumente erschweren Kontext.
- Ein externer Contributor kann leicht ein Feature bauen, das Scoping/Sensitivität/Retention nicht korrekt beachtet.

## Risiko von Stagnation

Mittel bis hoch, wenn das Projekt weiter Featurebreite aufbaut, ohne Architekturgrenzen nachzuziehen.

Die Gefahr ist nicht, dass das Projekt wertlos ist. Die Gefahr ist, dass es "fast produktionsreif" bleibt, weil jede neue fachliche Verbesserung neue Security- und Betriebsfragen öffnet.

## Potenzial für ernsthaftes Open Source?

**Ja, aber nicht automatisch.**

Das Projekt hat ein klares Problem, eine plausible Zielgruppe und echte Domänenpassung. Genau das fehlt vielen Open-Source-Fachverfahren. Um tragfähig zu werden, braucht es:

- klare Governance,
- harte Security-Roadmap,
- stabilen Release-Prozess,
- reproduzierbare Betriebsanleitung,
- modulare Domänengrenzen,
- externe Audits,
- kleinere, nachvollziehbare Contribution-Schnittflächen.

## Kollabiert es unter eigener Komplexität?

Noch nicht. Aber der Kollapspfad ist sichtbar: `core` wächst weiter, Offline wird wichtiger, Reporting wird komplexer, Einrichtungen konfigurieren unterschiedlich, Security-Regeln verteilen sich weiter. Wenn jetzt nicht modularisiert wird, wird v1.x schwer zu stabilisieren.

---

# 10. Schonungslose Gesamtbewertung

## Gesamtbewertung

**6.5 / 10**

Aufgeschlüsselt:

- Fachliches Produktverständnis: 8 / 10
- Grundarchitektur: 7 / 10
- Security-Konzept: 7 / 10
- Security-Durchsetzung: 5.5 / 10
- Wartbarkeit: 6 / 10
- Betrieb für kleine NGOs: 5 / 10
- Open-Source-Tragfähigkeit: 6.5 / 10
- Produktreife für Echtdaten: 5 / 10

## Würde ich es einsetzen?

**Für Pilotbetrieb: ja, kontrolliert.** 
**Für produktive sensible Sozialdaten: nein, nicht in diesem Zustand.**

Voraussetzungen für Pilot:

- kleine Datenmenge,
- klare Einwilligungs-/Informationslage,
- kein blinder Offline-Rollout,
- geprüfte Backups inklusive Medien,
- aktive technische Begleitung,
- harte Schulung gegen Klarnamen/Freitextmissbrauch,
- Security-Fixes vor Start.

## Würde ich investieren?

**Ja, aber als Pre-Product mit Härtungsbudget, nicht als fertiges GovTech/HealthTech-Produkt.**

Das Projekt löst ein reales Problem und hat ungewöhnlich starke Domänenpassung. Die Investition müsste aber zuerst in Produktisierung, Security und Betrieb gehen, nicht in weitere Features.

## Würde ich darauf aufbauen?

**Ja, wenn ich die Architekturhoheit bekomme.** 
Nicht, wenn erwartet wird, dass man nur ein bisschen UI poliert und dann produktiv geht.

Die nächsten Monate müssten stärker "Stabilisierung und Härtung" als "Featureausbau" sein.

---

# Bonus: Quick Wins und Refactorings

## Quick Wins (1-2 Tage)

1. **Prod-Compose reparieren.** Persistentes Volume für `MEDIA_ROOT` hinzufügen und Backup-Dokumentation explizit auf DB plus Medien erweitern.
2. **Dokumentationsdrift fixen.** AES-GCM-vs-Fernet-Aussagen korrigieren, fehlendes Audit-Dokument entfernen oder ergänzen, v0.10/v1.0-Status glätten.
3. **WorkItem-Edit-Policy schließen.** `WorkItemUpdateView` muss dieselbe `can_user_mutate_workitem`-Regel nutzen wie Status- und Bulk-Pfade.
4. **Offline-Modus facility-weit abschaltbar machen.** Default für echte Pilotdaten: aus oder explizit opt-in.
5. **Klartext-Freitext inventarisieren.** Alle Freitextfelder klassifizieren: erlaubt, sensitivitätsgeschützt, verschlüsselt, verboten oder retention-pflichtig.
6. **WeasyPrint lazy importen.** PDF-Imports aus Modulimporten entfernen, damit URL checks und Teile der Tests ohne Systemlib nicht scheitern.
7. **NPM-Audit-Lücke schließen oder Claim entfernen.** CI auditiert Python, nicht sichtbar Node-Abhängigkeiten.

## High-Impact-Refactorings

1. **Policy Layer einführen.** Eine zentrale API für `can_view`, `can_mutate`, `can_export`, `can_download`, `can_offline_cache`, `scope_queryset`. Views und Services dürfen keine eigenen Sicherheitsregeln mehr erfinden.
2. **Domänenmodule aus `core` schneiden.** Mindestens: `clients`, `documentation/events`, `workitems`, `retention`, `reporting`, `security/audit`, `offline`.
3. **Sensitive Freitexte vereinheitlichen.** Entweder alle sensiblen Notizen in das FieldTemplate/Event-Modell überführen oder verschlüsselte Modellfelder mit Sensitivität/Retention ergänzen.
4. **Reporting-Fact-Modell bauen.** JSONB bleibt Erfassungsschicht; Reporting bekommt normalisierte, geprüfte Fakten.
5. **Offline als Capability behandeln.** Per Facility/Rolle/Dokumenttyp steuerbar, mit Device-Liste, Revocation, Server-Expiry und High-Sensitivity-Default "nicht offline".

## Nächster sinnvoller Architekturschritt

Nicht Microservices. Nicht Rewrite. Nicht React-API-Migration.

Der nächste sinnvolle Schritt ist ein **Security-Policy- und Domänenmodul-Schnitt** innerhalb des Monolithen:

```text
core.policy
core.tenancy
core.clients
core.documentation
core.workitems
core.retention
core.reporting
core.offline
```

Ziel: Jede neue Funktion muss durch eine kleine Zahl verpflichtender APIs. Keine direkte Query auf sensitive Modelle aus Views, Exports oder Offline-Code ohne Facility- und Policy-Kontext.

---

# Erste 3 Maßnahmen bei Übernahme morgen

## 1. Produktionsstopp für Echtdaten bis Security-Lücken geschlossen sind

Ich würde sofort einen kurzen Security-Freeze setzen und drei Dinge in der ersten Woche schließen:

- WorkItem-Edit-Policy fixen.
- Offline-Modus standardmäßig deaktivieren oder facility-weit opt-in machen.
- Alle Klartext-Freitexte klassifizieren und mindestens `Client.notes`, `Case.description`, `WorkItem.description` in ein explizites Sensitivitäts-/Verschlüsselungsmodell überführen oder per UI/Policy begrenzen.

## 2. Betriebsbasis beweisbar machen

Ich würde einen echten Produktions-Probelauf mit Docker Compose/Coolify-artigem Setup aufsetzen:

- DB + Medien persistent.
- Backup und Restore getestet.
- RLS mit echter Prod-DB-Rolle geprüft.
- Retention-/Snapshot-/Orphan-Cleanup-Cronjobs aktiv.
- ClamAV-Ausfallverhalten getestet.
- Health/Monitoring sichtbar.

Ohne Restore-Test gibt es keinen Produktivbetrieb.

## 3. Zentrale Policy-Schicht einziehen

Ich würde alle kritischen Datenzugriffe durch eine verpflichtende Policy-/Scope-API ziehen:

- Event sichtbar?
- Feld sichtbar?
- Client exportierbar?
- Attachment downloadbar?
- WorkItem mutierbar?
- Offline cachebar?
- Query facility-gescoped?

Erst danach würde ich neue Produktfeatures akzeptieren. Sonst wächst das System weiter in genau die Richtung, in der es am leichtesten Sicherheitsfehler produziert.

