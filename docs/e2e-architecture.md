# E2E-Test-Architektur

Technische Dokumentation der E2E-Testinfrastruktur mit Playwright und gunicorn.

Für Schritt-für-Schritt-Anleitungen, Troubleshooting und Code-Rezepte siehe [e2e-runbook.md](e2e-runbook.md).

---

## Server-Setup

E2E-Tests laufen gegen einen echten gunicorn-Server (nicht Django `runserver`).

**Konfiguration** (`src/tests/e2e/conftest.py`):

```
gunicorn anlaufstelle.wsgi:application
  --bind 127.0.0.1:8844
  --workers 2
  --threads 2
  --timeout 120
```

**Warum gunicorn statt runserver:**
- `runserver` ist single-threaded — bei Session-Saves, HTMX-Requests oder WeasyPrint-PDF-Export stauen sich Requests
- gunicorn mit 2 Workers + 2 Threads = 4 parallele Request-Slots
- WeasyPrint (CPU-intensiv) blockiert nur einen Worker, die anderen bleiben responsiv
- `collectstatic` wird automatisch vor Server-Start ausgeführt, WhiteNoise servt die Dateien
- stdout/stderr gehen nach `/dev/null` — bei `subprocess.PIPE` kann sich der nicht gelesene Pipe-Buffer füllen und gunicorn blockieren

**Port:** 8844 (nicht 8000, um Konflikte mit lokalem Dev-Server zu vermeiden)

---

## Datenbank-Isolation

Jede Test-Art nutzt eine eigene Datenbank — Dev-Daten bleiben immer unberührt:

| Test-Art | Datenbank | Verwaltet durch | Isolation |
|----------|-----------|-----------------|-----------|
| Entwicklung | `anlaufstelle` | Manuell (`make seed`) | — |
| Unit/Integration | `test_anlaufstelle` | pytest-django (automatisch) | Vollständig, transaktional |
| E2E | `anlaufstelle_e2e` | conftest.py (flush + seed) | Eigene DB, pro Session |

- `make test-e2e-locked` erstellt `anlaufstelle_e2e` automatisch, flusht sie und lädt Seed-Daten (Default `--scale=small`; `E2E_SEED_SCALE` kann die Skala ändern)
- `make ci` nutzt `test_anlaufstelle` (von pytest-django erstellt/gelöscht)
- Beide Test-Arten lassen die Dev-DB `anlaufstelle` unangetastet

---

## Wann E2E-Tests einsetzen?

E2E-Tests prüfen **Verhalten aus Nutzersicht** (Klick → Ergebnis), nicht Aussehen.

| Szenario | E2E? | Beispiel |
|----------|------|----------|
| Kritische Workflows (erstellen, löschen, filtern) | ✓ Ja | Event erstellen → Stern-42 zuweisen → speichern |
| Rollenbasierter Zugriff | ✓ Ja | Assistenz kann keine Statistiken sehen |
| HTMX-Interaktionen | ✓ Ja | "Annehmen"-Button → Status wechselt |
| 4-Augen-Prinzip | ✓ Ja | Löschantrag → anderer User genehmigt |
| Berechnungslogik, Model-Validierung | ✗ Unit-Test | Statistik-Aggregation, Constraints |
| Service-Funktionen | ✗ Unit-Test | encrypt, export |
| Pixel-genaues Layout, Farben | ✗ Visual Regression | CSS-Refactoring-Schutz |

**Faustregel:** Wenn der Test mit einem **Verb** beginnt (erstellen, löschen, filtern, einloggen), ist es ein E2E-Test. Wenn er mit "sieht aus wie" beginnt, nicht.

```python
# Gut: Prüft Verhalten
assert page.locator("button:has-text('Annehmen')").is_visible()

# Schlecht: Prüft Aussehen (fragil, bricht bei CSS-Refactoring)
assert page.locator("button").get_attribute("class") == "px-3 py-1..."
```

---

## Settings-Kette

```
base.py → dev.py → e2e.py
```

| Setting | base.py | dev.py | e2e.py | Grund |
|---------|---------|--------|--------|-------|
| `DATABASES.NAME` | `anlaufstelle` | geerbt | `anlaufstelle_e2e` | Dev-Daten schützen |
| `DEBUG` | — | `True` | geerbt | Dev-Verhalten |
| `PASSWORD_HASHERS` | PBKDF2 (Default) | geerbt | geerbt | **Nicht** MD5 wie in test.py |
| `RATELIMIT_ENABLE` | `True` | geerbt | `False` | E2E macht viele Logins |
| `SESSION_SAVE_EVERY_REQUEST` | `False` | geerbt | `False` (redundant) | Reduziert DB-Writes; Inaktivitäts-Fenster liefert die `SessionIdleRefreshMiddleware` (#1858) |

**Warum nicht `test.py`?**
`test.py` nutzt `MD5PasswordHasher` als primären Hasher fuer schnelle Unit-Tests. Django rehashed Passwoerter bei jedem Login automatisch auf den primären Hasher. Wenn der E2E-Server mit test-Settings läuft, werden alle Seed-Passwoerter auf MD5 umgeschrieben — danach funktioniert der Login im Dev-Server (PBKDF2) nicht mehr.

---

## Fixture-Architektur

### Session-scoped (einmal pro Test-Suite)

1. **`base_url`** — Startet gunicorn-Server:
   - `migrate --run-syncdb`
   - `seed` (Testdaten laden)
   - `collectstatic --noinput`
   - gunicorn starten + Health-Check (`GET /login/`)
   - Am Ende: `SIGTERM` + `wait(timeout=10)`

2. **Storage-States** — Login einmal pro Rolle, Session-Cookies cachen:
   - `_login_storage_state` → Admin (admin)
   - `_lead_storage_state` → Lead (emma)
   - `_staff_storage_state` → Staff (miriam)
   - `_assistant_storage_state` → Assistant (lena)
   - `_super_admin_storage_state` → Super-Admin (superadmin)
   - Passwort: `anlaufstelle2026` (Seed-Default)
   - Bauen sich ueber einen **kurzlebigen eigenen Browser** (`launch_browser`), nicht ueber die `browser`-Fixture — sonst ScopeMismatch (session haengt an module, siehe unten). Ein Storage-State ist reines Cookie-JSON und ueberlebt das Schliessen des Browsers; die Server-Session bleibt unabhaengig davon gueltig.

### Modul-scoped (einmal pro Testdatei)

- **`browser`** — ueberschreibt die session-scoped `browser`-Fixture von pytest-playwright: **ein Chromium-Prozess je Testdatei** (Refs #1787). Der Speicherverbrauch eines Browser-Prozesses waechst ueber die Session monoton (Renderer-Reste, Caches, SW-Registrierungen), auch wenn jeder Test seinen Context sauber schliesst — auf RAM-knappen Hosts kippt der Voll-Lauf sonst im letzten Datei-Cluster ins OOM. Am Dateiende stirbt der Prozess, die naechste Datei startet frisch.
- Fuer Tests ist der Wechsel transparent: Alle Consumer sind function-scoped (`authenticated_page` & Co. sowie die Tests, die `browser.new_context()` selbst rufen — Lockout, MFA, Offline-Contract). Neue **session- oder modul-scoped** Fixtures duerfen dagegen **nicht** von `browser` abhaengen; sie nutzen `launch_browser` wie die Storage-States.

### Function-scoped (pro Test)

- **`authenticated_page`** — Admin-Context mit gecachter Session
- **`lead_page`** / **`staff_page`** / **`assistant_page`** — analog
- Jeder Test bekommt einen frischen `browser.new_context()` mit Storage-State
- Timeouts: 30s (Default), 30s (Navigation)

### Helfer

- **`login_as(browser, base_url, username)`** — Manueller Login für Ad-hoc-User. Unter `anlaufstelle.settings.e2e` ist Rate-Limiting deaktiviert (`RATELIMIT_ENABLE = False`); der Login verbraucht dort kein Rate-Limit-Budget. Für wiederkehrende User die Session-Fixtures nutzen.

---

## Wait-Strategien

**Niemals `networkidle` verwenden.** Es wartet auf 500ms ohne Netzwerkaktivitaet — jeder Background-Request (Session-Save, HTMX) resettet den Timer und verursacht Timeouts.

### Nach `page.goto()` — `domcontentloaded`

```python
page.goto(f"{base_url}/clients/new/")
page.wait_for_load_state("domcontentloaded")
```

DOM ist geparsed und interaktionsfaehig. Reicht fuer die meisten Seiten.

### Nach Formular-Submit — `wait_for_url()`

```python
page.locator(SUBMIT).click()
page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"), timeout=30000)
```

Wartet auf die erwartete Ziel-URL. Robuster als Load-State, da es den tatsaechlichen Zustandswechsel prueft.

### Nach HTMX-Aktionen (kein URL-Wechsel) — Element-Wait

```python
page.locator("button:has-text('Annehmen')").click()
page.wait_for_load_state("domcontentloaded")
# oder spezifischer:
expect(page.locator("button:has-text('Erledigt')")).to_be_visible()
```

### Alpine.js Debounce — `wait_for_timeout()`

```python
page.fill("input[name='q']", "Blitz")
page.wait_for_timeout(500)  # Alpine.js Debounce 200ms + Fetch
```

Nur fuer clientseitige Debounce-Timer noetig, nicht fuer Server-Waits.

---

## Bekannte Einschraenkungen

1. **Serieller Default, optionale Parallelisierung:** `make test-e2e-locked` läuft seriell. `make test-e2e-parallel-locked` verwendet pytest-xdist; `src/tests/e2e/conftest.py`, `_get_worker_info`/`base_url`, vergibt pro Worker eine eigene Datenbank und einen eigenen gunicorn-Port. Mehrere Worker erhöhen den RAM-Bedarf; ihre Anzahl muss zur Host-Kapazität passen. Volle Läufe werden über die `-locked`-Targets serialisiert.

   **RAM-Decke auch seriell (Refs #1787):** Auch ein serieller Voll-Lauf kann auf einem RAM-knappen Host den Chromium per OOM-Killer verlieren. Zwei Stufen der Abhilfe, in dieser Reihenfolge:
   1. **Browser-Recycling** (Default, kein Aufruf noetig): modul-scoped `browser`-Fixture, ein Chromium je Testdatei — kappt das monotone Speicherwachstum ueber die Session.
   2. **`make test-e2e-halves-locked`** — kanonischer Fallback, wenn der Voll-Lauf **trotz** Recycling ins OOM laeuft: zwei serielle pytest-Sessions ueber je eine Haelfte der Testdateien. Jede Haelfte startet Server + `seed --flush` neu und hat ihr eigenes Speicher-Budget; das Rezept laeuft als eine Shell mit `set -e`, ein Fehlschlag in Haelfte 1 bricht ab. Preis: doppelte Server-/Seed-Zeit und keine dateiuebergreifende Zustandskette — Tests muessen ohnehin dateiweise unabhaengig sein. Welche Datei in welche Haelfte faellt, entscheidet derselbe Splitter (#1976) wie in der CI-Matrix (siehe [Scheiben-Schnitt](#scheiben-schnitt-ci-matrix-und-lokaler-fallback) unten) — die Liste bleibt dynamisch, kein Drift bei neuen Dateien.

2. **Shared Database je Worker:** Im seriellen Default teilen sich die Tests `anlaufstelle_e2e`; bei pytest-xdist verwendet jeder Worker eine eigene Datenbank (`anlaufstelle_e2e`, `anlaufstelle_e2e_1`, …). Innerhalb eines Workers besteht keine Isolation zwischen Tests — sie können Daten sehen, die vorherige Tests erstellt haben. Eindeutige Bezeichner (`uuid.uuid4().hex[:8]`) verwenden. Dev-Daten (`anlaufstelle`) sind nicht betroffen.

3. **Rate-Limiting deaktiviert:** `RATELIMIT_ENABLE = False` in `src/anlaufstelle/settings/e2e.py`. Tests, die echte Drosselung prüfen sollen, benötigen einen gesonderten Server mit eingeschaltetem Rate-Limiting (`src/tests/e2e/conftest.py`, `enforced_base_url`). Der Name einer Testklasse allein schaltet den Schutz nicht ein.

4. **WeasyPrint:** PDF-Export funktioniert nur mit gunicorn (Multi-Worker). Benoetigt System-Dependencies (Pango, Cairo, etc.) — im Dockerfile installiert, in CI ueber `apt-get`.

---

## Scheiben-Schnitt (CI-Matrix und lokaler Fallback)

Der Scheiben-Schnitt verteilt Testdateien auf mehrere getrennte Jobs, jeder für
sich seriell. Er ergänzt den seriellen Default und den optionalen parallelen
Lauf. `scripts/e2e_slice.py` ermittelt die Gewichte statisch aus Testfunktionen
und verteilt sie möglichst gleichmäßig; feste Testzahlen oder Laufzeiten sind
keine Zusage. Die aktuelle Verteilung liefert `--summary`. Die CI-Matrix in
`.github/workflows/e2e.yml` bestimmt die Scheibenanzahl.

**Wer schneidet:** [`scripts/e2e_slice.py`](../scripts/e2e_slice.py), stdlib-only.

```bash
python scripts/e2e_slice.py --slices 8 --summary        # Verteilung ansehen
python scripts/e2e_slice.py --slices 8 --index 3        # Dateiliste der Scheibe 3
make test-e2e-slice-locked SLICES=8 INDEX=3             # Scheibe 3 lokal nachfahren
```

**Aufteilungsschluessel: Testzahl je Datei.** Der naheliegende Schnitt „erste
Hälfte der sortierten Dateiliste / zweite Hälfte“ ist deterministisch, aber
kann die Testlast ungleich verteilen. Der Splitter zählt stattdessen per `ast`
die Testfunktionen je Datei
(kein Import, keine Datenbank, kein Playwright noetig) und verteilt die
schwerste Datei zuerst in die jeweils leichteste Scheibe. Die tatsächliche Verteilung zeigt `--summary`. Die Testzahl ist ein **Proxy**
für Laufzeit; sie garantiert keine Timeout-Vermeidung. Im Gegensatz zu
gemessenen Laufzeiten ist sie unabhängig vom Runner und reproduzierbar.

**Warum dateiweise und nicht `pytest --splits`:** Der Chromium wird pro
Testdatei recycelt (modul-scoped `browser`-Fixture, #1787);
ein Schnitt mitten durch eine Datei wuerde denselben Browser in zwei Sessions
aufbauen. Ausserdem waere `--splits`/`--group` eine zusaetzliche Abhaengigkeit
(`pytest-split`), die das Projekt nicht mitbringt.

**Die Zusicherung, die zaehlt:** Die Vereinigung aller Scheiben ist exakt die
Menge, die `pytest -m e2e` einsammelt — keine Datei doppelt, keine fehlend.
Ohne diese Partition liefe die CI gruen, waehrend Tests unbemerkt nie laufen.
Abgesichert in [`src/tests/test_e2e_slice.py`](../src/tests/test_e2e_slice.py),
inklusive Abgleich gegen eine echte `--collect-only`-Sammlung. Aus demselben
Grund gibt der Splitter bei jedem Fehler **nichts** auf stdout aus und endet
mit Exit-Code != 0: `pytest` ohne Dateiargumente wuerde sonst die gesamte Suite
fahren, je Scheibe einmal.

**Und keine Scheibe bleibt leer** — auch das ist ein Voll-Lauf, nur ohne
Fehlermeldung. Der Fall entsteht nicht durch zu viele Scheiben (das faengt eine
Bereichspruefung ab), sondern durch Dateien mit **Gewicht 0**: eine
`test_*.py`, die nur Fixtures enthaelt, wiegt nichts und laege ohne Gegenmassnahme
mit allen anderen Nullern in derselben Scheibe. Die Zuteilung zieht bei
gleicher Last deshalb die noch kleiner besetzte Scheibe vor; da mindestens so
viele Dateien wie Scheiben verlangt sind, bekommt jede Scheibe eine Datei.

**Scheibenzahl aendern:** die `slice`-Liste in `.github/workflows/e2e.yml`
**und** `E2E_SLICES` im selben Job anpassen; ein Guard-Step vergleicht beide
gegen `strategy.job-total` und bricht bei Abweichung ab, bevor Tests laufen.

---

## AuthZ-Live-Audit-Runner

Zusätzlich zur regulären E2E-Suite gibt es einen **opt-in Live-Audit der Autorisierungs-Matrix** gegen den laufenden gunicorn-Server: `src/tests/e2e/test_authz_audit.py` (Helfer: `_authz_audit_helpers.py`; Refs #1055, #1042/A1). Er prüft pro Route aus `EXPECTATIONS` × HTTP-Methode × jede Rolle (`ROLES` + `anonymous`) drei Dimensionen ab:

1. **Vertikale Allow/Deny-Matrix** — darf Rolle X Route Y? (`_judge`)
2. **IDOR / Cross-Facility** — Zugriff auf Objekt-IDs aus Facility 2 (Fixture `medium_seed`, `--scale medium`, zwei Facilities; `facility_admin`/`lead` werden für den Objekt-Lookup per Sudo elevatet, sonst greift `RequireSudoModeMixin` schon davor)
3. **Cookie-Flags + Security-Header** — HttpOnly, SameSite, X-Content-Type-Options, Referrer-Policy, CSP

**Ausführung (manuell, nicht Teil von `make test-e2e`):**

```bash
AUTHZ_AUDIT=1 .venv/bin/python -m pytest \
    src/tests/e2e/test_authz_audit.py -m authz_audit -v
```

Gated über die Env-Var `AUTHZ_AUDIT=1` (`skipif`) + Marker `authz_audit`. Seriell laufen lassen (Assert gegen `pytest-xdist`); ein eigenes `make`-Target gibt es bewusst nicht.

**Solo-Invocation-Pflicht** (Refs #1788): `medium_seed` prüft zusätzlich zum `pytest-xdist`-Guard über `request.session.items`, dass in derselben pytest-Session keine weiteren `tests/e2e`-Dateien gesammelt wurden — `seed --flush --scale medium` reißt sonst die Seed-Basis jeder anderen Datei ab, die vor oder nach diesem Fixture in derselben Session läuft (dieselbe Klasse Fallout, die #1787 für geteilte Bestandsobjekte im vollen seriellen Lauf dokumentiert). Ein Verstoß failt hart mit erklärender Meldung statt eines verwirrenden Folgefehlers irgendwo in der fremden Datei. Aus demselben Grund mutiert `prepare_audit_objects` Bestandszeilen der Seed-DB direkt, ohne Snapshot/Restore — nur zulässig, weil die Suite exklusiv auf dieser frisch geflushten DB läuft.

**Fail-open mit Report:** Der Lauf **failt nicht** bei AuthZ-Abweichungen — diese landen als „ABWEICHUNG" im generierten Markdown-Report. Der Report wird datiert unter `docs/archive/audits/<Datum>-live-authz-matrix.md` abgelegt und beim Schreiben exklusiv angelegt, überschreibt also nie eine vorhandene Datei: Ist der Standardname vergeben (auch erst zwischen Auswahl und Schreiben), nimmt der Lauf den nächsten Zähler-Suffix (`-2`, `-3`, …). `AUTHZ_AUDIT_REPORT_PATH=<datei.md>` legt das Ziel ausdrücklich fest; existiert diese Datei bereits, bricht der Lauf erst beim Schreiben am Ende mit `FileExistsError` ab und lässt die Datei unverändert — ein bereits belegtes Ziel gehört daher vor dem Start entfernt oder geändert. Der eigentliche Regressionsschutz ist die schnelle Unit-Matrix (`test_authz_matrix.py`); der Live-Audit ist das periodische Vollbild. Er failt nur bei **strukturellem** Versagen (Schwellen: > 850 geprüfte Zeilen, < 10 % Setup-/Request-Fehler). Destruktive POSTs werden über `DESTRUCTIVE_RESET`-Hooks zurückgesetzt.

**Keine erlaubten Blindstellen mehr** (#1783): Fixture-Namen ohne `HARVEST_SQL`-Query bzw. `FOREIGN_ALIASES`-Eintrag liefen früher still als SETUP-ERROR durch — unter der 10-%-Schwelle also *ungeprüft*, ohne dass etwas rot wurde. Die letzte solche Bestandslücke (die ~20 Konfigurations-Endpoints) ist geschlossen, die benannte Allowlist `LIVE_AUDIT_UNHARVESTED` ersatzlos entfallen. Zwei Guards halten das: `test_every_fixture_reference_is_live_harvestable` (jeder Name der Erwartungs-Tabelle ist erntbar, **ohne Ausnahme**) und `test_no_stale_harvest_entries` (keine Query ohne Abnehmer). Ein dritter, `TestLiveAuditHarvestQueriesRunAgainstTheSchema`, führt jede Query im normalen Unit-Lauf gegen das echte Schema aus — sonst fiele ein Tippfehler erst beim nächsten manuellen Audit auf, und dann als stille SETUP-ERROR-Zelle.

Nicht alle Matrix-Objekte liefert der Seed: `prepare_audit_objects` bessert je Facility idempotent nach (Papierkorb-Person, vertragskonformes Ereignis samt Anhang, Owner-Zuordnung der personen-/falleigenen Anhänge — eine `FieldCondition` am Konfigurations-Anker (#1783), weil `core/packages/default.yaml` keine `conditions` deklariert, eine `FieldGroup` an diesem Anker, wo das Paket keine trägt, ein offener Importlauf für `core:import_*` und das Ereignis am geernteten Fall für `case_remove_event`). Die Dateianhänge der zweiten Einrichtung legt `_authz_audit_owner_files.py` im selben Fixture über den Vault an, weil deren Paket die Vorlage `anhang` nicht mitbringt. Destruktive POSTs setzen die `DESTRUCTIVE_RESET`-Hooks zurück — auch `client_restore`, `client_anonymize` und `case_remove_event`; ohne sie fände die nachfolgende erlaubte Zelle ein bereits verbrauchtes Objekt (404) und der Report zeigte eine Abweichung, die nur ein Messartefakt ist.

Schritt-für-Schritt-Bedienung + Debugging: siehe [e2e-runbook.md](e2e-runbook.md).

---

## Troubleshooting

### Timeout bei einzelnen Tests

- **Ursache:** Meist `networkidle`-Waits (falls noch vorhanden) oder Server unter Last
- **Fix:** `rg -n "networkidle" src/tests/e2e/` zeigt auch Kommentare. Prüfen, ob ein Treffer einen ausführbaren Wait betrifft; solche Waits durch `domcontentloaded`, konkrete Locator-Erwartungen oder `expect_response` ersetzen. Kommentartreffer sind kein Verstoß.

### Server startet nicht (RuntimeError)

- **Ursache:** Port 8844 belegt oder gunicorn-Fehler
- **Fix:** `lsof -i :8844` pruefen, ggf. alten Prozess beenden. gunicorn-Logs in `stderr` des Subprozesses pruefen.

### Login schlaegt fehl nach test-Settings

- **Ursache:** Unit-Tests mit `settings.test` (MD5-Hasher) haben Passwoerter rehashed
- **Fix:** `python src/manage.py seed` mit e2e-Settings ausfuehren (passiert automatisch in conftest)

### Static Files fehlen (404 auf CSS/JS)

- **Ursache:** `collectstatic` nicht gelaufen
- **Fix:** Wird automatisch in conftest vor gunicorn-Start ausgefuehrt. Bei manuellen Tests: `python src/manage.py collectstatic --noinput`

### WeasyPrint-Fehler in CI

- **Ursache:** Fehlende System-Dependencies (Pango, Cairo, GDK-Pixbuf; seit WeasyPrint 70 auch HarfBuzz-Subset — ohne `libharfbuzz-subset0` warnt der Import, und unter `filterwarnings = error` stirbt damit `django.setup()` beim pytest-Start)
- **Fix:** In CI-Pipeline installieren: `apt-get install -y libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 libharfbuzz-subset0` (test.yml und e2e.yml tun das; das Dockerfile installiert das Paket ebenfalls)
