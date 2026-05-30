# Tiefenaudit: `anlaufstelle/app`

**Auditor:** externer Software-Architekt / Security-Auditor
**Datum:** 2026-04-29
**Repo-Stand:** `main` (v0.10.x), Django 5.1, Python 3.13, ~20k LOC `core/`, 150 Testdateien / ~1.500 Testfunktionen
**Modus:** schonungslos. Keine Höflichkeit, nur was im Code steht.

---

## TL;DR

Anlaufstelle ist **deutlich überdurchschnittlich** für ein NGO-Open-Source-Projekt. Es gibt ein echtes Fachkonzept (Pseudonymisierung, Kontaktstufen, Sensitivitätsstufen), eine ehrliche Defense-in-Depth (Django-Scoping + Postgres RLS + AuditLog + verschlüsselte Felder/Files + ClamAV + MFA), und der Codebase wirkt nicht prototypisch, sondern wie das Resultat einer mehrmonatigen disziplinierten Iteration mit Issue-Referenzen, ADR-ähnlichen Notizen und expliziter Auseinandersetzung mit eigenen Findings (`docs/security-notes.md`, `docs/audits/`).

**Aber:** das System ist **deutlich komplexer als seine Zielgruppe** (5–20-Personen-Teams ohne IT-Admin) operativ tragen kann, und einige der "Defense-in-Depth"-Schichten haben subtile Lücken, die genau in dem Vertrauensmodell knallen, das das Projekt verspricht.

**Gesamtbewertung: 7,5 / 10.** Tragfähig. Nicht trivial. Nicht risikofrei.

---

## 1. 🏗️ Systemarchitektur

**Stil:** Klassischer Django-Modular-Monolith. Ein App-Modul `core/` mit klarer Aufteilung in `models/`, `views/`, `services/`, `forms/`, `middleware/`, `signals/`, `management/`, `templatetags/`. HTMX + Alpine fürs Frontend, kein SPA. Caddy-Reverse-Proxy, Gunicorn, Postgres, optional ClamAV, optional Sentry. WeasyPrint für PDF.

**Stärken**
- **Service-Layer existiert wirklich** und ist nicht kosmetisch: 31 Files in `core/services/`, in denen die nicht-triviale Logik (Encryption, K-Anonymisierung, Audit, Export, File Vault, Sensitivität, Search, Retention, Login-Lockout) sitzt. Views sind tendenziell dünn. Das ist für Django-Projekte selten.
- Kustom-Manager (`FacilityScopedManager`, `EventManager.visible_to`) als deklarative Sicherheitsschicht — gut.
- `core/middleware/facility_scope.py` setzt die Postgres-Session-Variable, die die RLS-Policies auswertet — Bridging zwischen Application- und DB-Layer ist explizit modelliert.
- 74 Migrationen, klar nummeriert, mit Issue-Referenzen in den Docstrings.

**Schwächen**
- **Eine einzige App `core` für alles.** Klienten, Events, WorkItems, Cases, Episoden, DocumentTypes, Statistics, Audit, MFA, Offline, DSGVO-Pakete — alles in derselben App. Bei aktuell ~20k LOC noch handhabbar, aber die Domänen sind nicht wirklich getrennt; ein Bug in der Statistik-Pipeline kann durch denselben Import-Graph laufen wie der Login. Keine Bounded Contexts. Wenn das Projekt um Module erweitert wird (z.B. Streetwork-Spezifika, Notschlafstellen-Spezifika), explodiert `core/`.
- **Querverweise zwischen Services** sind unstrukturiert. `services/clients.py`, `services/event.py`, `services/cases.py`, `services/handover.py` etc. importieren wechselseitig. Keine Domänenfassade. Bei Änderungen am Event-Modell muss man heute schon mehrere Services parallel anfassen.
- **Middleware-Stack ist lang (16 Einträge)** mit eigener `htmx_session`, `mfa`, `password_change`, `user_language`, `facility_scope`. Jeder davon ist berechtigt, aber die *Reihenfolge* ist ein implizites Vertragsnetz, das im Dev-Onboarding hart zuschlagen wird.
- **Kein klares CQRS-/Read-Model-Konzept**, obwohl der Reporting-Use-Case (Halbjahresbericht ans Jugendamt) das Killer-Feature ist. Statistics werden teils on-the-fly, teils per Snapshot generiert (`services/snapshot.py`, 340 LOC) — der Hybrid ist nicht in einem ADR begründet, den ich gefunden habe.

**Risiko-Level: niedrig–mittel.** Kein architektonischer Leichtsinn, aber die Monolith-zu-Modular-Schwelle wird in 12–18 Monaten erreicht.

---

## 2. 📦 Domänenmodell & Konzept

**Kernobjekte:** `Facility` (Mandant), `Client` (pseudonymisiert), `Case`, `Episode`, `Event`, `WorkItem`, `DocumentType` + `FieldTemplate` + `DocumentTypeField` (konfigurierbares Schema), `AuditLog`, `Activity`, `RetentionProposal`, `LegalHold`, `StatisticsSnapshot`, `EventAttachment`, `RecentClientVisit`, `TimeFilter` (Schichtmodelle), `QuickTemplate`.

**Was wirklich gut ist**
- **Pseudonym als first-class Konzept.** `Client.pseudonym` ist Pflicht, kein Klarnamen-Feld existiert. `ContactStage` (identifiziert/qualifiziert) modelliert die fachliche Eskalationsstufe — das ist konzeptionelles Niveau, das in keinem kommerziellen Pendant existiert. Das ist der eigentliche Wert dieses Projekts.
- **Konfigurierbares Event-Schema** über `DocumentType` + `FieldTemplate` (Slug, Sensitivität, `is_encrypted`) + `data_json` (JSONB). Pragmatisch: Einrichtungen können ihre eigenen Dokumentationstypen anlegen, ohne dass Migrationen nötig sind.
- **Sensitivitätsmatrix** ist ein zweiachsiges Konstrukt: DocumentType-Sensitivität × Field-Level-Sensitivität, ausgewertet via `effective_sensitivity = max(...)` (`services/sensitivity.py`). Die zentrale Logik ist *einmal* implementiert und wird konsistent genutzt.
- **K-Anonymisierung als Alternative zum Hard-Delete** (`services/k_anonymization.py`) — selten gesehen außerhalb akademischer Projekte. Statistik-Werte bleiben erhalten, Re-Identifikation wird reduziert.
- **Anonymisierung** kaskadiert auf `Case`, `Episode`, `WorkItem` (`Client.anonymize`).

**Was zufällig gewachsen wirkt**
- **`Case` vs. `Episode`.** Beides "Fall-Container", `Episode` hängt an `Case`. Im Fachkonzept-Doku werden sie sauber unterschieden, aber in den Modellen sind die Felder nahezu identisch (Title, Description, ähnliche Lebenszyklen). Klassischer Indikator: zwei Konzepte, die im Code wie eines aussehen.
- **`WorkItem` neben `Case` neben `Episode`.** Drei Aufgaben-/Vorgangs-artige Container. Für Außenstehende (und vermutlich neue Beitragende) nicht sofort offensichtlich, was wofür ist.
- **`Activity` neben `AuditLog` neben `EventHistory` neben `RecentClientVisit`.** Vier verschiedene Logs/Activity-Streams. Trennung ist dokumentiert, aber konzeptionell überladen.
- **`Event.is_anonymous` zusätzlich zu `Client = NULL`.** Doppelter Anonymitätsbegriff: Event ohne Client UND Event mit Anonym-Flag. Hier kommen früher oder später Datenbank-Inkonsistenzen.
- **`Facility` ist Mandant**, aber das User-Modell hat `facility = SET_NULL`. User-ohne-Facility ist ein expliziter Edge-Case (Admin), der in den Views als "facility=None → kein Zugriff" abgefangen wird — funktioniert, ist aber nicht modelliert, sondern gehärtet.

**Wo es zuerst bricht**: Bei Mehrfach-Facility-User. Aktuell ist 1 User: 1 Facility hartverdrahtet (`User.facility = ForeignKey`). Sobald Träger-Strukturen Mitarbeiter über mehrere Einrichtungen einsetzen (Nachschicht-Teams, Springer), kollabiert das Modell. Das Fachkonzept erwähnt "Organisationshierarchie offen" — das ist ein Schuldschein.

**Bewertung:** Konzeptionell **überdurchschnittlich**, technisch **stellenweise verwachsen**. Zu viele Container-Konzepte (Case, Episode, WorkItem). Risiko: mittel.

---

## 3. 🔐 Sicherheit & DSGVO

Das ist der Bereich, in dem das Projekt eindrucksvoll arbeitet, **aber auch der Bereich mit den präzisesten Fehlern**.

**Vorhandene Schichten (verifiziert im Code)**
- Postgres **Row Level Security** (Migration `0047`) mit `FORCE ROW LEVEL SECURITY` auf 15+ Tabellen, Policy gegen `current_setting('app.current_facility_id', true)`. Defense-in-Depth unter dem ORM.
- ORM-Manager `FacilityScopedManager` + `Event.visible_to(user)` als Standardpfad.
- Fernet/MultiFernet-Verschlüsselung für sensitive Felder + chunked File-Encryption im File Vault.
- Audit-Log mit ~30 Action-Types, `EventHistory` mit Append-Only-Trigger (Migration `0012`).
- ClamAV-Pflicht in Prod (`fail-closed`).
- MFA via `django_otp`, MFA-Enforcement-Middleware, Backup-Codes.
- Session-Cookie 30 Min, `CSRF_COOKIE_SAMESITE=Strict`, `CSRF_COOKIE_HTTPONLY=True`, HSTS, CSP ohne `unsafe-inline` (mit dokumentierter `unsafe-eval`-Schuld für Alpine).
- `SECURE_*`-Settings sauber, `DEBUG=False` in prod fail-closed enforced.
- Login-Lockout (`services/login_lockout.py`), Rate-Limit auf Mutations.
- DSGVO-Paket-Generator (`services/dsgvo_package.py`).

**Würde ich diesem System echte sensible Sozialdaten anvertrauen?** Mit Auflagen ja, aber nicht ohne.

### Top-5-Sicherheitsrisiken

1. **Stale `app.current_facility_id` über Connection-Pooling.**
 `FacilityScopeMiddleware` setzt die Postgres-Session-Variable nur für authentifizierte Requests. `is_local=false` macht sie session-weit. Mit `CONN_MAX_AGE=60` werden Connections wiederverwendet. Wenn eine Connection nach einem Auth-Request für eine *anonyme* Anfrage recycelt wird, bleibt die Variable auf dem alten Facility-Wert stehen. Aktuell harmlos, weil anonyme Routes keine facility-scoped Tabellen anfassen — aber das ist eine *Annahme*, kein Mechanismus. Sobald jemand einen anonymen Endpoint hinzufügt, der irrtümlich eine RLS-Tabelle queryt, leakt er fremde Facility-Daten *ohne Auth-Bypass*. Die Middleware sollte den Wert für anonyme Requests **explizit leeren**, nicht überspringen. Die Datei dokumentiert das Problem korrekt für authentifizierte Requests, übersieht es aber für die anonyme Branch.

2. **JSONB-Feld `Event.data_json` umgeht die Datenbank-Constraint-Schicht.**
 Verschlüsselung passiert in `Event.save` über `_encrypt_sensitive_fields`. Wer per `bulk_create`, `update`, `Manager.update_or_create` ohne `save`, oder per direktem SQL schreibt, **umgeht die Verschlüsselung vollständig**. Es gibt zwar `services/encryption.encrypt_event_data` als Hilfsfunktion für solche Pfade — aber das ist Disziplin, nicht Garantie. Eine Postgres-Trigger-basierte Verschlüsselung wäre robuster, ist aber bei Fernet kaum sauber abbildbar. Mindestens: Architecture-Test, der `Event.objects.bulk_create` und `update(data_json=...)` verbietet, oder pre-save-Signal als zweite Linie.

3. **Search durchsucht `data_json` mit `__icontains` über *alle* Events der Facility — auch verschlüsselte Felder werden zwar im Treffer-Filter ausgeschlossen, aber der ursprüngliche `LIKE`-Match läuft auf der gesamten JSONB-String-Repräsentation.**
 `search.py:64-69` filtert `Event.objects.filter(data_json__icontains=query,...)`. Das matcht in der Postgres-Repräsentation des JSONB **inklusive der verschlüsselten Tokens**. Praktisch: der Query "secret" matcht keine Klartextfelder, kann aber durch zufällige Substring-Treffer in verschlüsselten Tokens False-Positive-Treffer erzeugen, deren bloße Existenz dem Suchenden bestätigt, dass ein verschlüsselter Wert "irgendwie passt". Information-Leak im Konjunktiv. Lösung: Search nur auf nicht-verschlüsselte Felder per JSONB-Pfad.

4. **`safe_decrypt` fail-open auf `"[verschlüsselt]"`.**
 Bei rotated/lost Key liefert `safe_decrypt` einen Platzhalter und loggt `WARNING`. UX-freundlich, aber: Ein Angreifer mit DB-Zugriff, der einen Fernet-Token tampert, sieht im UI nicht "Datei manipuliert", sondern "[verschlüsselt]". Ein expliziter Tamper-Indikator (z.B. unterscheiden `KeyMissing` vs. `InvalidToken`) wäre gegenüber Forensik wertvoll.

5. **`unsafe-eval` in CSP für Alpine.js ( abgeschlossen, offen).**
 Dokumentiert (`base.py:243ff`) und in `docs/security-notes` als "akzeptierte Schuld" benannt. Sauber. Aber: solange `unsafe-eval` aktiv ist, wirkt die ansonsten sehr strikte CSP wesentlich schwächer. XSS-Reduzierung ist bei realistischer Bedrohung (Stored XSS in `data_json`-Notizen) der Hauptzweck der CSP — und genau die Mitigation ist gerade erodiert. Die Issue-#672-Migration ist die zweitwichtigste Sicherheitsbaustelle.

**Weitere konkrete Befunde**
- **`AuditLog.facility = NULL`** ist explizit gewollt (System-Events vor Login). Aber NULL-Zeilen entgehen der RLS-Policy und sind nur für DB-Superuser sichtbar. Das ist als "Feature" markiert (`security-notes.md`), heißt aber operativ: Failed-Logins-Audit ist aus der Anwendung heraus **nicht abrufbar** — kein Lead/Admin sieht sie über die UI. Bei einem Brute-Force ist man ohne psql-Zugang blind. Wenn das stimmt, ist es ein Operating-Gap.
- **Login-Username-Enumeration:** ohne Code-Lookup nicht abschließend beurteilt, aber `services/login_lockout.py` + `views/auth.py` sollten gegen Timing/Existence-Probing geprüft werden (im Audit nicht im Detail gemacht).
- **`SESSION_COOKIE_SAMESITE = "Lax"`** ist dokumentiert begründet (Password-Reset-Links). OK, aber die Begründung impliziert, dass der Reset-Link auf derselben Origin liegt — wenn jemand das später entkoppelt (separate Subdomain), muss die Begründung neu validiert werden.
- **`CONTRIBUTING` und `SECURITY.md` existieren** — gut. Vulnerability-Disclosure-Policy ist im Repo erwähnt, sollte aber gegen ein konkretes Mailbox-Abuse-Konto auflaufen.

---

## 4. 🧪 Codequalität

**Senior-Level oder Prototyp?** Senior-Level mit Anflug von "Solo-Entwickler-Konsistenz".

- Naming: deutsch in den User-facing Strings (`verbose_name`, Help-Text, Templates), englisch in Code/Variablen — saubere i18n-Trennung, das ist selten.
- Docstrings: ausführlich, oft mit Issue-Referenzen (`Refs #586`, `Refs #598 R-2`). Code wirkt nachdokumentiert, nicht vor-dokumentiert.
- Framework-Idiom: ORM korrekt verwendet (`select_related`, `prefetch_related` an den richtigen Stellen, Composite-Indexe explizit, Trigram-Index auf `Client.pseudonym`).
- Tests: ~1.500 Tests, inkl. RBAC-Matrix-Test, Architecture-Tests (anti-pattern Inline-Scripts), E2E mit Playwright.
- Konsistenz: hoch. Service-Function-Signaturen folgen Mustern.

**Schwächen**
- **`views/clients.py` mischt CBV-View-Klassen mit ad-hoc Imports innerhalb der Funktion** (`from django.core.paginator import Paginator` mitten im View) — kosmetisch, aber Indikator für sukzessives Wachstum.
- **`Event.save` lädt bei jedem Save document_type.fields** (zwar gecached pro Instanz, aber bei `bulk_update_or_create`-Loop durchaus N+1).
- **Lange Views** (`views/zeitstrom.py` 195 LOC, `workitems.py` 185 LOC) — knapp unter der Schmerzschwelle, aber Wachstumsrichtung negativ.
- **Mixin-Inflation**: `AssistantOrAboveRequiredMixin`, `StaffRequiredMixin`, `LeadOrAdminRequiredMixin`, `AdminRequiredMixin`, `FacilityScopedViewMixin`, `HTMXPartialMixin`. Das wird in 6 Monaten 10 sein und niemand findet mehr die richtige Kombination.

**Wo der Code am schwächsten ist:** im `services/snapshot.py`/`services/statistics.py`-Komplex. 340 + 163 LOC für etwas, das laut Fachkonzept *das* zentrale Differenzierungs-Feature (Halbjahresbericht!) sein soll, wirkt wie organisches Wachstum mit zwei Implementierungspfaden (live + snapshot) ohne Gesamtdesign.

---

## 5. 🧩 Komplexität & Technische Schulden

**Top-5 Tech-Debt-Hotspots**

1. **Statistics-Hybrid (live vs. snapshot)** — zwei Pfade, eine fehlende Architektur-Entscheidung.
2. **Search-Service** — `data_json__icontains` ist bei wachsendem Datenbestand sowohl Performance- als auch Privacy-Risiko (siehe Sec-Punkt 3).
3. **Container-Konzepte `Case` / `Episode` / `WorkItem`** — Domänen-Konsolidierung steht aus.
4. **CSP `unsafe-eval`-Migration auf `@alpinejs/csp`** — bekanntes Issue #672, aber blockiert durch Template-Rewrite-Aufwand.
5. **Verschlüsselung als `save`-Aspekt statt als DB- oder Manager-Garantie** — jede neue Code-Stelle, die `Event.objects.update(data_json=...)` schreibt, ist ein potenzieller Klartext-Leak.

**Was wird zuerst unwartbar:** der Statistics-/Reporting-Stack. Sobald Träger-Berichte mit 5+ Variantenparametern dazukommen, sprengt das den aktuellen Snapshot-Ansatz. Der zweite Wartbarkeits-Killer ist das Templates-Verzeichnis (`src/templates/core/...`, 6.159 LOC HTML mit Alpine), wenn das CSP-Tightening durchgezogen wird — jedes Template muss angefasst werden.

---

## 6. ⚙️ Entwicklererfahrung & Betrieb

**Setup:** `Dockerfile` + `docker-compose.yml` + `.staging.yml` + `.prod.yml` + `Caddyfile` + `Makefile` + `docker-entrypoint.sh` mit Postgres-Advisory-Lock für Multi-Replica-Migrate. Das ist sehr ordentlich. Healthcheck eingebaut. Coolify-Deployment-Doku vorhanden.

**Lokal produktiv in 1–2 Tagen?** Für einen Django-erfahrenen Entwickler: ja. `docs/admin-guide.md`, `coolify-deployment.md`, `ops-runbook.md`, `e2e-runbook.md` existieren. Das Onboarding ist dokumentiert.

**Ist das für kleine NGOs realistisch betreibbar?** Hier wird's ehrlich.
- Für eine NGO **mit IT-Dienstleister oder einer technisch affinen Person**: ja, plausibel. Coolify + Compose ist eine erträgliche Schwelle.
- Für eine NGO **ohne IT-Person**: nein. Das System hat 16 Middlewares, MultiFernet-Key-Rotation, ClamAV-Daemon, RLS-Policy-Maintenance, MFA-Pflicht, und der Operator muss die `ENCRYPTION_KEYS`-Rotation manuell fahren. Das ist nicht "ich klick auf Update" — das ist "ich verstehe Postgres-Session-Variablen".
- Das **widerspricht dem expliziten Versprechen** des Fachkonzepts ("Niedrige IT-Ausstattung. Ein gemeinsam genutzter Desktop-PC. Kein Server vor Ort, kein Netzwerkadministrator."). Anlaufstelle braucht in der Realität entweder einen Betreiber-Pool (managed hosting durch eine Trägerinitiative) oder schließt einen Teil seiner Zielgruppe aus. Das ist die wichtigste produktstrategische Inkonsistenz.

**Logging/Monitoring:** Strukturiertes JSON-Logging mit PII-Scrubber (`core/logging.py`) + Sentry (PII off). Solide.

---

## 7. 📊 Datenmodell & Speicher

- Postgres-only (Trigram-GIN-Index, JSONB, RLS, `pg_advisory_lock`). Kein DB-Portabilitäts-Anspruch, gut so.
- **JSONB für `Event.data_json`**: pragmatisch (konfigurierbares Schema), aber:
 - Reporting-Queries werden komplex (`->>'slug'` plus Trigram-Index nicht trivial).
 - Encrypted Felder als `{"__encrypted__": true, "value": "..."}`-Dict im JSONB → **kein Index möglich, keine SQL-Aggregation auf Klartext**, was Reports auf verschlüsselten Feldern unmöglich macht (das ist beabsichtigt, sollte aber als *bewusste* Einschränkung dokumentiert sein — das Fachkonzept erwähnt §18 JSONB-Performance-Monitoring, gut).
- **Keine sichtbaren Partitions-/Archivierungsstrategien** für Events. Bei einer Einrichtung mit 50 Kontakten/Tag × 250 Tage × 5 Jahre = 62.500 Events → unkritisch. Bei einem Streetwork-Team mit 3 Personen × 10 Kontakte/Schicht × 365 = 11.000/Jahr → unkritisch. Skaliert für die Zielgruppe.
- **Migrations:** 74 saubere Migrationen, RLS-Migration ist in der Mitte (0047) — bei einem Greenfield-Deployment wird die Reihenfolge funktionieren, bei einem In-Place-Upgrade von 0046 nach 0047 muss der DB-User die Privilegien für `ALTER TABLE... ENABLE ROW LEVEL SECURITY` haben. Doku dazu sollte geprüft werden.
- **Inkonsistenz-Risiko**: `Event.is_anonymous` parallel zu `Event.client = NULL`, `Client.is_active` parallel zu `Client.k_anonymized` — vier Boolean-Achsen pro Klient, die nicht orthogonal sind. Hier braucht es entweder einen `state`-Enum oder Constraint-Validierung.

---

## 8. 🧠 Produkt- & UX-Denken

**Wurde das von jemandem mit echter Domänenkenntnis gebaut?** Eindeutig **ja**.
Indikatoren:
- Pseudonym als Pflichtfeld, kein Klarnamen-Feld existiert.
- `ContactStage.IDENTIFIED` vs. `QUALIFIED` — exakt die Unterscheidung, die in der Praxis fehlt.
- `is_anonymous`-Flag *zusätzlich* zur Pseudonymisierung — modelliert den "kurzen Spritzentausch ohne Pseudonym"-Fall.
- `TimeFilter` als generisches Schichtmodell statt fixer "Schicht=21:30–09:00"-Annahme.
- DSGVO-Templates im `docs/`-Ordner (Auftragsverarbeitung, Verzeichnis-Vorlagen, Löschkonzept).
- Diplomarbeits-Bezug von 2009 + Realbau 2026 — domänenspezifische Reife, die nicht aus der Hüfte kommt.

**Wo arbeitet die Software gegen den Nutzer?**
- 16 Middlewares + MFA-Pflicht ist im Alltag eine Hürde für Teams, die historisch mit Kladde gearbeitet haben. Ein "Weichstart-Modus" für Onboarding-Phase fehlt.
- Konfigurierbares Schema (DocumentType + FieldTemplate) verlangt dem Admin sehr viel ab. Einrichtungen ohne fachliche Konfigurations-Rolle werden mit dem Default-Set arbeiten und das System nie ausreizen.
- Search-UX bei verschlüsselten Feldern: Treffer "irgendwo, aber Inhalt nicht sichtbar" wird Verwirrung produzieren.

---

## 9. 🚀 Langfristige Tragfähigkeit

**Open-Source-Beitragbarkeit:** mittel.
- AGPL v3 — schließt kommerzielle Forks aus, aber auch viele Träger-Adaptionen. Ist wahrscheinlich gewollt.
- CONTRIBUTING.md vorhanden, EN/DE.
- 74 Migrationen + RLS + benutzerdefinierte Verschlüsselung machen "casual contributing" schwer. Wer eine Funktion hinzufügen will, muss das Sicherheitsmodell verstehen.
- Das Repo ist erkennbar **Solo- oder Sehr-Klein-Team-getrieben** (Stil-Konsistenz, Ein-Stimmen-Docstrings). Bus-Faktor 1–2.

**Wartbarkeit 3–5 Jahre:** OK, wenn der Hauptentwickler dabei bleibt. Riskant, wenn nicht. Das Fachkonzept als 1.4-Versions-Dokument ist ein gutes Schutzschild gegen Wissensverlust.

**Stagnationsrisiko:** real, wenn keine Träger-Initiative die Trägerschaft übernimmt. Anlaufstelle ist *zu groß für Hobby* und *zu spezifisch für freiwilligen breiten Beitrag*. Förder-Ankerung (BMBF, OSS-Foerderfonds, Prototype Fund) ist mittelfristig existenznotwendig.

---

## 10. 💣 Schonungslose Gesamtbewertung

| Dimension | Bewertung |
|---|---|
| Architektur | 7/10 |
| Domänenmodell | 7,5/10 |
| Sicherheit / DSGVO | 7/10 (hoch ambitioniert, drei konkrete Lücken) |
| Codequalität | 8/10 |
| Tech-Debt-Niveau | 7/10 |
| DevEx / Betrieb | 7/10 für Tech-NGOs, 4/10 für die plakatierte Zielgruppe |
| Datenmodell | 7/10 |
| Produkt/UX | 9/10 |
| Tragfähigkeit | 6/10 |

**Gesamtwert: 7,5 / 10.**

**Würde ich es einsetzen?** Ja, in einer Einrichtung mit IT-Begleitung. Nicht ohne.
**Würde ich darin investieren?** Als Förderer ja — die Lücke ist real, das Konzept trägt, der Code ist gut genug, um auf ihm aufzubauen. Als Investor (kommerziell) nein — AGPL und Zielgruppe sind nicht monetarisierbar.
**Würde ich darauf aufbauen?** Mit Bedacht ja, aber nicht ohne die unter Kapitel 3 genannten Lücken zu schließen, **bevor** echte Klientendaten reingehen.

---

## 🧪 Bonus: Quick Wins & High-Impact-Refactorings

**Quick Wins (1–2 Tage)**
1. `FacilityScopeMiddleware`: für anonyme/unauthenticated Requests `app.current_facility_id` **explizit auf leer setzen**, nicht überspringen. Eliminiert Connection-Pool-Stale-State.
2. Architecture-Test: verbiete `Event.objects.bulk_create(...)` und `Event.objects.update(data_json=...)` außerhalb von `services/encryption.py`. Closes Sec-Punkt 2.
3. Search: `data_json__icontains` durch JSONB-Pfad-Suche pro nicht-verschlüsseltem Field-Slug ersetzen. Schließt Information-Leak und beschleunigt.
4. AuditLog: Lead/Admin-UI-View für `facility=NULL`-Events (Failed-Logins systemweit) mit Sonderrolle.

**High-Impact-Refactorings**
1. **Encryption als ORM-Garantie statt `save`-Aspekt.** Custom-`JSONField`, das beim Schreiben transparent verschlüsselt. Eliminiert die ganze Klasse von Bypass-Bugs.
2. **Statistics als eigene App + klares Read-Model.** `core/statistics/` mit eigenem Snapshot-Schema, eigene Migrationen, sauberer Cut zur Operativ-Domäne.
3. **CSP- vollziehen** (`@alpinejs/csp` + Template-Rewrite). Ohne `unsafe-eval` ist der CSP-Schild ernsthaft.
4. **Domänen-Trennung `clients/`, `cases/`, `events/`, `documentation/`, `audit/`, `security/` als eigene Django-Apps.** Reduziert die `core/`-Monokultur und erleichtert Code-Review.

**Nächster sinnvoller Architekturschritt:** Eine **Betreiber-Plattform**: ein gemeinsam betriebenes Hosting-Angebot für Anlaufstellen, das das DevOps-Problem löst. Ohne das wird das Projekt seine eigene Zielgruppe nicht erreichen.

---

## 🔚 Wenn ich es morgen übernehmen müsste — meine ersten 3 Maßnahmen

1. **`FacilityScopeMiddleware` für anonyme Requests fixen** und einen Architektur-Test schreiben, der `Event.objects.update`/`bulk_create` mit `data_json` blockt. Beide Sec-Lücken schließen, **bevor** das System produktiv mehr als 1 Einrichtung hat.
2. **Search-Service auf JSONB-Pfad-Suche umstellen** und im selben Aufwasch das Suchergebnis-Snippet so bauen, dass *nur* sichtbare Felder hervorgehoben werden — Information-Leak weg, UX besser.
3. **Betreiber-Frage politisch klären.** Ein technisches Audit-Item, das aber die größte Existenzfrage des Projekts ist: ohne ein "Anlaufstelle-managed-Hosting"-Angebot (Trägerinitiative, Verein, Kooperation) wird das Produkt seine eigene Zielgruppe (NGOs ohne IT) systematisch verfehlen — und die ganze technische Sorgfalt landet in Schubladen.

— Ende Audit.
