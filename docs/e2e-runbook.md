# E2E-Runbook

Praktisches Nachschlagewerk für E2E-Tests — Schritt-für-Schritt-Anleitungen, Troubleshooting und Code-Rezepte.

Für Architektur, Settings-Kette und Fixture-Design siehe [e2e-architecture.md](e2e-architecture.md).

---

## 1. Checkliste: E2E von Null zum grünen Lauf

Die `conftest.py` erledigt DB-Erstellung, Migration, Seed und `collectstatic` automatisch. Diese Checkliste sichert nur die **Voraussetzungen**.

| # | Schritt | Befehl | Prüfung |
|---|---------|--------|---------|
| 1 | PostgreSQL läuft | `sudo docker compose up -d` | `pg_isready -h localhost` → Exit 0 |
| 2 | Port 8844 frei | `lsof -i :8844` | Keine Ausgabe |
| 3 | Alte Prozesse beenden | `pkill -f 'gunicorn.*8844'` | `lsof -i :8844` danach leer |
| 4 | Venv aktiv | `source .venv/bin/activate` | `which python` zeigt `.venv/` |
| 5 | Playwright installiert | `python -m playwright install chromium` | Kein „browser not found" |
| 6 | Tailwind gebaut | `make tailwind-build` | `src/static/css/styles.css` existiert |
| 7 | Tests starten | `make test-e2e` | Grüne Ausgabe |

> **Hinweis:** Docker benötigt auf manchen Systemen `sudo`. Der Dev-Server (Port 8000/8443) muss **nicht** laufen — E2E nutzt Port 8844.

---

## 2. Troubleshooting: Häufige Fehler

### Fehlertabelle

| Symptom | Ursache | Fix |
|---------|---------|-----|
| `RuntimeError: gunicorn-Server konnte nicht gestartet werden` | Port 8844 belegt | `lsof -i :8844` → `kill <PID>` oder `pkill -f 'gunicorn.*8844'` |
| `RuntimeError: gunicorn...` + DB-Connection-Error | PostgreSQL nicht gestartet | `sudo docker compose up -d` → `pg_isready -h localhost` |
| `psycopg.OperationalError: database "anlaufstelle_e2e" does not exist` | conftest-Erstellung fehlgeschlagen | `PGPASSWORD=anlaufstelle psql -h localhost -U anlaufstelle -c "CREATE DATABASE anlaufstelle_e2e;"` |
| Login-Redirect zurück zu `/login/` | Passwörter durch MD5 korrumpiert | `DJANGO_SETTINGS_MODULE=anlaufstelle.settings.e2e .venv/bin/python src/manage.py seed --flush` |
| Login-Redirect + Rate-Limit-Meldung | Falsches Settings-Modul (Rate-Limit aktiv) | `DJANGO_SETTINGS_MODULE` muss `anlaufstelle.settings.e2e` sein |
| `TimeoutError` bei `page.goto()` | `networkidle` als Wait-Strategie | `grep -r "networkidle" src/tests/e2e/` → ersetzen durch `domcontentloaded` |
| `TimeoutError` nach Form-Submit | Kein `wait_for_url()` nach `.click()` | `page.wait_for_url(re.compile(r"/expected/path/"))` ergänzen |
| `strict mode violation` | Locator matcht mehrere Elemente | Container scopen (`#main-content`) oder `.first` nutzen |
| 404 auf CSS/JS (Seite ohne Styling) | `collectstatic` nicht gelaufen | Manuell: `DJANGO_SETTINGS_MODULE=anlaufstelle.settings.e2e .venv/bin/python src/manage.py collectstatic --noinput` |
| Seed-Client nicht gefunden (Stern-42 etc.) | Alte Daten nicht geflusht, Pagination | conftest nutzt `--flush`. Manuell: `seed --flush`. In Tests: per Filter suchen statt `.is_visible()` |
| `button[type="submit"]` matcht mehrere | Selektor greift Navigation-Buttons mit | `#main-content button[type='submit']` verwenden |
| HTMX-Swap nicht sichtbar nach Klick | Kein Wait nach Response | `page.wait_for_load_state("domcontentloaded")` oder `page.wait_for_timeout(500)` |

### Passwort-Hasher-Kette (Kernproblem)

```
e2e.py → erbt von dev.py → PBKDF2 (Django-Default) ✓ Passwörter bleiben intakt
test.py → MD5 als primärer Hasher → Django rehashed bei Login → Dev-Login kaputt ✗
```

**Regel:** E2E-Server **immer** mit `anlaufstelle.settings.e2e`, **nie** mit `test`.

---

## 3. Rezepte: Neue E2E-Tests schreiben

### 3.1 Grundgerüst

```python
"""E2E-Tests für <Feature>."""

import re
import uuid

import pytest

pytestmark = pytest.mark.e2e

SUBMIT = "#main-content button[type='submit']"


class TestFeatureName:
    """Beschreibung der Test-Klasse."""

    def test_something(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/path/")
        page.wait_for_load_state("domcontentloaded")
        # ...
```

### 3.2 Formular absenden + Redirect prüfen

```python
def test_create_and_redirect(self, authenticated_page, base_url):
    page = authenticated_page
    unique = f"E2E-{uuid.uuid4().hex[:6]}"

    page.goto(f"{base_url}/things/new/")
    page.wait_for_load_state("domcontentloaded")

    page.fill('input[name="title"]', unique)
    page.locator(SUBMIT).click()
    page.wait_for_url(re.compile(r"/things/[0-9a-f-]+/$"))

    assert page.locator("h1").inner_text() == unique
```

### 3.3 HTMX-Interaktion (kein URL-Wechsel)

```python
def test_htmx_filter(self, authenticated_page, base_url):
    page = authenticated_page
    page.goto(f"{base_url}/items/")
    page.wait_for_load_state("domcontentloaded")

    # HTMX-Trigger (z.B. Select-Änderung)
    page.select_option("select[name='status']", value="closed")
    page.wait_for_timeout(500)  # HTMX-Swap abwarten
    page.wait_for_load_state("domcontentloaded")

    # Element im aktualisierten Container prüfen
    assert page.locator("#result-container").locator("text=Geschlossen").count() > 0
```

### 3.4 Alpine.js Debounce (Autocomplete/Suche)

```python
from playwright.sync_api import expect

def test_autocomplete(self, authenticated_page, base_url):
    page = authenticated_page
    page.goto(f"{base_url}/events/new/")
    page.wait_for_load_state("domcontentloaded")

    page.fill("input[name='q']", "Stern")
    page.wait_for_timeout(500)  # Alpine.js Debounce 200ms + Fetch
    expect(page.locator("[data-testid='suggestions']")).to_be_visible()
```

### 3.5 Rollenbasierter Test

```python
class TestRollenZugriff:
    """Rollenbasierte Sichtbarkeit prüfen."""

    def test_assistant_sieht_keine_statistik(self, assistant_page):
        page = assistant_page
        nav = page.locator("nav[aria-label='Hauptnavigation']")
        assert nav.locator("a:has-text('Statistik')").count() == 0

    def test_admin_sieht_statistik(self, authenticated_page):
        page = authenticated_page
        nav = page.locator("nav[aria-label='Hauptnavigation']")
        assert nav.locator("a:has-text('Statistik')").is_visible()
```

### 3.6 Mobile-Viewport

```python
@pytest.fixture
def mobile_page(browser, base_url, _login_storage_state):
    """Mobile-Viewport für responsive Tests."""
    context = browser.new_context(
        storage_state=_login_storage_state,
        viewport={"width": 375, "height": 812},
        device_scale_factor=2,
        locale="de-DE",
    )
    page = context.new_page()
    page.set_default_timeout(30000)
    page.set_default_navigation_timeout(30000)
    yield page
    context.close()
```

### 3.7 Eigene Test-Daten (keine Seed-Mutation)

```python
def _create_own_client(self, page, base_url):
    """Eigenen Client erstellen statt Seed-Daten zu mutieren."""
    pseudonym = f"E2E-{uuid.uuid4().hex[:6]}"
    page.goto(f"{base_url}/clients/new/")
    page.wait_for_load_state("domcontentloaded")
    page.fill('input[name="pseudonym"]', pseudonym)
    page.locator(SUBMIT).click()
    page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))
    return pseudonym
```

---

## 4. Verbotene Muster (Anti-Patterns)

| Verboten | Warum | Stattdessen |
|----------|-------|-------------|
| `wait_until="networkidle"` | Session-Saves und HTMX resetten den Timer endlos | `wait_until="domcontentloaded"` |
| `page.wait_for_load_state("networkidle")` | Gleicher Grund | `page.wait_for_load_state("domcontentloaded")` |
| `button[type="submit"]` ohne Container | Matcht Logout-, Sprach-, Such-Buttons | `#main-content button[type='submit']` |
| `DJANGO_SETTINGS_MODULE=...test` für E2E | MD5-Hasher korrumpiert Passwörter | `...settings.e2e` |
| Seed-Daten direkt mutieren | Andere Tests erwarten den Originalzustand | Eigene Objekte erstellen mit `uuid.uuid4().hex[:6]` |
| `locator("text=...")` ohne Scope | Kann Desktop + Mobile Navigation matchen | Scopen auf `#main-content` oder `nav[aria-label='...']` |
| `time.sleep()` | Nicht deterministisch | `page.wait_for_timeout()`, `wait_for_url()`, `expect().to_be_visible()` |
| `runserver` statt gunicorn | Single-threaded, blockiert bei Last | conftest startet gunicorn automatisch |
| `stdout=subprocess.PIPE` für gunicorn | Pipe-Buffer füllt sich nach ~25 Requests | `stdout=subprocess.DEVNULL` |

### Drei Parallel-Last-Anti-Patterns (Refs [#849](https://github.com/tobiasnix/anlaufstelle/issues/849))

Diese Patterns sind im seriellen Lauf meist grün, fallen aber unter `make test-e2e-parallel` (4 Worker, gunicorn 2x2 Slots) reproduzierbar um. Vermeiden — alternative Patterns nutzen.

**1. `inner_text() / count() / assert` direkt nach `expect_response(...)`**

`page.expect_response` wartet nur auf den Server-Response, **nicht** auf den anschließenden HTMX-DOM-Swap. Unter Last liest die Folge-Assertion den alten Wert.

```python
# ✗ Race
with page.expect_response(...):
    page.locator("button").click()
text = locator.inner_text()         # evtl. Pre-Swap-Wert
assert "Neu" in text

# ✓ Auto-wartendes expect()
with page.expect_response(...):
    page.locator("button").click()
expect(locator).to_contain_text("Neu", timeout=10000)
expect(locator).to_have_count(0, timeout=10000)
```

**2. Hartkodierte enge Timeouts (3s) für Zustandsübergänge**

Unter Parallel-Last reagiert der Server langsamer. 3s ist zu knapp für HTMX-/Alpine-Übergänge.

```python
# ✗ Zu knapp
locator.wait_for(state="visible", timeout=3000)

# ✓ Standardwert für State-Transitions
locator.wait_for(state="visible", timeout=10000)
```

**3. `.first.click()` / `.nth(N).click()` ohne deterministische Vorbedingung**

Bei kleiner, zufälliger Seed-Menge ist Listen-Sortierung nicht garantiert.

```python
# ✗ Wer ist „first"?
page.locator("#inbox-content a").first.click()

# ✓ Eigene Test-Fixture anlegen
unique_title = f"E2E-{uuid.uuid4().hex[:6]}"
page.goto(f"{base_url}/workitems/new/")
page.fill("input[name='title']", unique_title)
page.locator(SUBMIT).click()
page.locator(f"a:has-text('{unique_title}')").click()
```

`.first.click()` ist nur dann zulässig, wenn der Selektor bereits eindeutig (z.B. `a:has-text('Stern-42')` mit Unique-Pseudonym) ist oder die Test-Assertion für jedes Listenelement gilt (z.B. Berechtigungsprüfung über alle Rollen hinweg).

---

## 5. Fixture-Referenz

| Fixture | Scope | Rolle | User | Typischer Einsatz |
|---------|-------|-------|------|-------------------|
| `authenticated_page` | function | Admin | admin | Standard für die meisten Tests |
| `lead_page` | function | Leitung | thomas | Statistik, Löschanträge genehmigen |
| `staff_page` | function | Fachkraft | miriam | CRUD, Facharbeit |
| `assistant_page` | function | Assistenz | lena | Eingeschränkte Rechte testen |
| `base_url` | session | — | — | `http://127.0.0.1:8844` |
| `admin_url` | session | — | — | `{base_url}/admin-mgmt` |

**Passwort (alle Seed-User):** `anlaufstelle2026`

**Ad-hoc-Login:** `login_as(browser, base_url, username)` — gibt `(page, context)` zurück. Verbraucht Rate-Limit-Versuch, daher bevorzugt die Session-Fixtures nutzen.

Details: [e2e-architecture.md § Fixture-Architektur](e2e-architecture.md#fixture-architektur).

---

## 6. Manueller E2E-Server (ohne pytest)

Für Debugging oder manuelles Browser-Testing gegen die E2E-Datenbank:

```bash
# 1. Port freimachen
pkill -f 'gunicorn.*8844' 2>/dev/null || true

# 2. Migrieren + Seeden
DJANGO_SETTINGS_MODULE=anlaufstelle.settings.e2e \
  .venv/bin/python src/manage.py migrate --run-syncdb

DJANGO_SETTINGS_MODULE=anlaufstelle.settings.e2e \
  .venv/bin/python src/manage.py seed --flush

# 3. Static Files
DJANGO_SETTINGS_MODULE=anlaufstelle.settings.e2e \
  .venv/bin/python src/manage.py collectstatic --noinput

# 4. Server starten
DJANGO_SETTINGS_MODULE=anlaufstelle.settings.e2e \
  .venv/bin/gunicorn anlaufstelle.wsgi:application \
  --bind 127.0.0.1:8844 --workers 2 --threads 2 --chdir src --timeout 120

# 5. Browser öffnen: http://127.0.0.1:8844/login/
#    Login: admin / anlaufstelle2026
```

---

## 7. Einzelne Tests ausführen und debuggen

```bash
# Einzelner Test
make test-e2e ARGS="-x src/tests/e2e/test_cases.py::TestCaseCRUD::test_create_case"

# Ganze Datei
.venv/bin/python -m pytest -m e2e --browser chromium -v \
  src/tests/e2e/test_cases.py

# Headed (Browser sichtbar)
.venv/bin/python -m pytest -m e2e --browser chromium --headed -v -x \
  src/tests/e2e/test_cases.py

# Nur fehlgeschlagene Tests wiederholen
.venv/bin/python -m pytest -m e2e --browser chromium -v --lf
```

### Dreistufige Verifikation neuer Tests

1. **Einzeln:** Jeden neuen Test einzeln ausführen bis grün (`pytest -x ...::test_name`)
2. **Gruppe:** Alle neuen Tests als Gruppe zusammen (`pytest -x .../test_file.py`)
3. **Gesamt:** Gesamtlauf über alle Tests (`make test-e2e`)

Bei Fehlern: fixen, prüfen ob verwandte Tests denselben Root Cause teilen, dann Stufe erneut.

---

## 8. Wait-Strategie-Entscheidungsbaum

```
Situation?
├── Nach page.goto()
│   └── page.wait_for_load_state("domcontentloaded")
│
├── Nach Formular-Submit (URL wechselt)
│   └── page.wait_for_url(re.compile(r"/expected/path/"))
│
├── Nach HTMX-Aktion (URL bleibt gleich)
│   ├── Spezifisches Element erwartet?
│   │   └── expect(page.locator("...")).to_be_visible()
│   └── Allgemein?
│       └── page.wait_for_load_state("domcontentloaded")
│
├── Alpine.js Debounce (Autocomplete, Suche)
│   └── page.wait_for_timeout(500)
│
└── Alpine.js Transition (Dropdown, Modal)
    └── page.wait_for_timeout(300)
```

**Niemals:** `networkidle`, `time.sleep()`.

---

## 9. Seed-Daten-Referenz (Small-Scale)

E2E-Tests nutzen standardmäßig `--scale=small` (via conftest.py):

| Daten | Anzahl | Details |
|-------|--------|---------|
| Einrichtungen | 1 | „Anlaufstelle Altstadt" |
| Users | 4 | admin, thomas (Leitung), miriam (Fachkraft), lena (Assistenz) |
| Clients | 7 | Stern-42, Wolke-17, Blitz-08, Regen-55, Wind-33, Nebel-71, Sonne-99 |
| Events | 25 | Verteilt über 80 Tage |
| Cases | 3 | |
| WorkItems | 5 | |
| Dokumenttypen | 6 | Kontakt, Krisenintervention, Streetwork, Bedarfserhebung, Weitervermittlung, Nachbetreuung |

**Bekannte Clients:** `Stern-42` ist qualifiziert (18–26), `Blitz-08` ist identifiziert (U18). Für Tests die einen qualifizierten Client brauchen, `Stern-42` verwenden.

**Passwort (alle User):** `anlaufstelle2026`

Scale-Profile: [CONTRIBUTING.md § Seed-Daten](../CONTRIBUTING.md#seed-daten-laden).
