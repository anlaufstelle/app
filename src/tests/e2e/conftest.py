"""E2E-Test-Fixtures: Live-Server, Seed-Daten, Login-Helfer.

E2E-Tests laufen gegen die echte Dev-Datenbank (nicht die Test-DB).
Der Server wird als Subprocess gestartet, Seed-Daten werden einmalig geladen.
Login-Sessions werden per Storage-State wiederverwendet (verhindert Rate-Limiting).

Bei paralleler Ausführung (pytest-xdist) bekommt jeder Worker:
- eigene Datenbank (anlaufstelle_e2e, anlaufstelle_e2e_1, ...)
- eigenen gunicorn-Prozess auf eigenem Port (8844, 8845, ...)
"""

import os
import signal
import subprocess
import sys
import time

import pytest
import requests
from filelock import FileLock

_TEST_ENV = {**os.environ, "DJANGO_SETTINGS_MODULE": "anlaufstelle.settings.test"}
# E2E-Server + Seed mit e2e-Settings (Dev/PBKDF2 + kein Rate-Limit).
# NICHT test-Settings verwenden — Django rehashed Passwörter bei Login
# automatisch auf den primären Hasher (MD5), was den Dev-Login zerstört.
_E2E_ENV = {**os.environ, "DJANGO_SETTINGS_MODULE": "anlaufstelle.settings.e2e"}


def _get_worker_info(request):
    """Worker-Nummer und -ID für xdist-Parallelisierung."""
    worker_input = getattr(request.config, "workerinput", None)
    if worker_input:
        wid = worker_input["workerid"]  # "gw0", "gw1", ...
        return int(wid.replace("gw", "")), wid
    return 0, "master"


def _run_or_die(cmd, env, label):
    """Subprocess + bei Exit != 0 vollen stdout/stderr in die Exception-Message einbetten.

    pytest captured ``sys.stderr`` von Fixtures, also reicht ein einfacher ``sys.stderr.write``
    nicht — die Ausgabe taucht im Test-Setup-Error nicht auf. RuntimeError mit komplettem
    Inhalt im Message-String wird dagegen vollständig im pytest-Traceback gerendert.
    """
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        msg = (
            f"FAIL: {label} (exit {result.returncode})\n"
            f"--- STDOUT ---\n{result.stdout}\n"
            f"--- STDERR ---\n{result.stderr}\n"
        )
        raise RuntimeError(msg)
    return result


def _create_e2e_db(python, db_name):
    """E2E-Datenbank erstellen falls nicht vorhanden."""
    subprocess.run(
        [
            python,
            "-c",
            "import psycopg; "
            "conn = psycopg.connect("
            "  host='{host}', port='{port}', user='{user}', password='{pw}', dbname='{db}', autocommit=True"
            "); "
            "cur = conn.cursor(); "
            "cur.execute(\"SELECT 1 FROM pg_database WHERE datname='{e2e_db}'\"); "
            "exists = cur.fetchone(); "
            "cur.execute('CREATE DATABASE {e2e_db}') if not exists else None; "
            "conn.close()".format(
                host=os.environ.get("POSTGRES_HOST", "localhost"),
                port=os.environ.get("POSTGRES_PORT", "5432"),
                user=os.environ.get("POSTGRES_USER", "anlaufstelle"),
                pw=os.environ.get("POSTGRES_PASSWORD", "anlaufstelle"),
                db=os.environ.get("POSTGRES_DB", "anlaufstelle"),
                e2e_db=db_name,
            ),
        ],
        check=True,
        capture_output=True,
    )


def _kill_port(port):
    """Alte Prozesse auf einem Port beenden."""
    try:
        stale = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True)
    except FileNotFoundError:
        subprocess.run(["pkill", "-f", f"gunicorn.*{port}"], capture_output=True)
        stale = None
    if stale and stale.stdout.strip():
        for pid in stale.stdout.split():
            try:
                os.kill(int(pid), signal.SIGTERM)
            except (ProcessLookupError, ValueError):
                pass
        time.sleep(1)


def _wait_for_server(url, retries=30, interval=0.5):
    """Warten bis der Server auf /login/ antwortet."""
    for _ in range(retries):
        try:
            resp = requests.get(f"{url}/login/", timeout=1)
            if resp.status_code == 200:
                return
        except (requests.ConnectionError, requests.ReadTimeout):
            time.sleep(interval)
    raise RuntimeError(f"Server konnte nicht gestartet werden: {url}")


@pytest.fixture(scope="session")
def django_db_setup():
    """Override: E2E-Tests brauchen keine Test-DB, der Server nutzt die echte Dev-DB."""


@pytest.fixture(scope="session")
def base_url(request, tmp_path_factory):
    """Startet den Django-Dev-Server mit Seed-Daten für die gesamte Test-Session.

    Bei xdist-Parallelisierung bekommt jeder Worker eigene DB + eigenen Port.
    """
    worker_num, worker_id = _get_worker_info(request)
    port = 8844 + worker_num
    db_name = "anlaufstelle_e2e" if worker_num == 0 else f"anlaufstelle_e2e_{worker_num}"

    python = ".venv/bin/python" if os.path.exists(".venv/bin/python") else sys.executable
    worker_env = {**_E2E_ENV, "E2E_DATABASE_NAME": db_name}

    # collectstatic nur einmal (filelock über shared tmp-Verzeichnis)
    root_tmp = tmp_path_factory.getbasetemp().parent
    with FileLock(str(root_tmp / "collectstatic.lock")):
        if not (root_tmp / "collectstatic.done").exists():
            subprocess.run(
                [python, "src/manage.py", "collectstatic", "--noinput"],
                check=True,
                capture_output=True,
                env=worker_env,
            )
            (root_tmp / "collectstatic.done").touch()

    # E2E-Datenbank erstellen + Migrationen + Seed (pro Worker)
    _create_e2e_db(python, db_name)
    _run_or_die(
        [python, "src/manage.py", "migrate", "--run-syncdb"],
        env=worker_env,
        label="migrate --run-syncdb",
    )
    _run_or_die(
        [python, "src/manage.py", "seed", "--flush"],
        env=worker_env,
        label="seed --flush",
    )

    # Alte gunicorn-Prozesse auf diesem Port beenden
    _kill_port(port)

    # gunicorn statt runserver: 2 Workers + 2 Threads = 4 parallele Request-Slots.
    # Verhindert Timeouts durch Request-Stau (Session-Saves, HTMX, WeasyPrint).
    proc = subprocess.Popen(
        [
            python,
            "-m",
            "gunicorn",
            "anlaufstelle.wsgi:application",
            "--bind",
            f"127.0.0.1:{port}",
            "--workers",
            "2",
            "--threads",
            "2",
            "--chdir",
            "src",
            "--timeout",
            "120",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=worker_env,
    )

    url = f"http://127.0.0.1:{port}"
    _wait_for_server(url)

    yield url

    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=10)


@pytest.fixture(scope="session")
def e2e_env(base_url):
    """Worker-spezifische Umgebungsvariablen für manage.py-Aufrufe in Tests.

    Tests die manage.py shell/Befehle brauchen, MÜSSEN dieses Fixture nutzen
    statt eigene _E2E_ENV zu konstruieren — sonst landet der Befehl bei
    paralleler Ausführung auf der falschen Datenbank.
    """
    from urllib.parse import urlparse

    port = urlparse(base_url).port
    worker_num = port - 8844
    db_name = "anlaufstelle_e2e" if worker_num == 0 else f"anlaufstelle_e2e_{worker_num}"
    return {**_E2E_ENV, "E2E_DATABASE_NAME": db_name}


@pytest.fixture(scope="session")
def admin_url(base_url):
    """Admin-Base-URL — Single Source of Truth für den obfuskierten Admin-Pfad."""
    return f"{base_url}/admin-mgmt"


def _create_storage_state(browser, base_url, username, password="anlaufstelle2026"):
    """Einmalig einloggen und Storage-State für einen User speichern."""
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.goto(f"{base_url}/login/")
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')
    page.wait_for_url(lambda url: "/login/" not in url, timeout=10000)
    storage = context.storage_state()
    context.close()
    return storage


@pytest.fixture(scope="session")
def _login_storage_state(base_url, browser):
    """Einmalig einloggen und Storage-State speichern (Session-Cookies)."""
    return _create_storage_state(browser, base_url, "admin")


@pytest.fixture(scope="session")
def _lead_storage_state(base_url, browser):
    """Storage-State für Lead-User (thomas)."""
    return _create_storage_state(browser, base_url, "thomas")


@pytest.fixture(scope="session")
def _staff_storage_state(base_url, browser):
    """Storage-State für Staff-User (miriam)."""
    return _create_storage_state(browser, base_url, "miriam")


@pytest.fixture(scope="session")
def _assistant_storage_state(base_url, browser):
    """Storage-State für Assistant-User (lena)."""
    return _create_storage_state(browser, base_url, "lena")


def _setup_page(context):
    """Page mit Timeouts für lokalen Server erstellen."""
    page = context.new_page()
    page.set_default_timeout(30000)
    page.set_default_navigation_timeout(30000)
    return page


def _cleanup_browser_state(page) -> None:
    """Defensiv: IndexedDB, Service Worker und Caches im aktuellen Origin
    leeren, bevor der Context geschlossen wird.

    Hintergrund (Refs #668): ``browser.new_context(...)`` erzeugt zwar
    isolierte Cookies/Storage, aber Playwright/Chromium koennen in
    selteneren Faellen Service-Worker-Registrierungen oder Cache-API-
    Eintraege ueber Context-Grenzen hinweg sichtbar machen, wenn Tests
    den Browser-Prozess teilen. ``context.close()`` raeumt zwar auf,
    aber ein expliziter Cleanup vor dem Close gibt eine zusaetzliche
    Sicherheitsschicht und macht die Zustands-Annahme explizit. Ohne
    diesen Cleanup waren Folge-Tests in derselben Session oft flaky:
    leere IndexedDB / leerer SW-Cache wurde nicht wirklich leer.

    Wird best-effort ausgefuehrt — bei Page-Close oder JS-Disabled
    Edge-Cases einfach ignoriert.
    """
    try:
        page.evaluate(
            """
            async () => {
                if ('serviceWorker' in navigator) {
                    const regs = await navigator.serviceWorker.getRegistrations();
                    await Promise.all(regs.map(r => r.unregister()));
                }
                if (window.indexedDB && indexedDB.databases) {
                    const dbs = await indexedDB.databases();
                    await Promise.all(dbs.map(db => new Promise((resolve) => {
                        const req = indexedDB.deleteDatabase(db.name);
                        req.onsuccess = req.onerror = req.onblocked = resolve;
                    })));
                }
                if (window.caches) {
                    const keys = await caches.keys();
                    await Promise.all(keys.map(k => caches.delete(k)));
                }
                try { localStorage.clear(); } catch (_) {}
                try { sessionStorage.clear(); } catch (_) {}
            }
            """
        )
    except Exception:
        # Page already closed, navigation interrupted, etc. — nicht
        # kritisch, der Context wird sowieso gleich geschlossen.
        pass


@pytest.fixture
def authenticated_page(base_url, browser, _login_storage_state):
    """Playwright-Page mit eingeloggtem Admin-User (wiederverwendete Session)."""
    context = browser.new_context(storage_state=_login_storage_state, locale="de-DE")
    page = _setup_page(context)
    page.goto(f"{base_url}/")
    yield page
    _cleanup_browser_state(page)
    context.close()


@pytest.fixture
def lead_page(base_url, browser, _lead_storage_state):
    """Playwright-Page mit eingeloggtem Lead-User (thomas)."""
    context = browser.new_context(storage_state=_lead_storage_state, locale="de-DE")
    page = _setup_page(context)
    page.goto(f"{base_url}/")
    yield page
    _cleanup_browser_state(page)
    context.close()


@pytest.fixture
def staff_page(base_url, browser, _staff_storage_state):
    """Playwright-Page mit eingeloggtem Staff-User (miriam)."""
    context = browser.new_context(storage_state=_staff_storage_state, locale="de-DE")
    page = _setup_page(context)
    page.goto(f"{base_url}/")
    yield page
    _cleanup_browser_state(page)
    context.close()


@pytest.fixture
def assistant_page(base_url, browser, _assistant_storage_state):
    """Playwright-Page mit eingeloggtem Assistant-User (lena)."""
    context = browser.new_context(storage_state=_assistant_storage_state, locale="de-DE")
    page = _setup_page(context)
    page.goto(f"{base_url}/")
    yield page
    _cleanup_browser_state(page)
    context.close()


def login_as(browser, base_url, username, password="anlaufstelle2026"):
    """Helfer: Einloggen als beliebiger User; gibt (page, context) zurück.

    ACHTUNG: Jeder Aufruf verbraucht einen Rate-Limit-Versuch (5/min).
    Für Tests die wiederholt denselben User brauchen, nutze stattdessen
    die Fixtures: authenticated_page, lead_page, staff_page, assistant_page.
    """
    context = browser.new_context(locale="de-DE")
    page = _setup_page(context)
    page.goto(f"{base_url}/login/")
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')
    page.wait_for_url(lambda url: "/login/" not in url, timeout=10000)
    return page, context
