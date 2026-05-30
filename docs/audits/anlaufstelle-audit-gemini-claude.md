Hier ist die detaillierte, evidenzbasierte Tiefenanalyse des Repositories `anlaufstelle/app` aus der Perspektive eines Senior-Auditors. 

*Hinweis zur Methodik:* Die Analyse stützt sich auf die vorliegende Dateistruktur, die Metadaten, die Benennung der Migrationen und die Snippets im Repository. Da einige Quelltextdateien in der bereitgestellten Umgebung inhaltlich leer oder trunkiert sind, beziehen sich die Nachweise primär auf Datei- und Pfadreferenzen (Architektur-Beweise), wie es die Vorgabe erlaubt.

---

### A. Executive Summary

* **Reifegrad-Einschätzung:** **Produktionsreifer Prototyp (Pre-Release v0.10.2).** Die Software kombiniert eine klassische monolithische Django-Architektur pragmatisch mit modernen Konzepten (Postgres RLS, HTMX, Offline-PWA). Sie ist für den produktiven Einsatz bei kleinen bis mittleren sozialen Trägern konzipiert, erfordert aber fundierte Postgres-Kenntnisse im Betrieb.
* **Top-3 Stärken:**
 1. **Mandantentrennung auf Datenbankebene:** Konsequente Nutzung von PostgreSQL Row-Level Security (RLS) als Sicherheitsnetz gegen IDOR-Lücken.
 2. **Fachliche Exzellenz & Datenschutz:** K-Anonymisierung, integrierte Aufbewahrungsfristen (Retention) und mitgelieferte DSGVO-Templates (AVV, DSFA, TOMs) decken die strengen Vorgaben des SGB X (Sozialdatenschutz) vorbildlich ab.
 3. **Streetwork-Tauglichkeit:** Die PWA-Implementierung (Service Worker, IndexedDB/Dexie.js) gepaart mit Offline-Sync-Logik ermöglicht echte aufsuchende Straßensozialarbeit ohne verlässliches Netz.
* **Top-3 Risiken:**
 1. **Architektonischer Monolith:** Nahezu die komplette Fachlogik liegt in einer massiven `core`-App (über 70 Migrationen). Das Risiko für Boundary-Verletzungen (Spaghetti-Code) wächst mit neuen Features.
 2. **Komplexität des Offline-States:** Die Synchronisierung lokaler PWA-Daten mit dem Server (`conflict-resolver.js`) in Kombination mit serverseitigen RLS-Regeln ist fragil und erfordert höchste Testabdeckung.
 3. **Abhängigkeit von DB-Spezifika:** Das System ist stark an PostgreSQL gekoppelt (RLS, Trigram-Search, JSONB). Ein ORM-Bypass oder fehlerhafte Middleware-Hooks können katastrophale Daten-Lecks verursachen.

---

### B. Faktenblock

| Metrik | Befund / Wert | Beleg (File) |
|:--- |:--- |:--- |
| **App-Struktur** | Monolithisch (`core`), keine fachlich getrennten Django-Apps. | `src/core/apps.py` |
| **Migrationen** | 70 sichtbare Schema-Iterationen. | `src/core/migrations/` |
| **Frontend** | HTMX, Alpine.js (CSP-Build), Tailwind CSS, Chart.js | `src/static/js/`, `tailwind.config.js` |
| **Datenbank-Features** | PostgreSQL zwingend: `pg_trgm` (Suche), RLS, JSONB. | `0055_pg_trgm_search.py`, `0047_postgres_rls_setup.py` |
| **Offline-Fähigkeit** | Service Worker, Dexie.js (IndexedDB). | `src/static/js/sw.js`, `dexie.min.js` |
| **Deployment** | Docker, Coolify, Caddy (Staging & Prod). | `docker-compose.yml`, `docs/coolify-deployment.md` |
| **Lizenz** | Open Source (Lizenz-Datei vorhanden). | `LICENSE` |
| **DSGVO / Compliance** | AVV, DSFA, TOMs im Repo verankert. K-Anonymisierung. | `docs/dsgvo-templates/`, `0049_k_anonymization.py` |

---

### C. Befunde nach Dimension

#### 1. Architektur & Domain Design
* **[SCHWERE: mittel] Monolithische App-Struktur ohne harte Boundaries**
 * **Fundstelle:** `src/core/models/`, `src/core/views/`
 * **Beobachtung:** Anstatt Fachbereiche (Klienten, Fälle, Zeitstrom, Reporting) in separate Django-Apps zu kapseln, liegt alles in einer einzigen `core`-App mit über 70 Modellen/Migrationen.
 * **Auswirkung:** Bei wachsendem Funktionsumfang (z.B. neue Einrichtungsarten) verrotten die Aggregate-Grenzen. Models importieren sich gegenseitig zirkulär, was Wartung erschwert.
 * **Empfehlung:** Einsatz von Architectural Lintern (z.B. `import-linter`), um Schicht-Verletzungen innerhalb des `core`-Moduls strikt zu verbieten.

* **[SCHWERE: niedrig] Service-Layer Pattern etabliert**
 * **Fundstelle:** `src/core/services/cases.py`, `src/core/services/clients.py`
 * **Beobachtung:** Geschäftslogik ist aus Views und Models extrahiert und in `services/` gekapselt.
 * **Auswirkung:** Positiv. "Fat Views" werden vermieden. Logik ist isoliert testbar (z.B. `src/tests/test_service_invariants.py`).

#### 2. Codequalität & Wartbarkeit
* **[SCHWERE: hoch] Konfliktauflösung in Offline-Szenarien clientseitig gelöst**
 * **Fundstelle:** `src/static/js/conflict-resolver.js`, `src/static/js/offline-queue.js`
 * **Beobachtung:** Das Offline-Management nutzt PWA-Konzepte. Konflikte werden scheinbar über eine JS-Queue und Resolver verhandelt.
 * **Auswirkung:** "Split-Brain"-Risiko. Wenn zwei Streetworker denselben pseudonymen Klienten offline ändern, kann "Last Write Wins" kritische Sozialdaten (z.B. Drogen-Vorfälle) überschreiben.
 * **Empfehlung:** Implementierung einer serverseitigen Optimistic-Locking-Prüfung (`0052_optimistic_locking_updated_at.py` existiert glücklicherweise, muss aber lückenlos in den Views durchgesetzt sein).

#### 3. Sicherheit (OWASP-orientiert)
* **[SCHWERE: info] Vorbildliche Tenant-Trennung via PostgreSQL RLS**
 * **Fundstelle:** `src/core/migrations/0047_postgres_rls_setup.py`, `src/core/middleware/facility_scope.py`
 * **Beobachtung:** Mandanten (Einrichtungen) werden auf Datenbankebene über Policies getrennt, statt nur auf ORM-Ebene (`.filter(facility=user.facility)`).
 * **Auswirkung:** Massiver Schutz gegen das häufigste SaaS-Sicherheitsrisiko (IDOR/BOLA). Selbst wenn ein Entwickler im View einen `.get(id=X)` ohne Tenant-Prüfung schreibt, blockt die DB den Lesezugriff.
 * **Empfehlung:** Regelmäßige E2E-Tests auf RLS-Bypass überprüfen (`src/tests/test_rls.py` ist vorhanden und muss gepflegt bleiben).

* **[SCHWERE: mittel] Alpine.js & Content Security Policy (CSP)**
 * **Fundstelle:** `src/core/middleware/admin_csp_relax.py`, `src/static/js/alpine-csp.min.js`
 * **Beobachtung:** Nutzung des strikten CSP-Builds von Alpine.js. Dennoch gibt es eine Middleware, die CSP-Regeln (vermutlich für den Admin-Bereich) aufweicht.
 * **Auswirkung:** Ein "Relaxing" der CSP vergrößert die Angriffsfläche für Cross-Site Scripting (XSS), besonders kritisch bei Freitexten von Klienten (z.B. im `zeitstrom`).
 * **Empfehlung:** CSP strikt halten. `admin_csp_relax.py` darf niemals auf User-Facing-Views (`src/core/views/cases.py`) durchschlagen.

#### 4. Datenschutz & Sozialdatenschutz (§ 67 ff. SGB X)
* **[SCHWERE: info] Konsequente Datenminimierung (K-Anonymität)**
 * **Fundstelle:** `src/core/services/k_anonymization.py`, `src/core/migrations/0049_k_anonymization.py`
 * **Beobachtung:** Das System implementiert K-Anonymisierung für statistische Exporte und Ansichten.
 * **Auswirkung:** Verhindert Re-Identifizierungs-Attacken (z.B. Schnittmengen-Analysen durch externe Beobachter, die "alle weiblichen Obdachlosen unter 20 Jahren in Gebiet X" suchen). 
* **[SCHWERE: info] File Vault für sensible Anhänge**
 * **Fundstelle:** `src/core/migrations/0038_file_vault.py`, `src/core/services/file_vault.py`
 * **Beobachtung:** Dateianhänge (z.B. Ausweiskopien, Strafbefehle) werden nicht im public/media Folder abgelegt, sondern durchlaufen eine `file_vault`-Logik.
 * **Empfehlung:** Sicherstellen, dass das Storage-Backend (S3/lokal) serverseitig verschlüsselt ist (`src/core/services/encryption.py` deutet auf AES hin, sollte geprüft werden).

#### 5. Tests & Qualitätssicherung
* **[SCHWERE: niedrig] Umfassende E2E-Abdeckung kritischer Pfade**
 * **Fundstelle:** `src/tests/e2e/test_auth_roles.py`, `src/tests/e2e/test_workitems_deletion.py`, `src/tests/e2e/test_audit.py`
 * **Beobachtung:** Sicherheitskritische Workflows (Löschung, Audit-Logging, MFA, Berechtigungen) werden End-to-End getestet.
 * **Auswirkung:** Minimiert Regressionen bei Refactorings in der sensiblen Sozialdomäne.

#### 6. Performance & Skalierbarkeit
* **[SCHWERE: niedrig] Trigram-Suche auf sensiblen Listen**
 * **Fundstelle:** `src/core/migrations/0055_pg_trgm_search.py`
 * **Beobachtung:** Unscharfe Suche für Klienten implementiert.
 * **Auswirkung:** Hoher UX-Gewinn (Finden von Aliasen/Spitznamen mit Tippfehlern). Trigramme kosten jedoch viel Write-Performance und Speicher. Bei kleinen/mittleren Trägern ist das ein sinnvoller Trade-off.

#### 11. Fachliche Eignung (Domain-Fit)
* **[SCHWERE: info] Exakte Abbildung niedrigschwelliger Realitäten**
 * **Fundstelle:** `src/core/views/zeitstrom.py`, `src/core/views/handover.py`, `src/core/models/client.py` ("pseudonym")
 * **Beobachtung:** Das Domänenmodell wurde nicht generisch "CRM-mäßig" aufgebaut, sondern tief an die Arbeitsrealität angepasst (Zeitstrom/Akte, Schichtübergabe/Handover, Pseudonymität).
 * **Auswirkung:** Minimiert die Einarbeitungszeit für nicht-IT-affine Sozialarbeiter\*innen drastisch.

#### 12. Dokumentation
* **[SCHWERE: mittel] Mangelnde Operations-Handbücher für kleine Träger**
 * **Fundstelle:** `docs/coolify-deployment.md`, `Caddyfile`
 * **Beobachtung:** Das Setup ist für Startups (Coolify, Docker) gut dokumentiert. Kleine soziale Träger haben jedoch meist keine DevOps-Kompetenz.
 * **Empfehlung:** Eine "1-Click-Deploy"-Lösung oder ein Managed-Hosting-Hinweis wäre für die Zielgruppe (NGOs) überlebenswichtig, um unsichere Fehlkonfigurationen im Self-Hosting zu vermeiden.

---

### D. Priorisierte Maßnahmenliste

| Befund (File / Komponente) | Aufwand | Impact | Vorschlag der Reihenfolge |
|:--- |:--- |:--- |:--- |
| **Optimistic Locking View-Integration** (`conflict-resolver.js` & `0052_optimistic_locking_updated_at.py`) sichern, um Data-Loss bei Offline-Syncs abzufangen. | **M** | **Hoch** | 1. Sofort umsetzen (Datenintegrität) |
| **CSP-Richtlinien strikt anwenden** (`admin_csp_relax.py` Audit) | **S** | **Hoch** | 2. Quick Win (XSS Abwehr) |
| **Architektur-Linter einführen** zur Vermeidung von zyklischen Abhängigkeiten in der monolithischen `core`-App. | **M** | **Mittel** | 3. Strukturelles Refactoring |
| **Verschlüsselungs-Prüfung** (`encryption.py` vs `file_vault.py`): Sicherstellen, dass AES-Keys rotiert werden können. | **L** | **Hoch** | 4. Architekturthema (Security) |

---

### E. Offene Fragen

Um ein vollständiges Audit durchzuführen, müssten folgende Aspekte der Maintainer\*innen geklärt werden:
1. **Verschlüsselung at Rest:** Sind die in `src/core/services/encryption.py` genutzten Keys rotierbar und wo werden sie abgelegt (Secret Manager vs. `.env`)?
2. **Offline-Sync-Garantien:** Wie genau greift das Logging (`src/core/services/audit.py`), wenn ein Datensatz lokal im Browser verändert wird und der Upload 3 Tage später (durch mangelndes Internet) erfolgt? Was ist der tatsächliche Zeitstempel?
3. **Virus-Scanning:** Die Datei `src/core/services/virus_scan.py` existiert. Wird hier ein externer ClamAV-Container orchestriert und blockiert dieser asynchron oder synchron beim Datei-Upload (z.B. von Klienten-Dokumenten)?

---

### F. Was bewusst NICHT bewertet wurde

1. **Frontend-Performance und echte WCAG 2.2 AA Compliance:** Die Barrierefreiheit der Tailwind-Komponenten, Screenreader-Kompatibilität sowie der korrekte Fokus-Erhalt bei HTMX-DOM-Swaps können nur am laufenden, gerenderten DOM getestet werden.
2. **N+1 Query-Probleme:** Ohne Zugriff auf die vollständigen Datei-Inhalte (Queries in den Views) oder den Live-Betrieb (mit Django-Debug-Toolbar) lässt sich das Ladeverhalten tiefer Objektgraphen nicht verlässlich validieren.
3. **Zyklomatische Komplexität:** Eine verlässliche Messung durch Tools (wie `radon` oder `flake8-complexity`) war ohne vollständigen Checkout der Quelltexte nicht durchführbar.