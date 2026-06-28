# Manuelle Test-Matrix — Sektion D

> Teil der [Manual-Test-Matrix](manual-test-matrix.md) (Refs #1071 Block B). Setup-Block, Status-Legende, TC-ID-Schema, Browser-/Mobile-Konventionen und die Anhänge stehen im [Hub](manual-test-matrix.md); die Gesamtübersicht in [`test-matrix-index.md`](test-matrix-index.md).

---

## SEKTION D — Entwickler-Probes (LOKAL/SSH)

> **Zielgruppe:** Tobias / Server-Admin. Diese Tests erfordern direkten Zugriff auf den Server (`docker compose exec web python manage.py …`, `psql`, lokale `manage.py`-Befehle). Auf [`dev.anlaufstelle.app`](https://dev.anlaufstelle.app) nur durch Tobias oder per SSH durchführbar. Sie verifizieren Schema-Constraints (`on_delete`-Verhalten, RLS-Force, Encryption-at-Rest, Hash-Ketten) und Betriebsfähigkeit (DB-Rollen, Backup/Restore, Retention-Cron), die in Anwender-Tests nicht prüfbar sind. Aktuelle Fallzahl: siehe [`test-matrix-index.md`](test-matrix-index.md).

### D.CLIENT

#### DEV-CLIENT-15 — PROTECT: Klient mit aktivem Fall lässt sich nicht direkt löschen

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen / Kaskade | admin ||| `test_clients_protect.py` |

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
| Fälle / Kaskade | admin ||| `test_cases_cascade.py` |

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
| Fälle / Kaskade | admin ||| `test_cases_cascade.py` |

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
| DeletionRequests | leitung + fachkraft ||| `test_deletion_approval_audit.py` |

**Voraussetzung:** DEL-01 + DEL-03 + DEL-04 durchgeführt.

**Schritte:**
1. `/audit/?action=delete` aufrufen (AuditLogListView).
2. Suchen nach den Lifecycle-Events:
 - DEL-01 (Antrag): Aktuell schreibt `request_deletion` KEINEN dedizierten `deletion_requested`-AuditLog — der Code legt nur einen `DeletionRequest`-DB-Record an. Audit-Trail entsteht erst bei Approve/Reject.
 - DEL-03 (Approve → Soft-Delete): AuditLog `delete` mit `target_type=Event`, `detail={document_type, client_pseudonym, occurred_at}` (via `soft_delete_event`).
 - DEL-04 (Reject): KEIN AuditLog im Reject-Pfad — der Status-Change am DeletionRequest ist selbst der Audit-Trail (DeletionRequest-Tabelle ist append-only-artig).

**Erwartetes Ergebnis:**
- AuditLog für Approve = action `delete` (von `soft_delete_event`).
- AuditLog für Reject = nicht in `AuditLog`-Tabelle, sondern im `DeletionRequest.status`/`reviewed_*`-Feld.
- Die im Plan-Beschreib genannten Action-Namen `DELETION_REQUESTED`/`DELETION_APPROVED`/`DELETION_REJECTED` existieren NICHT im aktuellen `AuditLog.Action`-Enum. Realer Stand: Lifecycle wird über `DeletionRequest`-Records + `delete`-AuditLog rekonstruiert.
- **Lücke (zu klären):** Falls explizite Audit-Einträge gewünscht sind, ist eine Code-Erweiterung in `request_deletion` / `approve_deletion` / `reject_deletion` nötig.

**DSGVO/Security-Note:**
- Aktueller Audit-Trail genügt Art. 5 (Rechenschaftspflicht), weil `DeletionRequest`-Tabelle die Antragshistorie abbildet (Antragsteller, Reviewer, Zeitpunkte, Reason).

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
| Aufbewahrung | admin (CLI) || ⚪ | `test_retention_dashboard.py` |

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
| Aufbewahrung | admin | C || `test_retention_sensitivity.py` |

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

### D.STAT

#### DEV-STAT-03 — Snapshot via `python manage.py create_statistics_snapshots`

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Statistik | admin (CLI) || ⚪ | `test_statistics_snapshot.py` |

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
| Audit | admin (Shell) || ⚪ | `test_audit_append_only_e2e.py` |

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
- Schritt 4: ValueError mit Message wie „AuditLog ist append-only, save nicht erlaubt".
- Schritt 5: ValueError beim delete.
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
| Compliance | admin | C/F/S || `src/tests/test_k_anonymization.py`, `src/tests/test_anonymize_residue.py` |

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
| Compliance | leitung | C/F/S || `test_retention_dashboard.py` |

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
| Compliance | admin ||| `src/tests/test_rls.py` |

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
| Compliance | admin | C/F/S || `test_dsgvo_package.py` |

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
| Compliance |||| `src/tests/test_encryption.py` |

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
| Compliance || C/F/S || `src/tests/test_auth.py` |

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
| Compliance | admin ||| `src/tests/test_breach_detection.py`, `src/tests/test_breach_webhook_ssrf.py` |

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
| Security |||| `src/tests/test_rls.py` |

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
| Security | leitung (F1) + leitung_2 (F2) | C || `test_rls.py` |

**Voraussetzung:** zwei Facilities mit jeweils mindestens einem offenen `DeletionRequest`.

**Code-Referenz:**
- `src/core/views/deletion.py` — Listen/Genehmigen-Views.
- `src/core/services/deletion.py` — `DeletionRequest.objects.for_facility(...)`.
- `src/core/middleware/facility_scope.py` — `request.current_facility`.

**Schritte:**
1. F1: als `leitung` einloggen, in F1 ein neues `DeletionRequest` für einen F1-Klienten anlegen (`/clients/<uuid>/request-deletion/`).
2. F2: als `leitung_2` einloggen.
3. `/deletion-requests/` aufrufen → Liste prüfen.
4. Direkt-URL des F1-Antrags aufrufen (`/deletion-requests/<F1-uuid>/`) — versuchen zu genehmigen.
5. Optional in PostgreSQL: ohne `app.current_facility_id` `SELECT count(*) FROM core_deletionrequest;` → 0.

**Erwartetes Ergebnis:**
- Liste zeigt nur F2-Anträge, kein F1-Eintrag.
- Direkt-URL liefert 404 (oder leere QuerySet-Antwort), niemals 200 mit fremden Daten.
- AuditLog protokolliert keinen Zugriff auf F1-Datensatz durch F2-User.

**DSGVO/Security-Note:**
- Art. 5 Abs. 1 lit. f (Integrität & Vertraulichkeit) + Art. 32 (TOM). RLS-Layer auf `core_deletionrequest` muss FORCE-aktiv sein, App-Filter zusätzlich.

**Status:** ☐ Offen

---

#### DEV-SEC-MFA-03 — Recovery-Flow

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | fachkraft + admin | C/F/S || `test_mfa_scoping.py` |

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
| Security | admin ||| `src/tests/test_audit_signals.py`, `src/tests/test_audit_trigger.py` |

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
| Security | admin ||| `src/tests/test_audit_service.py` |

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
| Security | admin ||| `src/tests/test_encryption.py` |

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
| Security |||| `src/tests/test_security_hardening.py` |

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

> Refs #903 ( + §4.6): Ops-/Self-Hosting-Härtung. DSGVO-relevant über Art. 5 (Speicherbegrenzung) und Art. 32 (TOM) — werden aus Sektion C verlinkt, leben aber als LOKAL/SSH-Probes hier.

#### DEV-OPS-01 — DB-Rollen-Check (App NOSUPERUSER, kein BYPASSRLS; Admin BYPASSRLS)

> 🔧 **LOKAL/SSH erforderlich.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Ops | admin ||||

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
| Ops | admin ||||

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
| Ops | admin ||||

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
| Ops | admin ||||

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
| Ops | admin ||||

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
| Ops | admin ||| `src/tests/test_migrations.py` (falls vorhanden) |

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
| Ops | admin ||| `src/tests/test_retention_*.py` |

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
| Ops |||| `src/tests/test_health_endpoint.py` (falls vorhanden) |

**Code-Referenz:** Health-View (siehe `ENT-SYS-01`, `ENT-SYS-02`).

**Schritte:**
1. `curl -s https://dev.anlaufstelle.app/health/` → JSON mit Status `ok`.
2. ClamAV-Container stoppen: `docker compose stop clamav`.
3. Health-Endpoint erneut → erwartet `degraded` (App läuft, aber ClamAV down).
4. DB-Container stoppen: `docker compose stop db`.
5. Health-Endpoint erneut → erwartet `critical` (App nicht funktionsfähig).
6. Container wieder hochfahren, Status zurück auf `ok`.

**Erwartetes Ergebnis:**
- Drei Stufen sind unterscheidbar.
- Externes Monitoring (UptimeRobot o.ä.) kann auf `degraded` warnen statt erst auf hartem 5xx.

**Status:** ☐ Offen

---

Übersicht: pro Bereich, in welchen Browsern und auf Mobile getestet werden muss. `✓` = Pflicht, `⚪` = Stichprobe, `—` = nicht relevant.

| Bereich | Chromium | Firefox | Safari/WebKit | Mobile (iPhone) |
|---------|----------|---------|---------------|------------------|
| AUTH | ✓ | ✓ | ✓ | ⚪ |
| MFA | ✓ | ✓ | ✓ | ⚪ |
| ACCT | ✓ | ⚪ | ⚪ | ⚪ |
| SUDO | ✓ | ⚪ | ⚪ ||
| PWA | ✓ | ⚪ | ✓ | ✓ |
| CLIENT | ✓ | ⚪ | ⚪ | ✓ |
| CASE | ✓ | ⚪ | ⚪ | ⚪ |
| EPI | ✓ | ⚪ | ⚪ | ⚪ |
| GOAL | ✓ | ⚪ | ⚪ | ⚪ |
| EVT | ✓ | ✓ | ⚪ | ✓ |
| ATT | ✓ | ✓ | ⚪ | ✓ |
| WI | ✓ | ⚪ | ⚪ | ✓ |
| DEL | ✓ | ⚪ | ⚪ ||
| RET | ✓ | ⚪ | ⚪ | ⚪ |
| SRCH | ✓ | ⚪ | ⚪ | ✓ |
| ZS | ✓ | ⚪ | ⚪ | ✓ |
| HOV | ✓ | ⚪ | ⚪ | ✓ |
| STAT | ✓ | ⚪ | ⚪ | ⚪ |
| AUDIT | ✓ | ⚪ | ⚪ ||
| DSGVO | ✓ | ⚪ | ⚪ ||
| OFFL | ✓ | ⚪ | ✓ | ✓ |
| SYS | ✓ | ⚪ | ⚪ ||
| HTMX-Toasts (siehe Anhang B) | ✓ | ✓ | ⚪ | ⚪ |

**Konvention:** Jeder Bereich wird **mindestens** in Chromium komplett durchgespielt. Firefox/Safari/Mobile-Stichproben (`⚪`) bedeuten: einmal pro Release prüfen, nicht jeden Test-Lauf.
