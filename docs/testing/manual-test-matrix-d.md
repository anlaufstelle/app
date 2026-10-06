# Manuelle Test-Matrix — Sektion D

> Teil der [Manual-Test-Matrix](manual-test-matrix.md) (Refs #1071 Block B). Setup-Block, Status-Legende, TC-ID-Schema, Browser-/Mobile-Konventionen und die Anhänge stehen im [Hub](manual-test-matrix.md); die Gesamtübersicht in `test-matrix-index.md`.

---

## SEKTION D — Entwickler-Probes (LOKAL/SSH)

> **Zielgruppe:** Tobias / Server-Admin. Diese Tests erfordern direkten Zugriff auf den Server (`docker compose exec web python manage.py …`, `psql`, lokale `manage.py`-Befehle). Auf [`dev.anlaufstelle.app`](https://dev.anlaufstelle.app) nur durch Tobias oder per SSH durchführbar. Sie verifizieren Schema-Constraints (`on_delete`-Verhalten, RLS-Force, Encryption-at-Rest, Hash-Ketten) und Betriebsfähigkeit (DB-Rollen, Backup/Restore, Retention-Cron), die in Anwender-Tests nicht prüfbar sind. Aktuelle Fallzahl: siehe `test-matrix-index.md`.

### D.CLIENT

#### DEV-CLIENT-15 — PROTECT: Klient mit aktivem Fall lässt sich nicht direkt löschen

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen / Kaskade | admin | — | — | `test_clients_protect.py` |

**Voraussetzung:** Klient:in mit mindestens einem `Case` (Status egal — entscheidend ist die FK-Beziehung).

**Code-Referenz:**
- `src/core/models/case.py:29-38` — `Case.client on_delete=PROTECT` mit explizitem help_text: „PROTECT verhindert versehentliches Löschen einer Person mit aktiven Fällen."

**Schritte:**
1. `python manage.py shell`.
2. `from django.db.models.deletion import ProtectedError`.
3. `Client.objects.get(id=<uuid>).delete()` direkt aufrufen.
4. Den Fall zuerst löschen (`Case.objects.filter(client_id=<uuid>).delete()`), dann erneut `Client.delete()`.
5. UI-Probe (optional): Versuch über Admin / DeletionRequest-Workflow — Fehlermeldung an User-Oberfläche prüfen.

**Erwartetes Ergebnis:**
- Schritt 3 wirft `ProtectedError` mit Verweis auf den blockierenden Case.
- Schritt 4 funktioniert: nach Case-Löschung lässt sich der Klient löschen.
- Über die UI führt der reguläre Pfad zu einem `DeletionRequest` (Soft-Delete), nicht zum Hard-Delete — der PROTECT-Constraint ist die letzte Verteidigungslinie.

**DSGVO/Security-Note:**
- Schützt vor unbeabsichtigtem Verlust von Fallhistorie (Art. 5 Abs. 1 lit. d Richtigkeit / lit. e Speicherbegrenzung) und vor Audit-Lücken (Art. 30 Verarbeitungsverzeichnis).

**Status:** ☐ Offen

### D.CASE

#### DEV-CASE-13 — CASCADE: Fall löschen entfernt Goals und Milestones

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle / Kaskade | admin | — | — | `test_cases_cascade.py` |

**Voraussetzung:** Fall mit ≥ 3 `OutcomeGoal` und je Goal ≥ 2 `Milestone` (Seed oder manuell anlegen).

**Code-Referenz:**
- `src/core/models/outcome.py:14` — `OutcomeGoal.case on_delete=CASCADE`.
- `src/core/models/outcome.py:40` — `Milestone.goal on_delete=CASCADE`.

**Schritte:**
1. `python manage.py shell` öffnen.
2. Case-UUID notieren, IDs der Goals/Milestones zählen (`Case.objects.get(...).goals.count()`, `goal.milestones.count()`).
3. `Case.objects.get(id=<uuid>).delete()` ausführen.
4. Erneut `OutcomeGoal.objects.filter(case_id=<uuid>).count()` und `Milestone.objects.filter(goal__case_id=<uuid>).count()`.
5. AuditLog prüfen: erfasst Lösch-Kette oder mindestens den Case-Delete?

**Erwartetes Ergebnis:**
- Alle abhängigen Goals + Milestones sind weg (Count = 0).
- Keine `IntegrityError`; PostgreSQL räumt per CASCADE auf.
- AuditLog: mindestens ein Eintrag für den Case-Delete (Cascade-Nebenwirkungen ggf. nicht protokolliert — Lücke dokumentieren).

**DSGVO/Security-Note:**
- Direktes `Case.delete()` umgeht den Vier-Augen-Workflow. In Produktion nur über `DeletionRequest`-Service erlaubt — dieser Test prüft das Schema, nicht den User-Pfad.

**Status:** ☐ Offen

---

#### DEV-CASE-14 — SET_NULL: Fall löschen löst Events ab, behält sie aber

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle / Kaskade | admin | — | — | `test_cases_cascade.py` |

**Voraussetzung:** Fall mit ≥ 2 zugeordneten `Event`s (`event.case = <fall>`).

**Code-Referenz:**
- `src/core/models/event.py:38-40` — `Event.case on_delete=SET_NULL, null=True`.

**Schritte:**
1. Shell: Event-UUIDs notieren, `Event.objects.filter(case_id=<uuid>).values_list('id', 'case_id')` ausgeben.
2. `Case.objects.get(id=<uuid>).delete()`.
3. Erneut `Event.objects.filter(id__in=[…]).values_list('id', 'case_id')`.
4. UI-Gegenprobe: Klient-Detail öffnen → Timeline zeigt die Events weiterhin (ohne Fall-Verlinkung).

**Erwartetes Ergebnis:**
- Events existieren weiter, `event.case_id` ist nun `NULL`.
- Keine Daten-Verluste an den Events (Texte, Anhänge bleiben).
- Klient-Timeline zeigt die Events ohne Fall-Badge.

**DSGVO/Security-Note:**
- Wichtig für Art. 5 Abs. 1 lit. e: Dokumentations-Inhalte (Beratungsverlauf) dürfen nicht durch Fall-Bereinigung verloren gehen.

**Status:** ☐ Offen

### D.DEL

#### DEV-DEL-06 — AuditLog: DELETION_REQUESTED + DELETION_APPROVED/REJECTED

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DeletionRequests | leitung + fachkraft + admin | — | — | `test_deletion_approval_audit.py` |

**Voraussetzung:** ENT-DEL-01 (Antrag auf ein Ereignis) + ENT-DEL-03 (Genehmigung) + ENT-DEL-04 (Ablehnung eines zweiten Antrags) durchgeführt; PKs der beiden `DeletionRequest`s (`dr_ok`, `dr_rej`) notiert.

**Schritte:**
1. Als `admin` (Einrichtungsadmin; `AuditLogListView` verlangt `FacilityAdminRequiredMixin`) `/audit/?action=deletion_requested`, `/audit/?action=deletion_approved` und `/audit/?action=deletion_rejected` aufrufen.
2. Gegenprüfung in der Shell: `AuditLog.objects.filter(target_type="DeletionRequest", target_id__in=[str(dr_ok.pk), str(dr_rej.pk)]).values_list("action", "user__username", "target_id", "detail")`.
3. Lösch-Eintrag des genehmigten Ereignisses: `AuditLog.objects.filter(action="delete", target_type="Event", target_id=str(dr_ok.target_id))`.
4. Idempotenz in der Shell: `request_deletion(event, fachkraft, "x")` (`core.services.events.deletion`) zweimal hintereinander auf ein Ereignis ohne offenen Antrag; `deletion_requested`-Einträge für dieses Ereignis zählen.

**Erwartetes Ergebnis:**
- Jede Workflow-Stufe hat **einen eigenen** Eintrag mit `target_type="DeletionRequest"`, `target_id=str(<dr>.pk)`, `detail={"target_event": "<event_pk>"}`:
  - `deletion_requested` (`DELETION_REQUESTED`) je Antrag, `user` = antragstellende Fachkraft — **fehlt er, ist der Testfall rot**;
  - `deletion_approved` (`DELETION_APPROVED`) für `dr_ok`, `user` = genehmigende Leitung — **fehlt er, ist der Testfall rot**;
  - `deletion_rejected` (`DELETION_REJECTED`) für `dr_rej`, `user` = ablehnende Leitung — **fehlt er, ist der Testfall rot**.
- `detail` enthält keine Begründung (`reason` lebt nur in `DeletionRequest.reason`, Refs #1093).
- Schritt 3: zusätzlich genau ein `delete`-Eintrag (`target_type="Event"`, `detail={document_type, client_id, occurred_at}`) aus `soft_delete_event` — nur für den genehmigten Antrag, keiner für `dr_rej`.
- Schritt 4: ein laufender Antrag wird zurückgegeben, kein zweiter `deletion_requested`-Eintrag.

**Code-Beleg:** `create_or_get_event_deletion_request`, `approve_deletion`, `reject_deletion` (`src/core/services/events/deletion.py`), `soft_delete_event` (`src/core/services/events/crud.py`), `AuditLog.Action.DELETION_*` (`src/core/models/audit.py`); Unit-Tests `test_deletion_request_creation_writes_audit`, `test_request_deletion_is_idempotent_no_duplicate_audit`, `test_approve_deletion_writes_audit`, `test_reject_deletion_writes_audit` (`src/tests/test_deletion_approval_audit.py`).

**DSGVO/Security-Note:**
- Art. 5 Abs. 2 (Rechenschaftspflicht, Refs #932): Antrag, Genehmigung und Ablehnung stehen je als eigener, hash-verketteter AuditLog-Eintrag im Audit-Trail; die `DeletionRequest`-Tabelle allein genügt dafür nicht.

**Status:** ☐ Offen

### D.RET

#### DEV-RET-02 — Bulk-Approve: 5 ablaufende Einträge genehmigen

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Aufbewahrung | leitung | C/F/S | ⚪ | `test_retention_dashboard.py` |

**Voraussetzung:** ENT-RET-01, mindestens 5 ablaufende Einträge im Tab „Ablaufende"

**Vorbereitung:**
- `/retention/` geöffnet, Tab „Ablaufende".
- 5 Checkboxen markiert.

**Schritte:**
1. „Bulk-Approve" klicken.
2. Bestätigungsdialog mit „Genehmigen" bestätigen.
3. Anschließend `python manage.py enforce_retention` ausführen.
4. Auf Detail eines genehmigten Eintrags navigieren.

**Erwartetes Ergebnis:**
- 5 Einträge verlassen den Tab „Ablaufende" und erscheinen in „Historie" als „Genehmigt".
- Nach Cron-Lauf: Anonymisierung sichtbar (Pseudonym ersetzt, sensitive Felder geleert).
- Audit-Eintrag pro Datensatz mit Aktor:in `leitung`.

**DSGVO/Security-Note:**
- Auto-Anonymisierung statt Hard-Delete für statistische Verwertbarkeit (Art. 4 Nr. 5 Pseudonymisierung).

**Status:** ☐ Offen

---

#### DEV-RET-04 — Bulk-Reject: 2 Einträge ablehnen → Hard-Delete in Sicht

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Aufbewahrung | leitung | C/F/S | ⚪ | `test_retention_dashboard.py` |

**Voraussetzung:** ENT-RET-01, mindestens 2 ablaufende Einträge

**Vorbereitung:**
- `/retention/` geöffnet, Tab „Ablaufende".

**Schritte:**
1. 2 Einträge markieren.
2. „Bulk-Reject" klicken, Bestätigung.
3. Auf nächsten Cron-Lauf via `python manage.py enforce_retention` warten.
4. Anschließend in Admin/DB nach IDs der Einträge suchen.

**Erwartetes Ergebnis:**
- Einträge wandern in „Historie" als „Abgelehnt — wird gelöscht".
- Nach Cron-Lauf: Hard-Delete (Datensatz nicht mehr in DB, nur Audit-Stub bleibt).
- Audit-Eintrag dokumentiert „Hard-Delete genehmigt durch leitung".

**DSGVO/Security-Note:**
- Hard-Delete = Art. 17 Recht auf Löschung. Audit-Stub minimal (nur Aktion, Aktor:in, Zeitpunkt — keine personenbezogenen Daten).

**Status:** ☐ Offen

---

#### DEV-RET-05 — Hold auf einzelnen Eintrag setzen → Auto-Löschung blockiert

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Aufbewahrung | leitung | C | ⚪ | `test_retention_dashboard.py` |

**Voraussetzung:** ENT-RET-01, ein ablaufender Eintrag mit Frist < 7 Tage

**Vorbereitung:**
- `/retention/` geöffnet, Tab „Ablaufende".

**Schritte:**
1. Auf Detail eines ablaufenden Eintrags klicken.
2. „Hold setzen" wählen, Begründung („Laufendes Verfahren") eingeben.
3. Hold-Dauer wählen (z.B. „unbegrenzt").
4. `python manage.py enforce_retention` ausführen.
5. Eintrag-Status prüfen.

**Erwartetes Ergebnis:**
- Eintrag wechselt in Tab „Holds".
- POST an `/api/retention/<uuid>/hold/` erfolgreich (HTTP 200).
- Cron-Lauf überspringt Eintrag (kein Anonymize/Delete).
- Audit-Log: „Hold gesetzt durch leitung".

**DSGVO/Security-Note:**
- Hold = berechtigtes Interesse (z.B. laufende Ermittlung) sticht Löschpflicht (Art. 17 Abs. 3 lit. b/e).

**Status:** ☐ Offen

---

#### DEV-RET-06 — Hold dismissen → Frist läuft normal weiter

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Aufbewahrung | leitung | C | ⚪ | `test_retention_dashboard.py` |

**Voraussetzung:** ENT-RET-05 abgeschlossen — Eintrag im Hold-Tab

**Vorbereitung:**
- `/retention/` geöffnet, Tab „Holds".

**Schritte:**
1. Eintrag aus Hold-Tab öffnen.
2. „Hold aufheben" klicken, Begründung („Verfahren abgeschlossen").
3. `python manage.py enforce_retention` ausführen.
4. Eintrag-Status prüfen.

**Erwartetes Ergebnis:**
- Eintrag wechselt zurück nach „Ablaufende" oder direkt in „Historie/Anonymisiert", falls Frist bereits überschritten.
- Audit dokumentiert „Hold dismissed durch leitung".

**DSGVO/Security-Note:**
- Nach Wegfall des Hold-Grundes greift Löschpflicht wieder (Art. 17 Abs. 1).

**Status:** ☐ Offen

---

#### DEV-RET-07 — retention_anonymous_days = 90 → Anonyme Klient:in nach 90 Tagen

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Aufbewahrung | admin | C | ⚪ | `test_retention_dashboard.py` |

**Voraussetzung:** Settings `retention_anonymous_days = 90`; Anonymous-Klient:in (kein Echtname, nur Pseudonym) seit 91 Tagen.

**Vorbereitung:**
- DB-Backdate: `UPDATE core_client SET created_at = NOW() - INTERVAL '91 days' WHERE sensitivity = 'anonymous';`
- `python manage.py enforce_retention --dry-run`.

**Schritte:**
1. Dry-Run-Output prüfen.
2. `python manage.py enforce_retention` (live).
3. Klient-Detail aufrufen.

**Erwartetes Ergebnis:**
- Dry-Run listet anonymen Klient als „würde anonymisiert".
- Nach Live-Run: Pseudonym auf Hash-Prefix gekürzt, Notizen geleert.
- Audit-Eintrag „auto-anonymized (anonymous_days=90)".

**DSGVO/Security-Note:**
- Niedrigste Sensitivität = kürzeste Frist (Datenminimierung Art. 5 Abs. 1 lit. c).

**Status:** ☐ Offen

---

#### DEV-RET-08 — retention_identified_days = 365 → Identifiziert nach 1 Jahr

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Aufbewahrung | admin | C | ⚪ | `test_retention_dashboard.py` |

**Voraussetzung:** Settings `retention_identified_days = 365`; identifizierte:r Klient:in seit 366 Tagen.

**Vorbereitung:**
- DB-Backdate für `sensitivity='identified'`.
- Cron noch nicht gelaufen.

**Schritte:**
1. `/retention/` Tab „Ablaufende" — Klient sollte erscheinen.
2. Bulk-Approve auswählen.
3. `python manage.py enforce_retention`.
4. Klient-Detail prüfen.

**Erwartetes Ergebnis:**
- Klient erscheint im Tab „Ablaufende" mit Hinweis „Frist: 365 Tage".
- Nach Approve + Cron: Anonymisierung (Echtname → Pseudonym, Adresse geleert).
- Verknüpfte Events bleiben mit anonymisiertem Klient-Bezug.

**DSGVO/Security-Note:**
- Mittlere Sensitivität = Standardfrist 1 Jahr (Art. 5 Abs. 1 lit. e Speicherbegrenzung).

**Status:** ☐ Offen

---

#### DEV-RET-09 — retention_qualified_days = 3650 → Qualifiziert (Pflicht) 10 Jahre

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Aufbewahrung | admin | C | ⚪ | `test_retention_dashboard.py` |

**Voraussetzung:** Settings `retention_qualified_days = 3650`; qualifizierte:r Klient:in (z.B. Hilfeplan §27 SGB VIII) seit 1 Jahr.

**Vorbereitung:**
- Klient mit `sensitivity='qualified'` angelegt.
- Cron läuft täglich.

**Schritte:**
1. `/retention/` Tab „Ablaufende" prüfen → Klient darf NICHT erscheinen.
2. DB-Backdate auf 3651 Tage.
3. `/retention/` erneut prüfen.
4. `python manage.py enforce_retention --dry-run`.

**Erwartetes Ergebnis:**
- Vor Backdate: Klient nicht in „Ablaufende" (Frist 10 Jahre).
- Nach Backdate: Klient erscheint zur Anonymisierungs-Freigabe.
- Dry-Run dokumentiert geplante Aktion mit Frist-Berechnung.

**DSGVO/Security-Note:**
- Höchste Sensitivität = längste Pflicht-Aufbewahrung (Schnittstelle SGB VIII §62 ff., AO §147 Abs. 3 — überlagert Art. 17 DSGVO als spezialgesetzliche Pflicht).

**Status:** ☐ Offen

---

#### DEV-RET-10 — `enforce_retention --dry-run` zeigt Aktionen ohne Schreiben

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Aufbewahrung | admin (CLI) | — | ⚪ | `test_retention_dashboard.py` |

**Voraussetzung:** ENT-RET-07/08/09 (mehrere ablaufende Datensätze in DB)

**Vorbereitung:**
- Shell-Zugang mit Django-Env.
- DB-Snapshot vor Lauf (`pg_dump`).

**Schritte:**
1. `python manage.py enforce_retention --dry-run` ausführen.
2. Output lesen (geplante Aktionen pro Tabelle).
3. DB-Snapshot mit aktueller DB vergleichen.

**Erwartetes Ergebnis:**
- Stdout listet pro Datensatz: ID, Typ, geplante Aktion (Anonymize/Delete), Begründung.
- DB unverändert (kein Schreibvorgang).
- Exit-Code 0.

**DSGVO/Security-Note:**
- Trockenlauf = Vorab-Kontrolle vor unwiderruflicher Anonymisierung/Löschung (Risikofolgenabschätzung Art. 35).

**Status:** ☐ Offen

---

#### DEV-RET-11 — Event-Sensitivity beeinflusst Aufbewahrung (HIGH vs. NORMAL)

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Aufbewahrung | admin | C | — | `test_retention_sensitivity.py` |

**Voraussetzung:** Zwei Events am gleichen Klienten — eines mit `sensitivity=NORMAL`, eines mit `sensitivity=HIGH` (über DocumentType-Mindeststufe gesteuert).

**Code-Referenz:**
- `src/core/services/retention.py` — Frist-Berechnung pro Sensitivity.
- `src/core/models/event.py` — `sensitivity`-Feld + Default-Logik.

**Schritte:**
1. DB-Backdate beide Events um genau das Maximum der NORMAL-Frist (z.B. `retention_event_normal_days`).
2. `python manage.py enforce_retention --dry-run` ausführen.
3. Output filtern auf die beiden Event-IDs.
4. Backdate +1 Tag, erneut `--dry-run`.

**Erwartetes Ergebnis:**
- NORMAL-Event erscheint zur Anonymisierung, HIGH-Event nicht (längere Frist).
- Falls Code nur eine einheitliche Frist kennt: TC dokumentiert die Lücke, Backlog-Eintrag mit Verweis auf SGB VIII §62 ff.
- AuditLog enthält keine Anonymisierungs-Aktion (Dry-Run).

**DSGVO/Security-Note:**
- Art. 5 Abs. 1 lit. e (Speicherbegrenzung) — Sensitivität rechtfertigt unterschiedliche Aufbewahrungsdauern; Pflichtfristen (SGB VIII §62 Abs. 3, AO §147) überlagern Art. 17.

**Status:** ☐ Offen

---

#### DEV-RET-12 — `enforce_retention` räumt liegengebliebene Import-Läufe ab (STALE_DAYS=7)

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Aufbewahrung | admin (CLI) | — | — | `test_import_engine.py::TestCleanupStaleImportJobs` |

**Voraussetzung:** ein CSV-Import-Lauf im Status `uploaded`/`mapped`/`validated` (nicht abgeschlossen).

**Code-Referenz:**
- `src/core/services/import_engine.py` (`cleanup_stale_import_jobs`, `STALE_DAYS = 7`)
- `src/core/management/commands/enforce_retention.py` — ruft `cleanup_stale_import_jobs(facility)` je Einrichtung im regulären Lauf mit auf.

**Schritte:**
1. Einen Import-Lauf anlegen, nicht abschließen (weder Übernahme noch Abbruch).
2. Per DB-Backdate `created_at` des Laufs auf mehr als 7 Tage zurückdatieren.
3. `python manage.py enforce_retention --dry-run` ausführen — Ausgabe auf einen Hinweis zum Import-Lauf prüfen.
4. `python manage.py enforce_retention` (ohne `--dry-run`) ausführen.
5. Lauf in der DB / unter `/import/<uuid>/` erneut prüfen; Quelldatei auf der Platte prüfen.
6. Einen frischen (< 7 Tage alten) Lauf gegenprüfen — bleibt unangetastet.

**Erwartetes Ergebnis:**
- Der liegengebliebene Lauf wird auf `aborted` gesetzt, seine verschlüsselte Quelldatei von der Platte entfernt.
- AuditLog-Eintrag `import_aborted` mit `detail.reason=stale`.
- Ein bereits abgeschlossener (committed/aborted) oder ein frischer Lauf bleibt unangetastet.
- Der Cleanup ist je Einrichtung gescopt — ein liegengebliebener Lauf einer anderen Facility wird von diesem Aufruf nicht angefasst.

**DSGVO/Security-Note:**
- Art. 5 Abs. 1 lit. e (Speicherbegrenzung) — verhindert, dass abgebrochene/vergessene Import-Läufe unbegrenzt eine verschlüsselte Kopie roher Klientendaten auf der Platte halten.

**Status:** ☐ Offen

### D.STAT

#### DEV-STAT-03 — Snapshot via `python manage.py create_statistics_snapshots`

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Statistik | admin (CLI) | — | ⚪ | `test_statistics_snapshot.py` |

**Voraussetzung:** Mindestens 1 Monat Aktivitätshistorie

**Vorbereitung:**
- Shell mit Django-Env.

**Schritte:**
1. `python manage.py create_statistics_snapshots` ausführen.
2. Output prüfen.
3. In `/statistics/` neuen Snapshot-Eintrag suchen.
4. Erneut ausführen — Idempotenz prüfen.

**Erwartetes Ergebnis:**
- Stdout dokumentiert erstellten Snapshot pro Facility/Periode.
- DB enthält neuen Eintrag in `core_statisticssnapshot`.
- Wiederholter Lauf legt keine Duplikate an.

**DSGVO/Security-Note:**
- Snapshot speichert nur Aggregat-Werte (Art. 5 Datenminimierung).

**Status:** ☐ Offen

---

#### DEV-STAT-10 — Mobile-Stats (responsive Charts mit Chart.js)

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Statistik | leitung | C | ⚪ | `test_statistics_charts.py` |

**Voraussetzung:** Browser-DevTools mit iPhone-Profil. ENT-STAT-01.

**Vorbereitung:**
- Mobile-Viewport.

**Schritte:**
1. `/statistics/` aufrufen.
2. Charts beobachten beim Drehen (Portrait/Landscape).
3. Tap auf Chart-Datapoint → Tooltip prüfen.
4. Filter-Selectoren auf Mobile bedienen.

**Erwartetes Ergebnis:**
- Charts skalieren auf Viewport-Breite.
- Achsenbeschriftung lesbar (ggf. rotiert).
- Tooltip auf Tap sichtbar (Touch-tauglich).
- Filter-Dropdown nutzt natives Mobile-UI.
- KPI-Karten stapeln sich vertikal.

**DSGVO/Security-Note:**
- Mobile-Cache: `Cache-Control: no-store` für Stat-Seite, da Aggregate auf Geräte-Ebene nicht persistiert werden sollen.

**Status:** ☐ Offen

### D.AUDIT

#### DEV-AUDIT-04 — Append-Only-Probe (DSGVO-Beleg Art. 5)

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Audit | admin (Shell) | — | ⚪ | `test_audit_append_only_e2e.py` |

**Voraussetzung:** mindestens ein AuditLog-Eintrag in der DB.

**Vorbereitung:**
- SSH/Terminal-Zugang. `python manage.py shell` startbar.

**Schritte:**
1. `python manage.py shell` ausführen.
2. `from core.models.audit import AuditLog`
3. `entry = AuditLog.objects.first()`
4. `entry.action = "TAMPERED"; entry.save()` versuchen → erwartet `ValueError` oder `IntegrityError`.
5. `entry.delete()` versuchen → erwartet `ValueError` oder gleiche Exception.
6. DB-Direktzugriff: `psql` → `UPDATE core_auditlog SET action='X' WHERE id=...;` → Trigger blockiert (PG-Exception).

**Erwartetes Ergebnis:**
- Schritt 4: ValueError mit Message wie „AuditLog ist append-only, save() nicht erlaubt".
- Schritt 5: ValueError beim delete().
- Schritt 6: PostgreSQL-Trigger wirft Exception, UPDATE/DELETE rollt zurück.

**DSGVO/Security-Note:**
- **DSGVO-Beleg Art. 5 (Integrität & Vertraulichkeit):** Audit-Log ist immutable und damit gerichtsfest.
- Append-Only ist mehrlagig: Django-Model-Override + PostgreSQL-Trigger (Defense-in-Depth).
- Auch Superuser/Admin-User kann Audit-Log nicht manipulieren.

**Status:** ☐ Offen

---

#### DEV-AUDIT-07 — Cross-Facility-Isolation (RLS)

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Audit | admin (zwei Facilities) | C/F/S | ⚪ | `test_audit.py` |

**Voraussetzung:** zwei Facilities mit je eigenen Admin-Usern und je eigenen Audit-Einträgen.

**Vorbereitung:**
- Facility A: Admin-A loggt ein, erzeugt 5 Audit-Einträge.
- Facility B: Admin-B loggt ein, erzeugt 5 Audit-Einträge.

**Schritte:**
1. Als Admin-A in Facility A einloggen.
2. `/audit/` aufrufen — nur die 5 Audit-Einträge der Facility A sichtbar.
3. Versuch: `/audit/<uuid-von-facility-B>/` direkt aufrufen → 404 (RLS blockt).
4. Als Admin-B einloggen, gleicher Test umgekehrt.
5. SQL-Probe: `psql` ohne `SET app.current_facility_id` → 0 Audit-Einträge sichtbar (RLS-Default-Deny).

**Erwartetes Ergebnis:**
- Admin sieht ausschließlich Audit-Einträge der eigenen Facility.
- Direkt-URL auf fremde Facility → 404.
- RLS auf DB-Ebene aktiv (Defense-in-Depth, nicht nur Django-Filter).

**DSGVO/Security-Note:**
- **DSGVO Art. 32 (Mandantentrennung):** Audit-Log ist facility-gescoped via RLS.
- Auch ein kompromittierter Admin-Account kann nicht über Facility-Grenzen lesen.
- AuditLog-Tabelle in `JOIN_TABLES` der RLS-Migration `0047_postgres_rls_setup.py` registriert.

**Status:** ☐ Offen

### D.DSGVO

#### DEV-DSGVO-Art17-03 — Auto-Anonymisierung mit k-Anonymität

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | admin | C/F/S | — | `src/tests/test_k_anonymization.py`, `src/tests/test_anonymize_residue.py` |

**DSGVO-Artikel-Zitat:** *Art. 17 — Tatsächliche Löschung oder Anonymisierung nach Ablauf der Aufbewahrungsfrist.*

**Code-Referenz:**
- `src/core/retention/anonymization.py` (`anonymize_clients`)
- `src/core/services/k_anonymization.py` (k=5 Default)
- `src/core/management/commands/enforce_retention.py`

**Voraussetzung:** Backdate-Daten (Klient:in mit `created_at` vor 400 Tagen).

**Schritte:**
1. SQL: `UPDATE core_client SET created_at = NOW() - INTERVAL '400 days' WHERE id = '<uuid>'`.
2. `python manage.py enforce_retention --dry-run` ausführen → zeigt geplante Anonymisierungen.
3. `python manage.py enforce_retention` ausführen.
4. Klient:in in DB prüfen: `pseudonym` ist auf k-anon-Cluster gesetzt, alle direkt identifizierenden Felder leer.
5. Events der Klient:in: Sensitive Inhalte gelöscht oder generalisiert.
6. AuditLog: `CLIENT_ANONYMIZED` mit Cluster-Hinweis.

**Erwartetes Ergebnis:**
- k-Anonymität: Klient:in ist mit ≥ 4 anderen ununterscheidbar (k=5).
- Audit-Spur dokumentiert Anonymisierung.

**Erwarteter Audit-Eintrag:** `CLIENT_ANONYMIZED` mit `anonymization_run_id`.

**Status:** ☐ Offen

---

#### DEV-DSGVO-Art18-01 — Hold-Mechanismus blockiert Auto-Löschung

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | leitung | C/F/S | — | `test_retention_dashboard.py` |

**DSGVO-Artikel-Zitat:** *Art. 18 — Recht auf Einschränkung der Verarbeitung (z.B. bei Streit um Richtigkeit).*

**Code-Referenz:**
- `src/core/views/retention.py` (`RetentionHoldView`, `RetentionDismissHoldView`)
- `src/core/models/legalhold.py` (`LegalHold`)

**Voraussetzung:** Backdate-Daten, Retention-Dashboard offen.

**Schritte:**
1. Mit `leitung` einloggen, `/retention/` öffnen.
2. Eintrag mit ablaufender Frist auswählen → **„Hold setzen"** → Begründung („Klient:in widerspricht Löschung").
3. `python manage.py enforce_retention --dry-run` → der Eintrag erscheint **nicht** in der Anonymisierungs-Liste.
4. Hold dismissen → der Eintrag erscheint wieder.

**Erwartetes Ergebnis:**
- Hold blockiert Auto-Löschung dauerhaft, bis er aufgehoben wird.
- AuditLog: `RETENTION_HOLD_SET` und `RETENTION_HOLD_DISMISSED`.

**Status:** ☐ Offen

---

#### DEV-DSGVO-Art25-01 — RLS aktiv vor App-Logik

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | admin | — | — | `src/tests/test_rls.py` |

**DSGVO-Artikel-Zitat:** *Art. 25 — Datenschutz durch Technik („Privacy by Design").*

**Code-Referenz:**
- `src/core/migrations/0047_postgres_rls_setup.py`
- `src/core/middleware/facility_scope.py`
- `src/tests/test_rls.py` (`EXPECTED_TABLES`)

**Migrations-Referenz:** `0047_postgres_rls_setup`, ggf. Folge-Migrationen für neue Tabellen.


**Schritte:**
1. PostgreSQL Connect: `sudo docker compose exec db psql -U postgres anlaufstelle`.
2. SQL: `SELECT tablename FROM pg_tables WHERE schemaname='public' AND rowsecurity=true;`
3. Verifizieren: alle Tabellen aus `EXPECTED_TABLES` (in `src/tests/test_rls.py`) sind RLS-aktiviert.
4. SQL: `SELECT tablename, forcerowsecurity FROM pg_tables WHERE rowsecurity=true;` — alle haben `FORCE ROW LEVEL SECURITY`.
5. SQL ohne `app.current_facility_id`: `SET app.current_facility_id TO ''; SELECT count(*) FROM core_client;` → 0 (keine Daten ohne Facility-Kontext).

**Erwartetes Ergebnis:**
- RLS aktiv auf 19+ Tabellen.
- Ohne gesetzten `app.current_facility_id` keine Daten sichtbar — selbst für Superuser auf SQL-Ebene (FORCE).

**Status:** ☐ Offen

---

#### DEV-DSGVO-Art30-01 — Verarbeitungsverzeichnis-Template

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | admin | C/F/S | — | `test_dsgvo_package.py` |

**DSGVO-Artikel-Zitat:** *Art. 30 — Verzeichnis von Verarbeitungstätigkeiten.*

**Code-Referenz:**
- `src/core/management/commands/generate_dsgvo_package.py`
- `src/core/services/dsgvo_package.py`

**Voraussetzung:** Sudo-Mode aktiv.

**Schritte:**
1. `python manage.py generate_dsgvo_package` ausführen → erzeugt Markdown-Templates.
2. Mit `admin` einloggen, Sudo-Mode betreten, `/dsgvo/` öffnen.
3. **„Verarbeitungsverzeichnis"** herunterladen.
4. Inhalt prüfen:
   - Verarbeitungszwecke benannt (Soziale Beratung, Falldokumentation).
   - Rechtsgrundlage (Art. 6 Abs. 1 lit. e — öffentliches Interesse).
   - Datenkategorien aufgeführt.
   - Empfänger benannt (intern/extern).
   - Speicherdauer aus `settings.retention_*_days`.
   - TOMs verlinkt (siehe Art. 32-Template).

**Erwartetes Ergebnis:**
- Template ist vollständig, Facility-spezifisch gerendert.

**Erwarteter Audit-Eintrag:** `EXPORT` mit `slug=verarbeitungsverzeichnis`.

**Status:** ☐ Offen

---

#### DEV-DSGVO-Art32-02 — Encryption-at-Rest (Fernet)

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | — | — | — | `src/tests/test_encryption.py` |

**DSGVO-Artikel-Zitat:** *Art. 32 — Verschlüsselung als TOM.*

**Code-Referenz:** `src/core/services/encryption.py` (`MultiFernet`, Key-Rotation)

**Voraussetzung:** ENCRYPTION_KEY gesetzt.

**Schritte:**
1. Event mit Inhalt anlegen (Klartext „Vertraulich-XYZ").
2. PostgreSQL Connect: `sudo docker compose exec db psql -U postgres anlaufstelle`.
3. SQL: `SELECT encrypted_data FROM core_event WHERE id = '<uuid>'`.
4. Verifizieren: Inhalt ist **base64-Fernet-Token**, nicht Klartext.
5. In Django-Shell: Event abrufen und `event.data` prüfen → Klartext sichtbar (Decrypt funktioniert).

**Erwartetes Ergebnis:**
- DB-Spalte enthält verschlüsselten Token.
- App-Layer entschlüsselt korrekt.

**Status:** ☐ Offen

---

#### DEV-DSGVO-Art32-04 — Passwort-Policy & Login-Lockout

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | — | C/F/S | — | `src/tests/test_auth.py` |

**DSGVO-Artikel-Zitat:** *Art. 32 — Schutz vor Brute-Force und schwachen Passwörtern.*

**Code-Referenz:**
- `src/anlaufstelle/settings/base.py` (`AUTH_PASSWORD_VALIDATORS`)
- `src/core/services/login_lockout.py`


**Schritte:**
1. Pwd-Wechsel-Versuch mit schwachem Passwort `12345678` → Fehler (zu kurz, < 12 Zeichen).
2. Pwd-Wechsel mit `password1234` → Fehler (Common-Password).
3. Pwd-Wechsel mit `Anlaufstelle2026!` → Erfolg.
4. Login mit `fachkraft` + falschem Pwd 11× hintereinander.
5. Nach 10. Versuch → AuditLog `LOGIN_LOCKED`, weitere Versuche → 429.
6. Admin entsperrt: `python manage.py shell -c "from core.services.login_lockout import unlock; unlock('fachkraft')"`.

**Erwartetes Ergebnis:**
- 12-Zeichen-Pflicht greift.
- Common-Password-Liste greift.
- Nach 10 Fehlversuchen: Lockout für 15 Min.

**Status:** ☐ Offen

---

#### DEV-DSGVO-Art33-34-01 — Breach-Detection-Command

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | admin | — | — | `src/tests/test_breach_detection.py`, `src/tests/test_breach_webhook_ssrf.py` |

**DSGVO-Artikel-Zitat:** *Art. 33 — Meldung an Aufsichtsbehörde binnen 72h. Art. 34 — Benachrichtigung der Betroffenen bei hohem Risiko.*

**Code-Referenz:**
- `src/core/services/breach_detection.py`
- `src/core/management/commands/detect_breaches.py`

**Voraussetzung:** mind. 11 fehlgeschlagene Logins eines Users.

**Schritte:**
1. 11 falsche Login-Versuche mit `fachkraft` (auslösen Lockout).
2. `python manage.py detect_breaches --since=1h` ausführen.
3. Output prüfen: Anomalie wird gemeldet (Login-Burst).
4. Webhook-Konfiguration prüfen: SSRF-Whitelist greift (kein `http://localhost`-Webhook).

**Erwartetes Ergebnis:**
- Anomalie erkannt und protokolliert.
- Webhook-Notification ausgelöst (sofern konfiguriert).

**Erwarteter Audit-Eintrag:** `BREACH_DETECTED` mit `category=login_burst`.

**Status:** ☐ Offen

### D.SEC

#### DEV-SEC-RLS-05 — `app.current_facility_id`-Tampering

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | — | — | — | `src/tests/test_rls.py` |

**Code-Referenz:** `src/core/middleware/facility_scope.py`


**Schritte:**
1. PostgreSQL Connect.
2. SQL: `SET app.current_facility_id TO '<facility-2-uuid>'; SELECT count(*) FROM core_client;`
3. Erwartung: nur Klient:innen aus Facility 2 sichtbar — gemäß RLS-Policy (basierend auf Session-Variable).
4. Test: Facility-1-Admin mit `SET app.current_facility_id TO '<facility-2-uuid>'` → kann er Facility-2-Daten sehen?
   - **Erwartung:** RLS-Policy sollte Owner-Check über `users.facility_id == app.current_facility_id` erzwingen — ggf. nicht direkt möglich, prüfen.

**Status:** ☐ Offen

---

#### DEV-SEC-RLS-07 — Cross-Facility DeletionRequest unsichtbar (Mandantentrennung)

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | fachkraft `miriam` + leitung `emma` (F1) + leitung `emma_1` (F2) | C | — | `test_rls.py` |

**Voraussetzung:** Seed mit zwei Einrichtungen (`--scale=medium`: F1 „Hauptstelle" mit `miriam`/`emma`, F2 „Zweigstelle Nord" mit `emma_1`); `emma` und `emma_1` haben das Recht „Löschbestätigung" (`can_confirm_deletion`, Seed-Default für Leitung).

**Code-Referenz:**
- `src/core/views/client_deletion.py` — `ClientDeleteRequestView` (Route `/clients/<uuid>/delete/`, `core:client_delete_request`).
- `src/core/views/event_deletion.py` — `DeletionRequestListView` (`/deletion-requests/`, `DeletionRequest.objects.for_facility(...)`) und `DeletionRequestReviewView` (`/deletion-requests/<uuid>/review/`, `core:deletion_review`, Objekt über `get_scoped_object`).
- `src/core/services/scoping.py` — `get_scoped_object` (Einrichtungsfilter auf `request.current_facility`).
- `src/core/middleware/facility_scope.py` — `request.current_facility` und `app.current_facility_id` für RLS.

**Schritte:**
1. F1: als `miriam` eine F1-Person öffnen → „Löschen beantragen" (`/clients/<uuid>/delete/`) → Begründung eingeben, absenden. UUID des neuen Antrags (`<F1-dr>`) notieren (Shell: `DeletionRequest.objects.latest("created_at").pk` oder Link in der Liste).
2. Positivkontrolle F1: als `emma` `/deletion-requests/` aufrufen → der Antrag steht in der Liste; `/deletion-requests/<F1-dr>/review/` liefert HTTP 200 mit Genehmigen/Ablehnen. **Nicht** entscheiden.
3. F2: als `emma_1` einloggen, `/deletion-requests/` aufrufen → Liste prüfen.
4. Als `emma_1` `/deletion-requests/<F1-dr>/review/` direkt aufrufen (GET), dann per POST Genehmigen versuchen (z. B. das Formular aus Schritt 2 mit `emma_1`-Session und deren CSRF-Token absenden).
5. Als `emma_1` `/clients/<F1-Person-uuid>/delete/` direkt aufrufen.
6. Nach Schritt 4: Status des Antrags und AuditLog prüfen (`DeletionRequest.objects.get(pk=<F1-dr>).status`, `AuditLog.objects.filter(target_type="DeletionRequest", target_id="<F1-dr>")`).
7. Optional in PostgreSQL mit der Laufzeitrolle der App (nicht der Admin-/BYPASSRLS-Rolle): `SET app.current_facility_id = '<F2-uuid>'; SELECT count(*) FROM core_deletionrequest WHERE id = '<F1-dr>';` → `0`; Gegenprobe mit `'<F1-uuid>'` → `1`.

**Erwartetes Ergebnis:**
- Schritt 2 (Positivkontrolle): HTTP 200 auf genau derselben Route. **Liefert schon `emma` 404, ist der Testfall ungültig** (Route oder Antrag falsch) — der 404 aus Schritt 4 belegt dann keine Mandantentrennung.
- Schritt 3: Liste zeigt nur F2-Anträge, kein F1-Eintrag.
- Schritt 4: GET und POST liefern HTTP 404, niemals 200 mit fremden Daten. Liefert die Route für `emma_1` 200 oder zeigt Inhalte des F1-Antrags, ist der Testfall rot.
- Schritt 5: HTTP 404 (`get_scoped_object(Client, …)`), kein Antragsformular für eine fremde Person.
- Schritt 6: Status bleibt `pending`; kein `deletion_approved`- oder `deletion_rejected`-Eintrag von `emma_1`.
- Schritt 7: F2-Kontext sieht die F1-Zeile nicht, F1-Kontext sieht sie. Sieht der F2-Kontext die Zeile, ist der Testfall rot (RLS-Policy fehlt oder die Rolle umgeht RLS).

**Code-Beleg:** Unit-Tests `TestDeletionEdgeCases::test_facility_scoping_prevents_cross_facility_review` (`src/tests/test_deletion_requests.py`, 404 auf `core:deletion_review` für eine Leitung der zweiten Einrichtung), `TestDeletionRequestRLSIsolation::test_deletion_request_invisible_across_facility` (`src/tests/test_rls.py`, RLS unter `rls_test_role`), `test_cross_facility_is_404` (`src/tests/test_deletion_request_withdraw.py`).

**DSGVO/Security-Note:**
- Art. 5 Abs. 1 lit. f (Integrität & Vertraulichkeit) + Art. 32 (TOM). RLS-Layer auf `core_deletionrequest` muss FORCE-aktiv sein, App-Filter zusätzlich.

**Status:** ☐ Offen

---

#### DEV-SEC-RLS-08 — Cross-Facility RectificationRequest unsichtbar (Mandantentrennung)

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | leitung (F1) + leitung_2 (F2) | C | — | `test_rectification_model.py::TestRectificationRequestRLSIsolation` |

**Voraussetzung:** zwei Facilities mit jeweils mindestens einem offenen `RectificationRequest`.

**Code-Referenz:**
- `src/core/views/rectification.py` — Listen-/Review-Views.
- `src/core/models/rectification.py` (`RectificationRequest.objects`-Manager, Facility-Scoping).
- Migration `0139_rls_rectificationrequest.py` — RLS-Policy.

**Schritte:**
1. F1: als `emma` einen Berichtigungsantrag für eine F1-Person anlegen (`/clients/<uuid>/rectification/new/`).
2. F2: als `emma_1` einloggen.
3. `/rectification/` aufrufen → Liste prüfen.
4. Direkt-URL des F1-Antrags aufrufen (`/rectification/<F1-uuid>/review/`) — Genehmigen/Ablehnen versuchen.
5. Optional in PostgreSQL: ohne `app.current_facility_id` `SELECT count(*) FROM core_rectificationrequest;` mit gesetztem F2-Kontext → 0 F1-Zeilen sichtbar.

**Erwartetes Ergebnis:**
- Liste zeigt nur F2-Anträge, kein F1-Eintrag.
- Direkt-URL liefert 404, niemals 200 mit fremden Alt-/Neu-Werten.
- AuditLog protokolliert keinen Zugriff auf den F1-Datensatz durch F2-User.

**DSGVO/Security-Note:**
- Art. 5 Abs. 1 lit. f (Integrität & Vertraulichkeit) + Art. 32 (TOM). Besonders sensibel, da die Review-Seite entschlüsselte Alt-/Neu-Werte anzeigt — ein RLS-Leck wäre hier ein direkter Klartext-Leak.

**Status:** ☐ Offen

---

#### DEV-SEC-MFA-03 — Recovery-Flow

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | fachkraft + admin | C/F/S | — | `test_mfa_scoping.py` |

**Schritte:**
1. Fachkraft hat alle Backup-Codes verloren + TOTP-App weg.
2. Admin entfernt MFA via `python manage.py shell` oder Admin-UI.
3. Fachkraft loggt sich neu ein → wird zu MFA-Setup geführt (falls `mfa_required=True`).
4. AuditLog: `MFA_DISABLED_BY_ADMIN`.

**Erwartetes Ergebnis:**
- Admin-Recovery möglich, vollständig auditiert.

**Status:** ☐ Offen

---

#### DEV-SEC-AUDIT-01 — Append-Only-Probe

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | admin | — | — | `src/tests/test_audit_signals.py`, `src/tests/test_audit_trigger.py` |

**Code-Referenz:** `src/core/models/audit.py` (`AuditLog.save()` raises on update, `delete()` raises)

**Schritte:**
1. `python manage.py shell` öffnen.
2. `from core.models import AuditLog`
3. `log = AuditLog.objects.first()`
4. `log.action = 'TAMPERED'; log.save()` → erwarte `ValueError` (Append-Only).
5. `log.delete()` → erwarte `ValueError`.
6. Direkt-SQL: `UPDATE core_auditlog SET action='X' WHERE id='<uuid>'` — falls DB-Trigger vorhanden: blockiert. Falls nur App-Layer: SQL umgeht App-Schutz, dann RLS+DB-Trigger ergänzen.

**Erwartetes Ergebnis:**
- App-Layer verhindert UPDATE/DELETE.
- Falls DB-Trigger vorhanden: auch SQL-Direkt-Tampering blockiert.

**Status:** ☐ Offen

---

#### DEV-SEC-AUDIT-02 — Hash-Kette intakt

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | admin | — | — | `src/tests/test_audit_service.py` |

**Code-Referenz:** `src/core/services/audit_hash.py`

**Schritte:**
1. `python manage.py shell` öffnen.
2. `from core.services.audit_hash import verify_chain`
3. `verify_chain()` → erwartet `True` (alle Einträge konsistent).
4. Direkt-SQL: ein älteres `hash_self`-Feld manipulieren.
5. `verify_chain()` erneut → erwartet `False` mit Position des Bruchs.

**Erwartetes Ergebnis:**
- Hash-Kette erkennt nachträgliche Manipulationen.

**Status:** ☐ Offen

---

#### DEV-SEC-ENC-01 — Re-Encrypt-Command

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | admin | — | — | `src/tests/test_encryption.py` |

**Code-Referenz:**
- `src/core/services/encryption.py` (MultiFernet-Rotation)
- `src/core/management/commands/reencrypt_fields.py`

**Voraussetzung:** ENCRYPTION_KEYS mit 2 Schlüsseln (alt+neu).

**Schritte:**
1. `ENCRYPTION_KEYS=NEW_KEY,OLD_KEY` setzen (neuer Key zuerst → für Encrypt).
2. `python manage.py reencrypt_fields --dry-run` → zeigt geplante Verschlüsselungs-Updates.
3. `python manage.py reencrypt_fields` → re-encrypt aller verschlüsselten Felder mit NEW_KEY.
4. ENCRYPTION_KEYS auf `NEW_KEY` reduzieren (OLD_KEY entfernen).
5. App neu starten → Daten weiterhin lesbar.

**Erwartetes Ergebnis:**
- Key-Rotation ohne Datenverlust.
- Audit: `ENCRYPTION_REENCRYPTED`.

**Status:** ☐ Offen

---

#### DEV-SEC-HEAD-01 — Header-Smoke gegen Prod-Mirror

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | — | — | — | `src/tests/test_security_hardening.py` |

**Schritte:**
1. `curl -sI https://anlaufstelle-prod-mirror.example/` → alle Sicherheits-Header gesetzt.
2. `curl -sI -H "Origin: http://evil.example" https://...` → CORS-Antwort prüfen (kein `Access-Control-Allow-Origin: *`).
3. Browser-DevTools → Security-Panel → kein „mixed content".
4. Optional: securityheaders.com gegen Prod (extern).

**Erwartetes Ergebnis:**
- A+ bei securityheaders.com.
- Keine offenen CORS-Lecks.

**Status:** ☐ Offen

---

### D.OPS

> Refs #903 (+ §4.6): Ops-/Self-Hosting-Härtung. DSGVO-relevant über Art. 5 (Speicherbegrenzung) und Art. 32 (TOM) — werden aus Sektion C verlinkt, leben aber als LOKAL/SSH-Probes hier.

#### DEV-OPS-01 — DB-Rollen-Check (App NOSUPERUSER, kein BYPASSRLS; Admin BYPASSRLS)

> 🔧 **LOKAL/SSH erforderlich.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Ops | admin | — | — | — |

**Code-Referenz:**
- [`docker-compose.dev.yml`](https://github.com/anlaufstelle/app/blob/main/docker-compose.dev.yml) — Bootstrap/App/Admin-Rollen
- [`deploy/postgres-init/01-app-role.sh`](https://github.com/anlaufstelle/app/blob/main/deploy/postgres-init/01-app-role.sh)
- Prod-Pfad: #902 (docker-compose.prod auf gleiches Modell)

**Schritte:**
1. `docker compose exec db psql -U $POSTGRES_USER $POSTGRES_DB`
2. `\du` ausführen — App-Rolle und Admin-Rolle prüfen.
3. App-Rolle: `Attributes` enthält **nicht** `Superuser`, **nicht** `Bypass RLS`.
4. Admin-Rolle: `Attributes` enthält `Bypass RLS` (für Maintenance/Migrationen).
5. Optional per SQL: `SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname IN ('<app>', '<admin>');`.

**Erwartetes Ergebnis:**
- App-Rolle: `rolsuper=f`, `rolbypassrls=f`.
- Admin-Rolle: `rolbypassrls=t`.
- Bei Abweichung: Healthcheck/Compliance-Dashboard (#919) muss `critical` melden.

**Status:** ☐ Offen

---

#### DEV-OPS-02 — Backup-Frische (jünger als 24h)

> 🔧 **LOKAL/SSH erforderlich.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Ops | admin | — | — | — |

**Code-Referenz:** `dev-ops/deploy/backup.sh` (dev-only), [`docs/ops-runbook.md`](https://github.com/anlaufstelle/app/blob/main/docs/ops-runbook.md)

**Schritte:**
1. SSH auf Host: `ssh <ssh-user>@dev.anlaufstelle.app`.
2. Backup-Verzeichnis prüfen (per Runbook): `ls -lh /var/backups/anlaufstelle/ | head`.
3. Neuestes Backup: `find /var/backups/anlaufstelle/ -mtime -1 -type f`.
4. Optional: Backup-Cron-Log einsehen (z.B. `journalctl -u anlaufstelle-backup` oder `/var/log/syslog`).

**Erwartetes Ergebnis:**
- Mindestens ein Backup jünger als 24 h.
- Dateigröße plausibel (≥ Vortagswert ± 30 %).
- Compliance-Dashboard (#919) zeigt `ok`.

**Status:** ☐ Offen

---

#### DEV-OPS-03 — Restore-Test gegen frische DB

> 🔧 **LOKAL/SSH erforderlich.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Ops | admin | — | — | — |

**Code-Referenz:** [`deploy/restore.sh`](https://github.com/anlaufstelle/app/blob/main/deploy/restore.sh) (falls vorhanden), Runbook „Disaster Recovery".

**Voraussetzung:** dedizierte Test-DB (z.B. `anlaufstelle_restore_test`), niemals gegen Produktiv-DB.

**Schritte:**
1. Frisches Backup auswählen (z.B. `*.sql.gz` aus DEV-OPS-02).
2. Leere Test-DB anlegen.
3. Backup einspielen (`pg_restore` oder `psql < dump.sql`).
4. Smoke-Query: `SELECT count(*) FROM core_client;`, `SELECT count(*) FROM core_event;`.
5. Optional Django-Side: `DATABASE_URL=...test_db python manage.py check`.

**Erwartetes Ergebnis:**
- Restore läuft ohne Fehler.
- Zählungen entsprechen Erwartung.
- Datum/Zeit des letzten erfolgreichen Restore-Tests wird im Compliance-Dashboard angezeigt.
- **DSGVO-Beleg:** Art. 32 Abs. 1 lit. c (Wiederherstellbarkeit).

**Status:** ☐ Offen

---

#### DEV-OPS-04 — Media-Volume Persistenz

> 🔧 **LOKAL/SSH erforderlich.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Ops | admin | — | — | — |

**Code-Referenz:** [`docker-compose.prod.yml`](https://github.com/anlaufstelle/app/blob/main/docker-compose.prod.yml) — Volume-Konfiguration für `src/media/`.

**Schritte:**
1. In App: Datei hochladen (z.B. via Klient-Detail → Attachment).
2. SSH: Datei im Host-Volume sichtbar (`docker compose exec web ls /app/src/media/...`).
3. `docker compose restart web` ausführen.
4. Datei in App weiterhin abrufbar (Download-Link funktioniert).
5. `docker compose down && docker compose up -d`.
6. Datei nach erneutem Up immer noch da.

**Erwartetes Ergebnis:**
- Anhänge überleben Container-Restart und Stack-Restart.
- Volume ist im Compose als persistenter Named-Volume oder Bind-Mount konfiguriert.

**Status:** ☐ Offen

---

#### DEV-OPS-05 — collectstatic nach Image-Pull erfolgreich

> 🔧 **LOKAL/SSH erforderlich.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Ops | admin | — | — | — |

**Code-Referenz:** `dev-ops/deploy/deploy-dev.sh` (dev-only) oder Entrypoint im App-Container.

**Schritte:**
1. Neues Image pullen: `docker compose pull web`.
2. Stack hochfahren: `docker compose up -d`.
3. Statics prüfen: `curl -I https://dev.anlaufstelle.app/static/css/styles.css` → 200.
4. Whitenoise-Manifest existiert: `docker compose exec web ls staticfiles/staticfiles.json` oder Pendant.
5. App lädt im Browser ohne fehlende Assets (DevTools-Network).

**Erwartetes Ergebnis:**
- Statics werden nach Pull automatisch eingesammelt (Entrypoint oder Compose-Command).
- Keine 404-Spam in Caddy-Logs.

**Status:** ☐ Offen

---

#### DEV-OPS-06 — Migrations-Drift (`migrate --check`)

> 🔧 **LOKAL/SSH erforderlich.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Ops | admin | — | — | `src/tests/test_migrations.py` (falls vorhanden) |

**Schritte:**
1. SSH: `docker compose exec web python manage.py migrate --check` → Exit-Code 0.
2. `docker compose exec web python manage.py makemigrations --check --dry-run` → keine ausstehenden Migrationen.
3. Optional `django_migrations`-Tabelle einsehen — neuestes Datum stimmt mit Code-Stand überein.

**Erwartetes Ergebnis:**
- Beide Checks Exit 0.
- Bei Drift: Deploy ist unvollständig — neu deployen oder manuell `migrate` ausführen.

**Status:** ☐ Offen

---

#### DEV-OPS-07 — Retention-Cron Output

> 🔧 **LOKAL/SSH erforderlich.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Ops | admin | — | — | `src/tests/test_retention_*.py` |

**Code-Referenz:**
- [`src/core/management/commands/enforce_retention.py`](https://github.com/anlaufstelle/app/blob/main/src/core/management/commands/enforce_retention.py)
- Cron/systemd-Timer-Konfiguration im Runbook.

**Schritte:**
1. SSH: Cron-Log einsehen — `enforce_retention` lief in den letzten 24 h.
2. Letzter erfolgreicher Lauf: Audit-Eintrag `RETENTION_EXECUTED` in AuditLog vorhanden.
3. `docker compose exec web python manage.py enforce_retention --dry-run` → zeigt aktuelle Pipeline.
4. Bei Fehlerfall: Audit-Eintrag `RETENTION_FAILED` mit Begründung.

**Erwartetes Ergebnis:**
- Cron läuft, Audit ist vollständig.
- Compliance-Dashboard (#919) zeigt Datum des letzten erfolgreichen Laufs.
- **DSGVO-Beleg:** Art. 5 Abs. 1 lit. e (Speicherbegrenzung).

**Status:** ☐ Offen

---

#### DEV-OPS-08 — Healthcheck unterscheidet ok/degraded/critical

> 🔧 **LOKAL/SSH erforderlich.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Ops | — | — | — | `src/tests/test_health_endpoint.py` (falls vorhanden) |

**Code-Referenz:** Health-View (siehe `ENT-SYS-01`, `ENT-SYS-02`).

**Schritte:**
1. `curl -s https://dev.anlaufstelle.app/health/` → JSON mit Status `ok`.
2. ClamAV-Container stoppen: `docker compose stop clamav`. (Lokal läuft der Dienst seit #1062 nur mit Profil — erst `docker compose --profile clamav up -d clamav`, dann stoppen. Auf dev/prod ist er unverändert profillos.)
3. Health-Endpoint erneut → erwartet `degraded` (App läuft, aber ClamAV down).
4. DB-Container stoppen: `docker compose stop db`.
5. Health-Endpoint erneut → erwartet `critical` (App nicht funktionsfähig).
6. Container wieder hochfahren, Status zurück auf `ok`.

**Erwartetes Ergebnis:**
- Drei Stufen sind unterscheidbar.
- Externes Monitoring (UptimeRobot o.ä.) kann auf `degraded` warnen statt erst auf hartem 5xx.

**Status:** ☐ Offen

---

#### DEV-OPS-09 — `export_facility` (Admin-DB-Rolle, Klartext-Datei wie Backup behandeln)

> 🔧 **LOKAL/SSH erforderlich.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Ops | admin | — | — | `src/tests/test_facility_export.py` |

**Code-Referenz:**
- [`src/core/management/commands/export_facility.py`](https://github.com/anlaufstelle/app/blob/main/src/core/management/commands/export_facility.py)
- Schema-Referenz: [`docs/facility-export-schema.md`](../facility-export-schema.md)
- Betrieb: [`docs/ops-runbook.md`](../ops-runbook.md) § 3.5

**Voraussetzung:** Admin-DB-Rolle (`$POSTGRES_ADMIN_USER`/`$POSTGRES_ADMIN_PASSWORD`) verfügbar.

**Schritte:**
1. Ohne Admin-Rollen-Override ausführen: `docker compose exec web python manage.py export_facility --facility "<Name>" --output /tmp/facility-export.json`.
2. Ueber den Admin-Maintenance-Pfad (Ops-Runbook § 3.5): `docker compose run --rm -v "$PWD/exports:/exports" maintenance export_facility --facility "<Name>" --output /exports/facility-export.json --indent 2`.
3. Ausgabedatei sichten: `schema_version`, `facility`, `configuration`, `entities`.
4. `--no-file-hashes` einmal gegenprüfen: Laufzeit spürbar kürzer, `sha256`-Felder der Attachments sind `null`.
5. Facility per UUID **und** per Name aufrufen; mehrdeutigen Namen (zwei Facilities mit demselben Namen, falls vorhanden) und unbekannte UUID/Namen gegenprüfen.
6. Ausgabedatei danach sicher löschen (`shred`/`rm`) — sie enthält entschlüsselten Klartext.

**Erwartetes Ergebnis:**
- Schritt 1: Command bricht **fail-loud** ab (kein leerer/stiller Export ohne Bypass).
- Schritt 2: Export läuft durch, Datei ist vollständiges, schema-konformes JSON.
- Schritt 4: `--no-file-hashes` überspringt das SHA-256-Streaming über die entschlüsselten Anhänge.
- Schritt 5: unbekannte UUID/unbekannter Name → `CommandError`; mehrdeutiger Name → `CommandError` mit Hinweis auf Eindeutigkeit per UUID.
- **DSGVO-Hinweis (nicht automatisiert prüfbar):** die Ausgabedatei ist wie ein DB-Backup zu behandeln — nur auf verschlüsselte Ziele schreiben, Übergabe nur an Berechtigte.

**DSGVO/Security-Note:**
- Art. 20 (Datenübertragbarkeit) + Art. 32 (TOM: Admin-Rollen-Zwang verhindert versehentlichen Export mit RLS-Lücken).

**Status:** ☐ Offen

---

#### DEV-OPS-10 — Orphan-Storage-Cleanup schützt offene Import-Uploads

> 🔧 **LOKAL/SSH erforderlich.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Ops | admin | — | — | `test_file_vault.py::TestStorageOrphanCleanup`, `test_cleanup_orphan_storage_command.py` |

**Code-Referenz:**
- `src/core/management/commands/cleanup_orphan_storage_files.py`
- `src/core/services/file_vault/cleanup.py` (`cleanup_orphan_storage_files`)
- [`docs/ops-runbook.md`](../ops-runbook.md) § 3.1 — `.enc`-Dateien ohne `Attachment`-**und** ohne offenen `ImportJob`-Record gelten als Waise; ein offener `ImportJob.storage_filename` schützt die Datei, solange der Lauf nicht `committed`/`aborted` ist.

**Voraussetzung:** ein CSV-Import-Lauf im Status `uploaded`/`mapped`/`validated` (offen, Quelldatei liegt noch verschlüsselt vor).

**Schritte:**
1. Import-Lauf anlegen, **nicht** abschließen — Quelldatei-Pfad notieren (`MEDIA_ROOT/<facility>/imports/<uuid>.enc`).
2. Mit Admin-DB-Rolle ausführen: `docker compose run --rm maintenance cleanup_orphan_storage_files`.
3. Quelldatei-Pfad erneut prüfen — noch vorhanden?
4. Denselben Lauf abschließen (Übernahme oder Abbruch) — `storage_filename` wird geleert.
5. Cleanup erneut ausführen.
6. Gegenprobe **ohne** Admin-DB-Rolle ausführen.

**Erwartetes Ergebnis:**
- Schritt 3: Datei ist **weiterhin vorhanden** — der offene `ImportJob` schützt sie, obwohl kein `Attachment`-Record existiert.
- Schritt 5: nach Abschluss des Laufs ist die (jetzt tatsächlich verwaiste, falls noch auf der Platte) Datei ein regulärer Löschkandidat.
- Schritt 6: Command bricht **fail-loud** ab, ohne Bypass sähe der Lauf 0 `ImportJob`-Zeilen und würde offene Uploads **aller** Einrichtungen fälschlich löschen (Refs #662, #1554, #1060).

**DSGVO/Security-Note:**
- Art. 5 Abs. 1 lit. e (Speicherbegrenzung) — der Guard verhindert zwei gegenläufige Fehler: verwaiste Dateien häufen sich (kein Cleanup) **oder** ein laufender Import verliert mitten im Wizard seine Quelldatei (zu aggressiver Cleanup).

**Status:** ☐ Offen

---

### D.PKG

> Refs #1058 (M2): Konfigurationspakete (YAML, Schema v1) werden über `manage.py load_package` eingespielt — ein reiner LOKAL/SSH-Pfad ohne UI. Format, Loader-Semantik und Pflegeregeln: [`docs/packages.md`](../packages.md), Begründung: [ADR-037](../adr/037-konfigurationspakete-dynamische-felder.md).
>
> **Gemeinsame Voraussetzung für D.PKG:** Facility-UUID zur Hand (`python src/manage.py shell -c "from core.models import Facility; print(*Facility.objects.values_list('id', 'name'), sep='\n')"`) und eine Kopie von `src/core/packages/default.yaml` als Arbeitsdatei (z. B. `/tmp/paket.yaml`). Die produktive `default.yaml` dabei **nicht** verändern.

#### DEV-PKG-01 — `load_package --dry-run` schreibt nichts und meldet die geplanten Änderungen

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfigurationspakete | admin | — | — | `test_load_package_command.py`, `test_packages_loader.py` |

**Voraussetzung:** Geseedete Einrichtung (`make seed`), Arbeitskopie `/tmp/paket.yaml`. **Rolle:** lokal genügt die Entwicklungs-Rolle (Superuser); im Container-Stack braucht der Aufruf den Admin-Rollen-Override (`-e POSTGRES_USER=$POSTGRES_ADMIN_USER …`, siehe [`docs/packages.md` § 4](../packages.md)) — sonst bricht der Command mit der RLS-Meldung ab.

**Code-Referenz:**
- `src/core/management/commands/load_package.py` — CLI-Fassade, Report-Ausgabe, `CommandError`-Übersetzung.
- `src/core/packages/loader.py` — Dry-Run über internen Rollback-Sentinel (identischer Code-Pfad).

**Schritte:**
1. In `/tmp/paket.yaml` einen sichtbaren Wert ändern, z. B. `icon` des Typs `kontakt` auf `star` und `help_text` der Vorlage `dauer` auf „Dauer in Minuten (Test)".
2. `python src/manage.py load_package --file /tmp/paket.yaml --facility <uuid> --dry-run` ausführen; Ausgabe und Exit-Code (`echo $?`) notieren.
3. In der Shell prüfen, ob der Wert in der Datenbank tatsächlich unverändert ist (`DocumentType.objects.get(facility_id=…, slug="kontakt").icon`).

**Erwartetes Ergebnis:**
- Die Ausgabe beginnt mit „Dry-Run — es wird nichts geschrieben." und listet je Abschnitt (Feldvorlagen, Dokumentationstypen, Feldgruppen, Feldzuordnungen, Bedingungen) `angelegt` / `aktualisiert` / `unverändert` mit Anzahl und Bezeichnern.
- `aktualisiert` nennt genau die beiden geänderten Objekte (`kontakt`, `dauer`); alles andere steht unter `unverändert`.
- Exit-Code `0`, Abschlusszeile „Dry-Run abgeschlossen."
- Schritt 3: der Datenbankwert ist **unverändert** — der Dry-Run hat nichts geschrieben.

**Status:** ☐ Offen

---

#### DEV-PKG-02 — Zweiter Lauf ist vollständig `unverändert` (Idempotenz)

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfigurationspakete | admin | — | — | `test_load_package_command.py`, `test_packages_loader.py`, `test_seed.py` |

**Voraussetzung:** DEV-PKG-01 durchgeführt (Arbeitskopie mit den beiden geänderten Werten liegt bereit).

**Code-Referenz:**
- `src/core/packages/loader.py` — Diff vor dem Schreiben, `created`/`updated`/`unchanged` je Objektart.
- `src/core/seed/doc_types.py` — derselbe Loader-Pfad wird vom Seed benutzt.

**Schritte:**
1. `python src/manage.py load_package --file /tmp/paket.yaml --facility <uuid>` (ohne `--dry-run`) ausführen.
2. **Denselben** Befehl unverändert ein zweites Mal ausführen.
3. `python src/manage.py seed` erneut laufen lassen (spielt `default.yaml` ein) und die Ausgabe auf Fehler prüfen.
4. Stichprobe in der UI: `/config/document-types/` zeigt den Typ `kontakt` mit dem in Schritt 1 gesetzten Icon.

**Erwartetes Ergebnis:**
- Schritt 1: `aktualisiert (2)`, Abschlusszeile „Import abgeschlossen.", Exit-Code `0`.
- Schritt 2: **jeder** Abschnitt meldet `angelegt (0)` und `aktualisiert (0)` — alles steht unter `unverändert`. Das ist der Kernbeweis der Idempotenz.
- Schritt 3: der Seed läuft fehlerfrei durch und setzt `icon`/`help_text` wieder auf die Katalogwerte zurück (Rücksync — dokumentiert in `CONTRIBUTING.md` und [`docs/packages.md`](../packages.md)).
- Keine Duplikate: die Anzahl der Dokumentationstypen und Feldvorlagen der Einrichtung ist über alle Läufe konstant (9 bzw. 25 im Standardkatalog).

**Status:** ☐ Offen

---

#### DEV-PKG-03 — Fehlerfall: Paket wird komplett abgewiesen, nichts wird übernommen

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfigurationspakete | admin | — | — | `test_load_package_command.py`, `test_packages_schema.py`, `test_packages_loader.py` |

**Voraussetzung:** DEV-PKG-02 durchgeführt; Anzahl der Dokumentationstypen und Feldvorlagen der Einrichtung vorher notiert.

**Code-Referenz:**
- `src/core/packages/schema.py` — Sammel-Validierung mit pfadgenauen Bezeichnern, Cross-Checks, Fach-Invarianten.
- `src/core/packages/loader.py` — Alles-oder-nichts (äußeres `atomic` + Savepoint je Objekt), Immutabilitäts-Prüfungen.

**Schritte:**
1. **Schema-Fehler:** in der Arbeitskopie einen Tippfehler einbauen (`sort_oder: 0` statt `sort_order: 0`), zusätzlich bei einem `field_ref` eine Feldvorlage referenzieren, die es nicht gibt, und bei einem `entity_type: client`-Typ ein `file`-Feld zuordnen. Befehl ohne `--dry-run` ausführen.
2. Fehler beheben, stattdessen einen **Immutabilitäts-Konflikt** einbauen: `field_type` der Vorlage `dauer` von `number` auf `text` ändern **und** in derselben Datei ein neues, gültiges Feld ergänzen. Befehl erneut ausführen.
3. Anzahl der Dokumentationstypen/Feldvorlagen erneut zählen; prüfen, ob das in Schritt 2 ergänzte neue Feld angelegt wurde.
4. `schema_version` auf `2` setzen und den Befehl erneut ausführen.

**Erwartetes Ergebnis:**
- Schritt 1: Abbruch mit „Paket ungültig:" und **allen** Verstößen in einem Lauf, jeder mit YAML-Pfad (z. B. `document_types[0].sort_oder: Unbekannter Schlüssel.`, `document_types[0].field_refs[3].template_slug: Unbekannte Feldvorlage …`, „Datei-Felder sind nur für Dokumentationstypen mit entity_type 'event' zulässig."). Exit-Code ≠ 0, es wird **nichts** geschrieben.
- Schritt 2: Abbruch mit „Import fehlgeschlagen — N Fehler, nichts wurde übernommen."; die Fehlerzeile nennt den Bezeichner `dauer` und „Feldtyp ist nach dem Anlegen unveränderlich (Bestand: 'number', Paket: 'text')". Der Report wird trotzdem ausgegeben.
- Schritt 3: die Zählungen sind **unverändert** — insbesondere wurde das neue, für sich gültige Feld **nicht** angelegt (Alles-oder-nichts, kein Teilstand).
- Schritt 4: Abbruch mit „Nicht unterstützte Schemaversion 2 — erwartet: 1."
- Gegenprobe: nach Zurücksetzen der Arbeitskopie läuft der Import wieder sauber durch und meldet alles als `unverändert`.

**DSGVO/Security-Note:**
- Ein Paket ist unvertrauenswürdiger Fremd-Input, der Feld-Sensibilität und Verschlüsselungs-Flags setzt. Die strikte Validierung (unbekannte Schlüssel = Fehler) und die Unveränderlichkeit von `field_type`/`is_encrypted`/`entity_type`/`system_type` verhindern, dass ein Import ein sensibles Feld still in ein unverschlüsseltes umdeutet.

**Status:** ☐ Offen

---

Übersicht: pro Bereich, in welchen Browsern und auf Mobile getestet werden muss. `✓` = Pflicht, `⚪` = Stichprobe, `—` = nicht relevant.

| Bereich | Chromium | Firefox | Safari/WebKit | Mobile (iPhone) |
|---------|----------|---------|---------------|------------------|
| AUTH | ✓ | ✓ | ✓ | ⚪ |
| MFA | ✓ | ✓ | ✓ | ⚪ |
| ACCT | ✓ | ⚪ | ⚪ | ⚪ |
| SUDO | ✓ | ⚪ | ⚪ | — |
| PWA | ✓ | ⚪ | ✓ | ✓ |
| CLIENT | ✓ | ⚪ | ⚪ | ✓ |
| CASE | ✓ | ⚪ | ⚪ | ⚪ |
| EPI | ✓ | ⚪ | ⚪ | ⚪ |
| GOAL | ✓ | ⚪ | ⚪ | ⚪ |
| EVT | ✓ | ✓ | ⚪ | ✓ |
| ATT | ✓ | ✓ | ⚪ | ✓ |
| WI | ✓ | ⚪ | ⚪ | ✓ |
| DEL | ✓ | ⚪ | ⚪ | — |
| RET | ✓ | ⚪ | ⚪ | ⚪ |
| SRCH | ✓ | ⚪ | ⚪ | ✓ |
| ZS | ✓ | ⚪ | ⚪ | ✓ |
| HOV | ✓ | ⚪ | ⚪ | ✓ |
| STAT | ✓ | ⚪ | ⚪ | ⚪ |
| AUDIT | ✓ | ⚪ | ⚪ | — |
| DSGVO | ✓ | ⚪ | ⚪ | — |
| OFFL | ✓ | ⚪ | ✓ | ✓ |
| SYS | ✓ | ⚪ | ⚪ | — |
| HTMX-Toasts (siehe Anhang B) | ✓ | ✓ | ⚪ | ⚪ |

**Konvention:** Jeder Bereich wird **mindestens** in Chromium komplett durchgespielt. Firefox/Safari/Mobile-Stichproben (`⚪`) bedeuten: einmal pro Release prüfen, nicht jeden Test-Lauf.
