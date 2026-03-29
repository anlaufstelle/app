"""E2E-Test-Fixtures: Live-Server, Seed-Daten, Login-Helfer.

E2E-Tests laufen gegen die echte Dev-Datenbank (nicht die Test-DB).
Der Server wird als Subprocess gestartet, Seed-Daten werden einmalig geladen.
Login-Sessions werden per Storage-State wiederverwendet (verhindert Rate-Limiting).
"""

import os
import signal
import subprocess
import sys
import time

import pytest
import requests

_TEST_ENV = {**os.environ, "DJANGO_SETTINGS_MODULE": "anlaufstelle.settings.test"}
# E2E-Server + Seed mit e2e-Settings (Dev/PBKDF2 + kein Rate-Limit).
# NICHT test-Settings verwenden — Django rehashed Passwörter bei Login
# automatisch auf den primären Hasher (MD5), was den Dev-Login zerstört.
_E2E_ENV = {**os.environ, "DJANGO_SETTINGS_MODULE": "anlaufstelle.settings.e2e"}


@pytest.fixture(scope="session")
def django_db_setup():
    """Override: E2E-Tests brauchen keine Test-DB, der Server nutzt die echte Dev-DB."""


@pytest.fixture(scope="session")
def base_url():
    """Startet den Django-Dev-Server mit Seed-Daten für die gesamte Test-Session."""
    python = ".venv/bin/python" if os.path.exists(".venv/bin/python") else sys.executable

    # E2E-Datenbank (anlaufstelle_e2e) erstellen falls nicht vorhanden
    subprocess.run(
        [
            python,
            "-c",
            "import psycopg; "
            "conn = psycopg.connect("
            "  host='{host}', port='{port}', user='{user}', password='{pw}', dbname='{db}', autocommit=True"
            "); "
            "cur = conn.cursor(); "
            "cur.execute(\"SELECT 1 FROM pg_database WHERE datname='anlaufstelle_e2e'\"); "
            "exists = cur.fetchone(); "
            "cur.execute('CREATE DATABASE anlaufstelle_e2e') if not exists else None; "
            "conn.close()".format(
                host=os.environ.get("POSTGRES_HOST", "localhost"),
                port=os.environ.get("POSTGRES_PORT", "5432"),
                user=os.environ.get("POSTGRES_USER", "anlaufstelle"),
                pw=os.environ.get("POSTGRES_PASSWORD", "anlaufstelle"),
                db=os.environ.get("POSTGRES_DB", "anlaufstelle"),
            ),
        ],
        check=True,
        capture_output=True,
    )

    # Migrationen + Seed mit E2E-Settings (eigene DB, PBKDF2-Hasher, kein Rate-Limit)
    subprocess.run([python, "src/manage.py", "migrate", "--run-syncdb"], check=True, capture_output=True, env=_E2E_ENV)
    subprocess.run([python, "src/manage.py", "seed", "--flush"], check=True, capture_output=True, env=_E2E_ENV)

    # Static Files sammeln (WhiteNoise servt sie dann über gunicorn)
    subprocess.run(
        [python, "src/manage.py", "collectstatic", "--noinput"],
        check=True,
        capture_output=True,
        env=_E2E_ENV,
    )

    # Alte gunicorn-Prozesse auf Port 8844 beenden (Reste abgebrochener Läufe)
    try:
        stale = subprocess.run(["lsof", "-ti", ":8844"], capture_output=True, text=True)
    except FileNotFoundError:
        # lsof nicht verfügbar — Fallback: pkill
        subprocess.run(["pkill", "-f", "gunicorn.*8844"], capture_output=True)
        stale = None
    if stale and stale.stdout.strip():
        for pid in stale.stdout.split():
            try:
                os.kill(int(pid), signal.SIGTERM)
            except (ProcessLookupError, ValueError):
                pass
        time.sleep(1)

    # gunicorn statt runserver: 2 Workers + 2 Threads = 4 parallele Request-Slots.
    # Verhindert Timeouts durch Request-Stau (Session-Saves, HTMX, WeasyPrint).
    proc = subprocess.Popen(
        [
            python,
            "-m",
            "gunicorn",
            "anlaufstelle.wsgi:application",
            "--bind",
            "127.0.0.1:8844",
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
        env=_E2E_ENV,
    )

    url = "http://127.0.0.1:8844"
    for _ in range(30):
        try:
            resp = requests.get(f"{url}/login/", timeout=1)
            if resp.status_code == 200:
                break
        except requests.ConnectionError:
            time.sleep(0.5)
    else:
        proc.terminate()
        raise RuntimeError("gunicorn-Server konnte nicht gestartet werden")

    yield url

    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=10)


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


@pytest.fixture
def authenticated_page(base_url, browser, _login_storage_state):
    """Playwright-Page mit eingeloggtem Admin-User (wiederverwendete Session)."""
    context = browser.new_context(storage_state=_login_storage_state, locale="de-DE")
    page = _setup_page(context)
    page.goto(f"{base_url}/")
    yield page
    context.close()


@pytest.fixture
def lead_page(base_url, browser, _lead_storage_state):
    """Playwright-Page mit eingeloggtem Lead-User (thomas)."""
    context = browser.new_context(storage_state=_lead_storage_state, locale="de-DE")
    page = _setup_page(context)
    page.goto(f"{base_url}/")
    yield page
    context.close()


@pytest.fixture
def staff_page(base_url, browser, _staff_storage_state):
    """Playwright-Page mit eingeloggtem Staff-User (miriam)."""
    context = browser.new_context(storage_state=_staff_storage_state, locale="de-DE")
    page = _setup_page(context)
    page.goto(f"{base_url}/")
    yield page
    context.close()


@pytest.fixture
def assistant_page(base_url, browser, _assistant_storage_state):
    """Playwright-Page mit eingeloggtem Assistant-User (lena)."""
    context = browser.new_context(storage_state=_assistant_storage_state, locale="de-DE")
    page = _setup_page(context)
    page.goto(f"{base_url}/")
    yield page
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
