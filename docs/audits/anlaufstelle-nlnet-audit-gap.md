#-Antrag vs. Audit-Maßnahmen: Gap-Analyse

**Stand:** 2026-04-29
**Vergleichsbasis:** Eingereichter-Antrag (Anlaufstelle / NGI Zero Commons Fund, Deadline 2026-04-01) gegen konsolidierten Audit aus 8 Quellen

---

## Kurzbefund

**Der-Antrag und die Audit-Maßnahmenliste haben eine kleine Schnittmenge.** finanziert ein **Feature-Programm** (Konfigurierbarkeit, Plugins, Accessibility, Datenportabilität). Der Audit fordert ein **Härtungs-Programm** (DSGVO-Lifecycle, RLS-Test, Service-Layer-Konsistenz, Operations).

Das ist nicht überraschend — die-Antragslogik fragt nach „neuen Commons", nicht nach Bug-Fixing in bestehendem Code. Aber es heißt auch: **die drei (MEDIA_ROOT-Volume, EventHistory-Lifecycle, Live-RLS-Test) sind in keinem Milestone explizit gedeckt.** Wenn die Bewilligung kommt und die Milestones starten, ist Anlaufstelle gleichzeitig in Feature-Arbeit *und* in einem Zustand, in dem sensible Echtdaten nicht produktiv reingehen sollten.

Die Frage ist also nicht „ist der Antrag schlecht" — er ist solide für seinen Zweck, sondern: **wie ordnet Toni Härtung und Feature-Arbeit zueinander an, jetzt wo das Audit konkret ist.**

---

## Die-Milestones im Überblick

| ID | Titel | Std | € | Audit-Schnittmenge |
|---|---|---:|---:|---|
| **M0** | Admin UI + Field Grouping | 100 | 5.000 | gering |
| **M1** | Conditional Field Logic | 120 | 6.000 | keine |
| **M2** | Entity Extension + Configuration Packages | 100 | 5.000 | keine |
| **M3** | Accessibility (WCAG 2.1 AA) | 80 | 4.000 | **teilweise** |
| **M4** | Data Portability (CSV-Import, JSON-Export, Art. 16) | 100 | 5.000 | **teilweise** |
| **M5** | Modular Reporting Standards (Plugin-Architektur) | 100 | 5.000 | **teilweise** |
| **M6** | Deployment, Demo & Documentation | 80 | 4.000 | **teilweise** |
| | **Summe** | **680** | **34.000** | |

---

## Maßnahme-für-Maßnahme: Was deckt, was nicht

Ich gehe die 50 priorisierten Maßnahmen aus der konsolidierten Audit-Liste durch
und markiere, ob sie unter einem Milestone realistisch durchgeführt werden
können. Bewertung:

- ✅ **gedeckt** — der Milestone behandelt das ohnehin
- 🟡 **anlehnbar** — nicht der Kern, aber im Rahmen mitnehmbar ohne Scope-Kreep
- ❌ **nicht gedeckt** — separate Zeit/Mittel nötig

### — Sofort (Datenverlust + Datenschutz-Blocker)

| # | Maßnahme |-Bezug | Status |
|---:|---|---|---|
| 1 | `MEDIA_ROOT`-Volume + Backup/Restore um Medien | M6 (Deployment + Runbook) | 🟡 |
| 2 | Retention-Delete-Historie redaktieren (gemeinsame Funktion) || ❌ |
| 3 | Daten-Migration für nicht-redaktierte `EventHistory`-Einträge || ❌ |
| 4 | Image-Tag pinnen + Namespace-Drift fixen | M6 (Deployment) | 🟡 |
| 5 | AGPL §13 Source-Link in UI | M6 (Documentation) | 🟡 |

**Bewertung:** Der kritischste Befund (EventHistory bewahrt Klartext, ist
DSGVO-Blocker) ist **nicht** im Antrag. Er kann nicht legitim unter „M3
Accessibility" oder „M5 Reporting" deklariert werden.

### — Sicherheits-Durchsetzung

| # | Maßnahme |-Bezug | Status |
|---:|---|---|---|
| 6 | Live-RLS-Test mit Non-Superuser-DB-Rolle || ❌ |
| 7 | Service-Layer-Konsistenz-Sweep (4 konkrete Stellen) || ❌ |
| 8 | WorkItem-Edit-Policy einheitlich || ❌ |
| 9 | `FacilityScopeMiddleware`: anonyme Requests leeren || ❌ |
| 10 | Validator `Sensitivity=HIGH ⇒ is_encrypted=True` | M2 (Entity Extension berührt FieldTemplate) | 🟡 |
| 11 | Architektur-Test gegen `bulk_create`/`update(data_json=...)` || ❌ |
| 12 | CSV-Formula-Injection-Escaping | M4 (Data Portability — CSV-Export) | ✅ |
| 13 | Login-Lockout atomar + Autocomplete `block=True` || ❌ |
| 14 | AuditLog-Trigger gegen UPDATE/DELETE verifizieren || ❌ |

**Bewertung:** Nur der CSV-Injection-Fix (#12) fällt natürlich unter einen
Milestone, weil M4 ohnehin den CSV-Export anfasst. Der Rest — RLS-Test,
Service-Layer-Sweep, Encryption-Garantien — ist klassische Härtung außerhalb
des-Scopes.

### — Datenschutz-Vollständigkeit

| # | Maßnahme |-Bezug | Status |
|---:|---|---|---|
| 15 | Anonymisierungs-Cascade vervollständigen || ❌ |
| 16 | Klartext-Freitext inventarisieren und klassifizieren || ❌ |
| 17 | `Client.notes`/`Case.description` Encryption oder UI-Policy || ❌ |
| 18 | K-Anonymität auf externe Berichte | M5 (Reporting Plugin-Architektur) | ✅ |
| 19 | DeletionRequest-Approval-Workflow | M4 (Art. 16 berührt Workflows) | 🟡 |
| 20 | Lösch-/Anonymisierungs-Matrix je Datenklasse | M4 (Datenportabilität als Anker) | 🟡 |
| 21 | `StatisticsSnapshot.data` aggregations-only | M5 (Reporting-Refactor) | ✅ |

**Bewertung:** Hier passt die Schnittmenge etwas besser. M4 + M5 berühren
Lifecycle und Reporting; K-Anonymität und StatisticsSnapshot-Refactor sind
legitim Teil des Plugin-Reportings. Aber der Kern — **Anonymisierungs-Cascade
auf EventHistory/EventAttachment** — ist keine Reporting-Frage und gehört
nicht in M5.

### — Operations & Beobachtbarkeit

| # | Maßnahme |-Bezug | Status |
|---:|---|---|---|
| 22 | Off-Site-Backup-Hook | M6 (Runbook) | 🟡 |
| 23 | Backup-Restore-Drill mit Trigger/RLS/Health | M6 (Runbook) | 🟡 |
| 24 | Healthcheck differenziert (ClamAV) | M6 (Runbook) | 🟡 |
| 25 | Caddy-Edge-Rate-Limit | M6 (Deployment) | 🟡 |
| 26 | Encryption-Key-Rollover-Runbook | M6 (Documentation) | 🟡 |
| 27 | SBOM, Cosign, Provenance | M6 (Deployment) | 🟡 |
| 28 | Dependabot, Trivy, Bandit || ❌ |
| 29 | `pyclamd 0.4.0` → `clamd 1.0.6+` || ❌ |
| 30 | Doku-Konsistenz (SECURITY.md, AES/Fernet, README) | M6 (Documentation) | ✅ |

**Bewertung:** M6 ist der Sammelpunkt für Ops. Aber M6 hat nur 80h und
laut Antrag ist der Kern „Ein-Befehl-Start, Demo-Instanz,
Mitwirkenden-Leitfaden". Off-Site-Backup, SBOM, Cosign sind erweiterbar,
aber nicht selbstverständlich Teil des Scopes. Realistisch: M6 könnte 4–6
dieser Punkte mitnehmen, wenn Toni sie aktiv hineindefiniert.

### — Performance & Skalierung

| # | Maßnahme |-Bezug | Status |
|---:|---|---|---|
| 31 | N+1 im Zeitstrom-Feed || ❌ |
| 32 | Pagination-Cap || ❌ |
| 33 | Event-Edit N+1 + Attachment-List-Pagination || ❌ |
| 34 | JSONB GinIndex || ❌ |
| 35 | `SESSION_SAVE_EVERY_REQUEST=False` || ❌ |
| 36 | Search: JSONB-Pfad-Suche statt `__icontains` || ❌ |

**Bewertung:** Komplett außerhalb. Performance-Tuning ist-fremder Scope.

### — Strukturelle Hygiene

| # | Maßnahme |-Bezug | Status |
|---:|---|---|---|
| 37 | mypy in CI inkrementell || ❌ |
| 38 | Ruff erweitern (`B`,`S`,`UP`,`C90`,`DJ`) || ❌ |
| 39 | Import-Linter / Grimp | M2 (Entity Extension berührt Domänenstruktur) | 🟡 |
| 40 | `services/event.py` splitten || ❌ |
| 41 | `forms/`-`INPUT_CSS` zentralisieren | M3 (Accessibility-Refactor an Forms) | 🟡 |
| 42 | AuditLog-Sweep über State-Transitions || ❌ |
| 43 | HTMX-Fokus + aria-live + `aria-describedby` + `html lang` | M3 (Accessibility) | ✅ |
| 44 | ADRs (RLS, Retention, MV, Offline-Krypto, AGPL) | M6 (Documentation) | 🟡 |
| 45 | Code of Conduct, DCO/CLA | M6 (Contributor-Guide) | 🟡 |
| 46 | Axe/Pa11y E2E-Tests | M3 (Accessibility) | ✅ |

**Bewertung:** M3 deckt Accessibility-Maßnahmen sauber. Der Rest ist Scope-fremd
oder nur per Anlehnung mitnehmbar.

---

## Zusammenfassung der Deckung

| Kategorie | Maßnahmen gesamt | ✅ gedeckt | 🟡 anlehnbar | ❌ nicht gedeckt |
|---|---:|---:|---:|---:|
| (Blocker) | 5 | 0 | 3 | **2** |
| (Sicherheit) | 9 | 1 | 1 | **7** |
| (Datenschutz) | 7 | 2 | 2 | **3** |
| (Operations) | 9 | 1 | 6 | **2** |
| (Performance) | 6 | 0 | 0 | **6** |
| (Hygiene) | 10 | 2 | 4 | **4** |
| **Summe** | **46** | **6 (13%)** | **16 (35%)** | **24 (52%)** |

Etwa **die Hälfte der priorisierten Audit-Maßnahmen ist nicht im-Scope.**
Davon sind die wichtigsten — die und der RLS-Test
weitgehend orthogonal zu den Milestones.

Die andere Hälfte ist anlehnbar oder direkt gedeckt, **aber nicht ohne
aktives Scope-Engineering**. M6 mit 80h kann nicht gleichzeitig die geplante
Demo-Instanz, das Runbook, ADRs, Off-Site-Backup, SBOM, Healthcheck-Refinement
und Image-Pinning sauber liefern. Es kann zwei oder drei davon liefern.

---

## Spannungen, die der Audit zum-Antrag erzeugt

### 1. M5 wird datenschutzkritischer als geplant

M5 baut eine Plugin-Architektur für Reporting. Im Antrag steht
„Migration des bestehenden Jugendamts-Reports zur Plugin-Architektur". Der
Audit sagt aber, dass genau dieser Report Re-Identifizierungs-Risiken hat
(`top_clients` mit Pseudonym, fehlende K-Anonymisierung kleiner Zellen, Klartext
in `StatisticsSnapshot.data`).

**Konsequenz:** M5 sollte nicht „Plugin-Architektur + Migration eines
unsicheren Reports" liefern, sondern „Plugin-Architektur + sichere
Referenz-Implementation mit K-Anonymität". Das ist konzeptionell stärker
gegenüber, aber kostet etwa 20–30 zusätzliche Stunden, die im Budget
nicht stehen.

### 2. M4 muss DSGVO-Lifecycle mitdenken

M4 enthält „DSGVO Art. 16 Berichtigungsanfrage-Workflow". Der Audit sagt,
dass es überhaupt **keinen** funktionierenden Lösch-/Anonymisierungs-Workflow
für Clients/Cases/Episodes/Users gibt — `DeletionRequest`-Modell existiert,
aber Approval-View fehlt; Anonymisierungs-Cascade ist unvollständig;
EventHistory bewahrt Daten nach Löschung.

**Konsequenz:** Wenn M4 Art.-16-Berichtigung baut, ist es inkonsistent
gegenüber den fehlenden Art.-17-Workflows (Löschung). Reviewer in einem
Stage-2-Gespräch werden das fragen. Die saubere Antwort: M4 wird zu
„Datenportabilität + DSGVO-Lifecycle (Art. 15/16/17 in einem Atemzug)"
erweitert. Auch das ist 30–40 zusätzliche Stunden.

### 3. M3 trifft auf existierende Formularduplikation

M3 ist Accessibility (WCAG 2.1 AA). Der Audit zeigt: `INPUT_CSS` ist 5×
dupliziert in `forms/{clients,cases,events,episodes,workitems}.py`. Das ist
bei Accessibility-Refactor genau die Stelle, an der jede ARIA-Korrektur 5×
gemacht werden müsste, falls man nicht zuerst zentralisiert.

**Konsequenz:** M3 sollte mit ~10h Forms-Zentralisierung beginnen, sonst wird
der Rest teurer. Das passt in den Scope, ist aber im Antrag nicht erwähnt.

### 4. Pilot ohne DSGVO-Härtung ist riskant erwartet in Stage 2 einen Adoption-Pfad. Der konsolidierte Audit sagt:
**„nicht mit echten sensiblen Sozialdaten produktiv ohne"**. Wenn
Toni in M0–M2 (Konfigurierbarkeit) eine Pilot-Einrichtung mitnimmt, läuft
diese Einrichtung gleichzeitig auf einem System mit.

**Konsequenz:** Entweder (a) Pilot wartet bis Härtung außerhalb des Antrags
abgeschlossen ist, oder (b) Härtung wird in den Antrag eingearbeitet und
Features verschoben.

---

## Drei Optionen für Toni — mit ehrlicher Bewertung

### Option A — Antrag wie eingereicht, Härtung in Eigenregie vor Pilot

**Pfad:** Härtung+2 (~3 Wochen Vollzeit, Maßnahmen 1–14) wird vor
Bewilligung oder parallel zu M0-Start gemacht — unbezahlt oder aus Eigenmitteln.
Pilot-Onboarding erst nach Härtung.

**Pro:**
- Antrag muss nicht angefasst werden,-Verfahren läuft störungsfrei
- Härtung passiert zur richtigen Zeit (vor Pilot)
- Klare Trennung: Eigenleistung = Härtung, = Features

**Kontra:**
- 3 Wochen unbezahlte Arbeit zusätzlich zu 680h-Arbeit
- Im-Reporting fehlt der Härtungs-Erfolg, obwohl er substanziell ist
- Wenn Toni nebenher Job sucht (laut Memory), ist die Doppelbelastung real

**Empfehlung:** Realistisch nur, wenn Toni explizit 3 Wochen Reserve hat oder
wenn Härtung als Teil des Stellenkontextes (Prototyper-Demo) doppelt verwertbar
ist.

### Option B — Stage-2-Gespräch nutzen, Milestones nachschärfen

**Pfad:** Wenn die Bewilligung kommt, im Stage-2-Gespräch proaktiv ansprechen:
„Audit hat kritische DSGVO-Findings ergeben. Wir möchten M3, M4, M6 leicht
erweitern und M1 etwas straffen, um Härtung zu integrieren." ist
erfahrungsgemäß flexibel bei nachweislich guten Argumenten.

**Konkrete Umpackung:**
- **M1** (Conditional Field Logic): 120h → 100h, 20h einsparen, Stretch-Goals
 rausnehmen
- **M3** (Accessibility): 80h → 90h, +10h für Forms-Zentralisierung als
 Vorleistung
- **M4** (Data Portability): 100h → 130h, +30h für vollständigen
 DSGVO-Lifecycle (Art. 15/16/17) statt nur Art. 16
- **M5** (Reporting): 100h → 120h, +20h für sichere Referenz-Implementation
 (K-Anonymität, Aggregation-Only Snapshot)
- **M6** (Deployment): 80h → 100h, +20h für `MEDIA_ROOT`-Volume,
 Image-Pinning, Off-Site-Backup-Hook, SBOM/Cosign

Das wäre eine Verschiebung von ~80h aus M0–M2 in M3–M6 (oder Budget-Erhöhung
auf 760h / €38.000 — auch im-Rahmen denkbar).

**Pro:**
- Härtung wird bezahlt
--Reporting deckt das wichtige Feedback aus dem Audit ab
- Stage-2-Gespräch zeigt Reife („wir haben uns auditieren lassen")

**Kontra:**
- M0–M2 (die Feature-Highlights) werden kleiner — das ist genau das, was
 Reviewer in Stage 1 attraktiv fanden
- Erfordert Verhandlungsbereitschaft und Dokumentation der Audit-Findings
- Nicht alle fallen rein (RLS-Test, Service-Layer-Sweep
 bleiben außerhalb)

**Empfehlung:** Realistisch und ehrlich. Ich würde das so anbieten — vor
allem M4-Erweiterung (DSGVO-Vollständigkeit) und M6-Erweiterung (Ops-Härtung)
sind so plausibel, dass kaum nein sagen kann.

### Option C — Härtung als separater Antrag (Prototype Fund / BMBF /
NGI Zero Core)

**Pfad:**-Antrag bleibt unverändert. Parallel ein zweiter Förderantrag
für Härtung — z. B. Prototype Fund (Härtungssprint), oder NGI Zero Core
(eigene Tranche), oder BMBF Software Sprints.

**Pro:**
- Maximale Klarheit der Scopes — Features hier, Härtung dort
- Härtung wird eigenständig sichtbar
- Bei Bewilligung beider zusammen sind ~9 Monate Vollarbeit gefördert

**Kontra:**
- Doppelter Antragsaufwand (Prototype Fund hat eigene Deadlines, auch)
- Risiko: nur einer wird bewilligt — wahrscheinlich der mit der besseren Story
- Härtungs-Antrag braucht eigene Begründung; ein „wir haben uns auditieren
 lassen, hier sind 24 ungedeckte Findings" wäre konkret und glaubwürdig
- Zeitlich ist die Härtung nötig **bevor** M0–M2 läuft, nicht parallel

**Empfehlung:** Nur sinnvoll, wenn ein konkreter zweiter Topf zeitnah
verfügbar ist. Prototype Fund Round 16 hatte Deadline ~Mitte 2026 (zu prüfen).
Andernfalls verzögert sich die Härtung um 6–12 Monate, was den-Pilot
untergräbt.

---

## Meine Empfehlung

**Option B + Mini-Option A.**

1. **Sofort, vor-Bewilligung (~2 Wochen Eigenleistung):**-
 Findings 1, 4, 5 erledigen — sind alle „S"-Aufwand: `MEDIA_ROOT`-Volume,
 Image-Pinning, AGPL-Footer. Das sind die Punkte, die einem Stage-2-Gespräch
 am meisten weh tun, wenn ein Reviewer das Repo testet.
2. **Im Stage-2-Gespräch:** Audit nennen, M4/M5/M6 nachschärfen wie oben. Die
 Audit-Existenz ist ein Pluspunkt, kein Minuspunkt — sie zeigt, dass das
 Projekt selbstkritisch ist.
3. **Im Verlauf der Förderung:** Maßnahmen 2, 3, 6 (EventHistory, RLS-Test) als vor M0 erledigen — als Teil von M6 deklariert oder als
 „Vorbedingung für Pilot-Onboarding". Das muss im Final-Report begründbar
 sein, ist aber inhaltlich gerechtfertigt.

**Was Toni jetzt vorbereiten sollte:**

- **Audit-Zusammenfassung als Dokument** für ein mögliches Stage-2-Gespräch.
 Das konsolidierte Audit (vorheriges Dokument) ist dafür schon brauchbar,
 könnte aber für auf 2 Seiten Englisch destilliert werden („8
 independent audits identified the following hardening priorities").
- **Mini-Roadmap** aus dieser Gap-Analyse: welche wann, von
 welcher Quelle finanziert, mit welchem Ergebnis. liest das gern.
- **Stage-2-Antwort-Vorlage** für die wahrscheinliche Frage „what's the
 production-readiness path?" — die Audit-Existenz ist hier Gold wert, weil
 sie eine sonst unangenehme Frage in eine konkrete Antwort verwandelt.

---

## Zwei Punkte, die in keinem der Audits standen, hier aber relevant werden

### 1. Pilot-Einrichtung als Stage-2-Frage

Drei Audits sagen: ohne IT-Begleitung nicht für die Zielgruppe betreibbar. wird in Stage 2 nach Pilot-Einrichtungen fragen. Die ehrliche Antwort
ist: „Barbara hat Kontakte aus dokumentationsberatung.de, eine
Pilot-Einrichtung wird im Förderzeitraum identifiziert." Das ist OK, aber
schwächer als „Einrichtung X hat zugesagt".

Wenn Toni vor Bewilligung eine konkrete Pilot-Zusage hat, ist das stärker
als jede Härtungs-Maßnahme.

### 2. Bus-Faktor 1 ist-Risiko

Der konsolidierte Audit nennt das. weiß, dass Solo-Entwickler-Projekte
ein Risiko sind. Die AI-Disclosure ist transparent, aber Reviewer fragen
„what if the maintainer stops?". Eine glaubwürdige Antwort braucht entweder
einen Co-Maintainer (unrealistisch in 2 Wochen) oder einen institutionellen
Anker — z. B. wenn eine Trägerinitiative die Codebase als Referenz übernimmt.

Das ist ein Punkt, an dem Barbara's Netzwerk strategisch gehoben werden könnte:
nicht nur „Pilotnutzer", sondern „institutional sponsor". Eine
sozialarbeiterische Trägerorganisation als Anlaufpunkt für
Maintainer-Übergabe wäre für ein starkes Signal.

— Ende Gap-Analyse.
