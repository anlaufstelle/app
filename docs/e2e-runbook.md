# E2E-Runbook

Praktisches Nachschlagewerk für E2E-Tests — Schritt-für-Schritt-Anleitungen, Troubleshooting und Code-Rezepte.

Für Architektur, Settings-Kette und Fixture-Design siehe [e2e-architecture.md](e2e-architecture.md).

---

## 1. Checkliste: E2E von Null zum grünen Lauf

Die `conftest.py` erledigt DB-Erstellung, Migration, Seed und `collectstatic` automatisch. Diese Checkliste sichert nur die **Voraussetzungen**.

| # | Schritt | Befehl | Prüfung |
|---|---------|--------|---------|
| 1 | PostgreSQL läuft | `sudo docker compose up -d db` | `pg_isready -h localhost` → Exit 0 |
| 2 | Port 8844 frei | `lsof -i :8844` | Keine Ausgabe |
| 3 | Eigenen vorherigen Server beenden | Im zugehörigen Terminal `Strg-C` | Der eigene `make e2e-server`-Lauf endet und gibt den Lock frei; fremde Läufe abwarten |
| 4 | Venv aktiv | `source .venv/bin/activate` | `which python` zeigt `.venv/` |
| 5 | Playwright-Browser passend zur Version | `python -m playwright install chromium` | Kein „Executable doesn't exist at …" |
| 6 | Tailwind gebaut | `make tailwind-build` | `src/static/css/styles.css` existiert |
| 7 | Tests starten | `make test-e2e-locked` (bzw. `make test-e2e-halves-locked`; einzelne CI-Scheibe: `make test-e2e-slice-locked SLICES=8 INDEX=3`) | Grüne Ausgabe |

> **Hinweis:** Docker benötigt auf manchen Systemen `sudo`. Der Dev-Server (Port 8000/8443) muss **nicht** laufen — E2E nutzt Port 8844.

> **Schritt 5 ist nicht einmalig.** Jeder Playwright-Bump bindet einen **neuen Browser-Build** — die Paketversion allein reicht nicht. Nach `playwright 1.61 → 1.62` verlangte der Lauf `chromium_headless_shell-1234`, installiert waren 1223 und 1228; Folge waren **312 Setup-Fehler** in einem einzigen Lauf, alle mit „Executable doesn't exist". Nach jedem Bump der Playwright-Version in `requirements-dev.in` also `python -m playwright install chromium` nachziehen. Ein Blick ins Verzeichnis `~/.cache/ms-playwright/` genügt **nicht**: dort liegen die alten Builds weiterhin und sehen aus wie ein vollständiges Setup.

> **Lock nicht umgehen.** `CI_LOCK` verwendet `XDG_RUNTIME_DIR`, ersatzweise
> `TMPDIR`, sonst `/tmp`, jeweils mit `/anlaufstelle-ci.lock`. Alle Sitzungen mit
> gemeinsamen Ressourcen müssen denselben absoluten Pfad verwenden; bei
> unterschiedlichen Laufzeitverzeichnissen `CI_LOCK` einheitlich setzen. Die
> Targets brauchen `flock` (util-linux), das auf macOS nicht standardmäßig
> vorhanden ist.
>
> Die `-locked`-Targets serialisieren über ein globales `flock` (Refs #1840). Ein zweiter E2E-Lauf würde sonst dieselbe Datenbank `anlaufstelle_e2e` und denselben Port 8844 belegen — die Isolation über die xdist-Worker-Nummer wirkt nur **innerhalb** eines Laufs, nicht zwischen Sessions.

---

## 2. Troubleshooting: Häufige Fehler

### Fehlertabelle

| Symptom | Ursache | Fix |
|---------|---------|-----|
| `RuntimeError: gunicorn-Server konnte nicht gestartet werden` | Port 8844 belegt | `lsof -i :8844` zur Diagnose; eigenen Lauf im zugehörigen Terminal mit `Strg-C` beenden, fremden Lauf abwarten |
| `RuntimeError: gunicorn...` + DB-Connection-Error | PostgreSQL nicht gestartet | `sudo docker compose up -d db` → `pg_isready -h localhost` |
| `psycopg.OperationalError: database "anlaufstelle_e2e" does not exist` | conftest-Erstellung fehlgeschlagen | `make test-e2e-locked` lässt die conftest die E2E-Datenbank unter dem globalen Lock aufsetzen |
| Login-Redirect zurück zu `/login/` | Passwörter durch MD5 korrumpiert | Für manuelle Prüfungen `make e2e-server`: vorbereitet unter dem globalen Lock, leert die E2E-Datenbank |
| Login-Redirect + Rate-Limit-Meldung | Falsches Settings-Modul (Rate-Limit aktiv) | `DJANGO_SETTINGS_MODULE` muss `anlaufstelle.settings.e2e` sein |
| `TimeoutError` bei `page.goto()` | `networkidle` als Wait-Strategie | `grep -r "networkidle" src/tests/e2e/` → ersetzen durch `domcontentloaded` |
| `TimeoutError` nach Form-Submit | Kein `wait_for_url()` nach `.click()` | `page.wait_for_url(re.compile(r"/expected/path/"))` ergänzen |
| `strict mode violation` | Locator matcht mehrere Elemente | Container scopen (`#main-content`) oder `.first` nutzen |
| 404 auf CSS/JS (Seite ohne Styling) | `collectstatic` nicht gelaufen | Manuell: `make e2e-server` führt auch `collectstatic` unter dem globalen Lock aus |
| Seed-Client nicht gefunden (Stern-42 etc.) | Alte Daten nicht geflusht, Pagination | conftest nutzt `--flush`. Manuell: `make e2e-server` bereitet die E2E-Datenbank unter dem Lock neu vor. In Tests: per Filter suchen statt `.is_visible()` |
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

### Vier Last-/Timing-Anti-Patterns (Refs #849, #1855)

Die ersten drei sind im seriellen Lauf meist grün und fallen unter `make test-e2e-parallel` (4 Worker, gunicorn 2x2 Slots) reproduzierbar um; das vierte ist eine Timing-Lotterie, die in jedem Lauf zuschlagen kann. Vermeiden — alternative Patterns nutzen.

**1. `inner_text() / count() / assert` direkt nach `expect_response(...)`**

`page.expect_response` wartet nur auf den Server-Response, **nicht** auf den anschließenden HTMX-DOM-Swap. Unter Last liest die Folge-Assertion den alten Wert.

```python
# ✗ Race
with page.expect_response(...):
    page.locator("button").click()
text = locator.inner_text()  # evtl. Pre-Swap-Wert
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

**4. Interaktion direkt nach einem HTMX-Swap (Settle-Fenster, Refs #1855)**

`htmx:afterSwap` feuert, sobald der neue Knoten im DOM hängt — die Event-Listener bindet htmx aber erst `htmx.config.settleDelay` (20 ms) später in `processNode`. In diesem Fenster ist das Element sichtbar und bedienbar, ein `click`/`select_option` löst aber **keinen** Request aus: das Event verpufft lautlos (kein Konsolen-Fehler, kein 4xx, kein Netzverkehr). Ein nachgelagertes `expect_response` läuft dann in seinen vollen Timeout — Fehlerbild `Timeout 30000ms exceeded while waiting for event "response"`.

Playwrights auto-wartende Assertions schützen **nicht**: `to_be_visible()`/`to_have_count()` sind schon mit `afterSwap` erfüllt, also genau eine Settle-Runde zu früh — sie führen sogar erst in das Fenster hinein. Ein größeres Timeout hilft ebenfalls nicht (die Antwort kommt nie); Host-Last verschiebt das Fenster nur, mal davor, mal dahinter — daher „im Voll-Lauf rot, isoliert grün" ohne Codeänderung.

```python
# ✗ Rennen gegen das 20-ms-Settle-Fenster
expect(karte).to_be_visible()
with page.expect_response(...):
    karte.locator("select").select_option(value=v)

# ✓ Auf die htmx-Bindung warten (Helfer in src/tests/e2e/_helpers.py)
from ._helpers import wait_until_htmx_bound

expect(karte).to_be_visible()
wait_until_htmx_bound(page, karte.locator("select"))
with page.expect_response(...):
    karte.locator("select").select_option(value=v)
```

Mehrstufige Ketten (Swap löst den nächsten Request aus) **nicht** in eine Sammel-Erwartung auf den letzten Request packen, sondern die `expect_response`-Blöcke schachteln — dann nennt die Traceback-Zeile den hängenden Schritt statt nur „response".

---

## 5. Fixture-Referenz

| Fixture | Scope | Rolle | User | Typischer Einsatz |
|---------|-------|-------|------|-------------------|
| `authenticated_page` | function | Admin | admin | Standard für die meisten Tests |
| `lead_page` | function | Leitung | emma | Statistik, Löschanträge genehmigen |
| `staff_page` | function | Fachkraft | miriam | CRUD, Facharbeit |
| `assistant_page` | function | Assistenz | lena | Eingeschränkte Rechte testen |
| `base_url` | session | — | — | `http://127.0.0.1:8844` |
| `admin_url` | session | — | — | `{base_url}/admin-mgmt` |

**Passwort (alle Seed-User):** `anlaufstelle2026`

**Ad-hoc-Login:** `login_as(browser, base_url, username)` — gibt `(page, context)` zurück. Verbraucht Rate-Limit-Versuch, daher bevorzugt die Session-Fixtures nutzen.

Details: [e2e-architecture.md § Fixture-Architektur](e2e-architecture.md#fixture-architektur).

### Helfer-Referenz (`src/tests/e2e/_helpers.py`)

Diese Bausteine liegen zentral (#1839),
nicht dateilokal in den Spec-Dateien. **Nicht kopieren** — importieren:

| Helfer | Wofür |
|--------|-------|
| `SEED_PASSWORD` | Passwort aller Seed-User. Nicht in Tests verwenden, die das Passwort selbst ändern. |
| `do_real_login(page, base_url, username="miriam")` | Echter Login-POST **statt** Storage-State. Nötig, sobald der Test verschlüsselte Offline-Bundles schreibt/liest: nur der Login-Formular-POST löst `crypto_session.deriveSessionKey` aus. Die `authenticated_page`-Familie ist dafür **kein** Ersatz (Cookie ja, Krypto-Schlüssel nein). |
| `wait_for_active_service_worker(page, timeout_ms=8000)` | SW registriert **und** aktiviert **und** `navigator.serviceWorker.controller` gesetzt. Immer vor `context.set_offline(True)` — sonst greift der Precache nicht. |
| `wait_until(page, predicate, …)` | Python-seitiges Polling statt `page.wait_for_function` mit `async`-Callback (§ 4.1). |
| `wait_until_htmx_bound(page, locator)` | htmx-Settle-Fenster (§ 4.4). |
| `track_console_errors(page)` | Konsolen-Errors/JS-Exceptions einsammeln. |
| `enter_sudo_mode(page, base_url)` | SudoMode für sicherheitskritische Aktionen. |
| `django_shell(e2e_env, script)` / `python_bin()` | Vorbedingungen gegen die E2E-DB setzen. |

---

## 6. Manueller E2E-Server (ohne pytest)

Für Debugging oder manuelles Browser-Testing gegen die E2E-Datenbank:

```bash
make e2e-server
```

Das Target hält den globalen `CI_LOCK` von der Vorbereitung bis zum Ende des
Serverlaufs. Es prüft zuerst, ob Port 8844 frei ist, führt Migrationen,
**`seed --flush --scale=medium` (leert die E2E-Datenbank)** und `collectstatic`
aus und startet Gunicorn über den Venv-Interpreter (`python -m gunicorn`).
Voraussetzungen: laufende Docker-Datenbank und installierte Python-Abhängigkeiten.
Browser öffnen: `http://127.0.0.1:8844/login/`.

Mit `Strg-C` im zugehörigen Terminal beenden: Das Target beendet nur seinen
eigenen Server und gibt danach den Lock frei. Einen fremden E2E-Lauf abwarten;
Port oder Datenbank nicht außerhalb des Locks verändern. Für Screenshots dient
`make docs-screens` mit demselben Lock und derselben Vorbereitung.

---

## 7. Einzelne Tests ausführen und debuggen

```bash
# Einzelner Test
# Hinweis: `make test-e2e` nimmt KEINE ARGS/T-Variable entgegen (das Target ist
# `pytest -m e2e --browser chromium -v` ohne Platzhalter). Fuer einen einzelnen
# Test/eine Datei pytest direkt aufrufen:
.venv/bin/python -m pytest -m e2e --browser chromium -v \
  -x src/tests/e2e/test_cases.py::TestCaseCRUD::test_create_case

# Ganze Datei
.venv/bin/python -m pytest -m e2e --browser chromium -v \
  src/tests/e2e/test_cases.py

# Headed (Browser sichtbar)
.venv/bin/python -m pytest -m e2e --browser chromium --headed -v -x \
  src/tests/e2e/test_cases.py

# Nur fehlgeschlagene Tests wiederholen
.venv/bin/python -m pytest -m e2e --browser chromium -v --lf
```

### Eine rot gewordene CI-Scheibe nachstellen

Die E2E-CI faehrt (#1976)
eine Matrix aus acht seriellen Scheiben; der rote Job heisst `E2E-Scheibe 3`.
Dieselbe Scheibe lokal, mit demselben Schnitt:

```bash
# Welche Dateien liegen in welcher Scheibe?
.venv/bin/python scripts/e2e_slice.py --slices 8 --summary
.venv/bin/python scripts/e2e_slice.py --slices 8 --index 3

# Die Scheibe fahren (Lock, weil Port 8844 + anlaufstelle_e2e geteilt sind)
make test-e2e-slice-locked SLICES=8 INDEX=3
```

Der Schnitt haengt nur am Dateibestand, nicht an Runner oder Reihenfolge —
derselbe Commit ergibt lokal dieselbe Scheibe wie in der CI. Aendert sich der
Dateibestand (neue Testdatei), verschiebt sich die Verteilung; dann zuerst
`--summary` gegen den Commit des roten Laufs pruefen. Hintergrund zum
Aufteilungsschluessel: [e2e-architecture.md § Scheiben-Schnitt](e2e-architecture.md#scheiben-schnitt-ci-matrix-und-lokaler-fallback).

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

E2E-Tests nutzen standardmäßig `seed --flush --scale small` (via conftest.py; `small` ist der Default des Commands):

| Daten | Anzahl | Details |
|-------|--------|---------|
| Einrichtungen | 1 | „Hauptstelle“ |
| Users | 7 | admin (Anwendungsbetreuung), emma (Leitung), miriam + markus (Fachkraft), lena + felix (Assistenz), superadmin (Systemadministration, ohne Einrichtung) |
| Clients | 7 | Stern-42, Wolke-17, Blitz-08, Regen-55, Wind-33, Nebel-71, Sonne-99 |
| Events | 25 | Verteilt über 80 Tage |
| Cases | 3 | |
| WorkItems | 5 | |
| Dokumenttypen | 9 | Kontakt, Krisengespräch, Medizinische Versorgung, Spritzentausch, Begleitung, Beratungsgespräch, Vermittlung, Notiz, Hausverbot (Paket `default`, `src/core/packages/default.yaml`) |

**Bekannte Clients:** `Stern-42` ist qualifiziert (18–26), `Blitz-08` ist identifiziert (U18). Für Tests die einen qualifizierten Client brauchen, `Stern-42` verwenden.

**Passwort (alle User):** `anlaufstelle2026`

Scale-Profile: [CONTRIBUTING.md § Scale-Profile im Überblick](../CONTRIBUTING.md#scale-profile-im-überblick).
