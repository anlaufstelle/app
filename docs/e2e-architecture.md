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
- stdout/stderr gehen nach `/dev/null` — bei `subprocess.PIPE` füllt sich der Pipe-Buffer nach ~25 Requests und gunicorn blockiert

**Port:** 8844 (nicht 8000, um Konflikte mit lokalem Dev-Server zu vermeiden)

---

## Datenbank-Isolation

Jede Test-Art nutzt eine eigene Datenbank — Dev-Daten bleiben immer unberührt:

| Test-Art | Datenbank | Verwaltet durch | Isolation |
|----------|-----------|-----------------|-----------|
| Entwicklung | `anlaufstelle` | Manuell (`make seed`) ||
| Unit/Integration | `test_anlaufstelle` | pytest-django (automatisch) | Vollständig, transaktional |
| E2E | `anlaufstelle_e2e` | conftest.py (flush + seed) | Eigene DB, pro Session |

- `make test-e2e` erstellt `anlaufstelle_e2e` automatisch, flusht sie und lädt Seed-Daten (`--scale=small`, 7 Clients)
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
| `DEBUG` || `True` | geerbt | Dev-Verhalten |
| `PASSWORD_HASHERS` | PBKDF2 (Default) | geerbt | geerbt | **Nicht** MD5 wie in test.py |
| `RATELIMIT_ENABLE` | `True` | geerbt | `False` | E2E macht viele Logins |
| `SESSION_SAVE_EVERY_REQUEST` | `True` | geerbt | `False` | Reduziert DB-Writes |

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
 - Passwort: `anlaufstelle2026` (Seed-Default)

### Function-scoped (pro Test)

- **`authenticated_page`** — Admin-Context mit gecachter Session
- **`lead_page`** / **`staff_page`** / **`assistant_page`** — analog
- Jeder Test bekommt einen frischen `browser.new_context()` mit Storage-State
- Timeouts: 30s (Default), 30s (Navigation)

### Helfer

- **`login_as(browser, base_url, username)`** — Manueller Login fuer Ad-hoc-User. Verbraucht Rate-Limit-Versuch (5/min). Fuer wiederkehrende User die Session-Fixtures nutzen.

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

1. **Serielle Ausfuehrung:** E2E-Tests laufen seriell (kein pytest-xdist). Playwright/Chromium braucht ~200-400 MB RAM pro Instanz. Auf CI-Runnern mit begrenztem RAM wuerde Parallelisierung zu OOM fuehren.

2. **Shared Database:** Alle E2E-Tests teilen sich `anlaufstelle_e2e` mit Seed-Daten. Keine Test-Isolation untereinander — Tests koennen Daten sehen, die andere Tests erstellt haben. Eindeutige Bezeichner (`uuid.uuid4().hex[:8]`) verwenden. Dev-Daten (`anlaufstelle`) sind nicht betroffen.

3. **Rate-Limiting deaktiviert:** `RATELIMIT_ENABLE = False` in e2e.py. Ausnahme: `TestZZRateLimiting` in `test_auth_roles.py` testet explizit das Rate-Limiting (muss als letzter Test laufen, ZZ-Prefix).

4. **WeasyPrint:** PDF-Export funktioniert nur mit gunicorn (Multi-Worker). Benoetigt System-Dependencies (Pango, Cairo, etc.) — im Dockerfile installiert, in CI ueber `apt-get`.

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

**Fail-open mit Report:** Der Lauf **failt nicht** bei AuthZ-Abweichungen — diese landen als „ABWEICHUNG" im generierten Markdown-Report (`docs/archive/audits/2026-06-12-a1-laufzeit-authz-matrix.md`). Der eigentliche Regressionsschutz ist die schnelle Unit-Matrix (`test_authz_matrix.py`); der Live-Audit ist das periodische Vollbild. Er failt nur bei **strukturellem** Versagen (Schwellen: > 850 geprüfte Zeilen, < 10 % Setup-/Request-Fehler). Destruktive POSTs werden über `DESTRUCTIVE_RESET`-Hooks zurückgesetzt.

Schritt-für-Schritt-Bedienung + Debugging: siehe [e2e-runbook.md](e2e-runbook.md).

---

## Troubleshooting

### Timeout bei einzelnen Tests

- **Ursache:** Meist `networkidle`-Waits (falls noch vorhanden) oder Server unter Last
- **Fix:** `grep -r "networkidle" src/tests/e2e/` — muss 0 Treffer liefern. Falls nicht, durch `domcontentloaded` ersetzen.

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

- **Ursache:** Fehlende System-Dependencies (Pango, Cairo, GDK-Pixbuf)
- **Fix:** In CI-Pipeline installieren: `apt-get install -y libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0`
