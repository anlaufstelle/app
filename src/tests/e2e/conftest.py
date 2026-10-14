"""E2E-Test-Fixtures: Live-Server, Seed-Daten, Login-Helfer.

E2E-Tests laufen gegen die echte Dev-Datenbank (nicht die Test-DB).
Der Server wird als Subprocess gestartet, Seed-Daten werden einmalig geladen.
Login-Sessions werden per Storage-State wiederverwendet (verhindert Rate-Limiting).
Der Chromium-Prozess wird pro Testdatei recycelt (``browser``-Fixture, Refs #1787),
damit der volle serielle Lauf auf RAM-knappen Hosts nicht ins OOM laeuft.

Bei paralleler Ausführung (pytest-xdist) bekommt jeder Worker:
- eigene Datenbank (anlaufstelle_e2e, anlaufstelle_e2e_1, ...)
- eigenen gunicorn-Prozess auf eigenem Port (8844, 8845, ...)
"""

import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import pytest
import requests
from filelock import FileLock

from tests.e2e._helpers import SEED_PASSWORD  # absolut: test_e2e_storage_state_ttl laedt die Datei ohne Paketkontext

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
            with suppress(ProcessLookupError, ValueError):
                os.kill(int(pid), signal.SIGTERM)
        time.sleep(1)


def _wait_for_server(url, retries=30, interval=0.5):
    """Warten bis der Server auf /login/ antwortet.

    ``Connection: close`` ist Pflicht (Refs #1055): Der erste Probe-Request
    erreicht den Server oft noch während des App-Boots, läuft client-seitig
    in den 1s-ReadTimeout und der Socket bleibt im pytest-Prozess offen
    (requests/urllib3 räumt ihn nach ReadTimeout nicht zuverlässig ab).
    Ohne ``Connection: close`` hält gunicorn diese Verbindung als
    Keep-Alive aktiv und blockiert beim Teardown den graceful Shutdown
    um volle 30s (graceful_timeout) — ``proc.wait(timeout=10)`` läuft
    dann reproduzierbar in ``TimeoutExpired``.
    """
    for _ in range(retries):
        try:
            resp = requests.get(f"{url}/login/", timeout=1, headers={"Connection": "close"})
            if resp.status_code == 200:
                return
        except (requests.ConnectionError, requests.ReadTimeout):
            time.sleep(interval)
    raise RuntimeError(f"Server konnte nicht gestartet werden: {url}")


@pytest.fixture(scope="session")
def django_db_setup():
    """Override: E2E-Tests brauchen keine Test-DB, der Server nutzt die echte Dev-DB."""


def _static_fingerprint() -> str:
    """Fingerabdruck der Quellen fuer ``collectstatic``: Dateizahl + juengste mtime unter ``src/static``."""
    root = Path("src/static")
    count = 0
    newest = 0
    for path in root.rglob("*"):
        if path.is_file():
            count += 1
            newest = max(newest, int(path.stat().st_mtime))
    return f"{count}:{newest}"


@pytest.fixture(scope="session")
def base_url(request, tmp_path_factory):
    """Startet den Django-Dev-Server mit Seed-Daten für die gesamte Test-Session.

    Bei xdist-Parallelisierung bekommt jeder Worker eigene DB + eigenen Port.
    """
    worker_num, worker_id = _get_worker_info(request)
    port = int(os.environ.get("E2E_PORT_BASE", "8844")) + worker_num
    db_name = "anlaufstelle_e2e" if worker_num == 0 else f"anlaufstelle_e2e_{worker_num}"

    python = ".venv/bin/python" if os.path.exists(".venv/bin/python") else sys.executable
    worker_env = {**_E2E_ENV, "E2E_DATABASE_NAME": db_name}

    # collectstatic nur einmal je Stand (filelock über shared tmp-Verzeichnis).
    # Der Marker traegt einen Fingerabdruck von ``src/static`` (Dateizahl +
    # juengste mtime): ein Marker ohne Fingerabdruck liess collectstatic ueber
    # Tage hinweg aus, und gunicorn lieferte veraltete Kopien aus
    # ``staticfiles/`` — Specs neuer JS-Aenderungen scheiterten dann, obwohl
    # das Feature funktionierte (Refs #2187, #2175).
    root_tmp = tmp_path_factory.getbasetemp().parent
    stamp_file = root_tmp / "collectstatic.stamp"
    with FileLock(str(root_tmp / "collectstatic.lock")):
        fingerprint = _static_fingerprint()
        if stamp_file.read_text() != fingerprint if stamp_file.exists() else True:
            subprocess.run(
                [python, "src/manage.py", "collectstatic", "--noinput"],
                check=True,
                capture_output=True,
                env=worker_env,
            )
            stamp_file.write_text(fingerprint)

    # E2E-Datenbank erstellen + Migrationen + Seed (pro Worker)
    _create_e2e_db(python, db_name)
    _run_or_die(
        [python, "src/manage.py", "migrate", "--run-syncdb"],
        env=worker_env,
        label="migrate --run-syncdb",
    )
    # Refs #2285: ``E2E_SEED_SCALE=demo`` seedet den Szenariokatalog statt des
    # Standard-E2E-Seeds (Vorbedingung der Demo-Bereichs-Spec, zusammen mit
    # ``E2E_DEMO_MODE=1``); ohne Variable bleibt der bisherige Seed unveraendert.
    seed_command = [python, "src/manage.py", "seed", "--flush"]
    seed_scale = os.environ.get("E2E_SEED_SCALE", "")
    if seed_scale:
        seed_command += ["--scale", seed_scale]
    _run_or_die(
        seed_command,
        env=worker_env,
        label=" ".join(seed_command[2:]),
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
    worker_num = port - int(os.environ.get("E2E_PORT_BASE", "8844"))
    db_name = "anlaufstelle_e2e" if worker_num == 0 else f"anlaufstelle_e2e_{worker_num}"
    return {**_E2E_ENV, "E2E_DATABASE_NAME": db_name}


@pytest.fixture(scope="session")
def admin_url(base_url):
    """Admin-Base-URL — Single Source of Truth für den obfuskierten Admin-Pfad."""
    return f"{base_url}/admin-mgmt"


@pytest.fixture(scope="session")
def enforced_base_url(request, base_url):
    """Zweiter E2E-Server mit aktivem Rollen-MFA-Enforcement (A3.1, Refs #1019).

    Teilt DB + Seed mit ``base_url`` (dort bereits migriert/geseedet), startet
    aber einen separaten gunicorn mit ``E2E_MFA_ENFORCE_ROLES=1`` auf eigenem
    Port (``8944 + worker_num``). So bleibt der Default-Server (``base_url``)
    bewusst MFA-frei und nur hier greift der Rollen-Redirect auf ``/mfa/setup/``.
    """
    worker_num, _ = _get_worker_info(request)
    port = 8944 + worker_num
    db_name = "anlaufstelle_e2e" if worker_num == 0 else f"anlaufstelle_e2e_{worker_num}"
    python = ".venv/bin/python" if os.path.exists(".venv/bin/python") else sys.executable
    env = {**_E2E_ENV, "E2E_DATABASE_NAME": db_name, "E2E_MFA_ENFORCE_ROLES": "1"}

    _kill_port(port)
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
        env=env,
    )
    url = f"http://127.0.0.1:{port}"
    _wait_for_server(url)

    yield url

    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=10)


# Replay is released only inside a sync-orchestrator run whose revocation check
# succeeded (``offlineStore.isRevocationPending``). Specs that call
# ``replayQueue`` / ``replayModifiedEvent`` / ``replayAllModifiedEvents``
# directly use ``replayInCheckedRun(() => ...)`` to follow the production
# sequence (check, then replay) instead of bypassing the gate. A failed check
# is not turned into an error here: the replay itself then reports the pending
# revocation, exactly as it does for the sync-orchestrator.
_CHECKED_REPLAY_INIT_SCRIPT = """
window.replayInCheckedRun = (replay) => window.syncOrchestrator.runExclusive(async () => {
    await window.offlineStore.applyClientRevocations();
    return replay();
});
"""


def _install_checked_replay_helper(browser) -> None:
    """Add ``window.replayInCheckedRun`` to every context created from ``browser``."""
    create_context = browser.new_context

    def new_context(*args, **kwargs):
        # Opt-in for runs that observe page-side requests through ``page.route``:
        # an active service worker answers some of them before the route sees them.
        if os.environ.get("E2E_BLOCK_SERVICE_WORKERS") == "1":
            kwargs.setdefault("service_workers", "block")
        context = create_context(*args, **kwargs)
        context.add_init_script(_CHECKED_REPLAY_INIT_SCRIPT)
        return context

    browser.new_context = new_context


@pytest.fixture(scope="module")
def browser(launch_browser):
    """Ein Chromium-Prozess je Testdatei statt je Session (Refs #1787).

    Der volle serielle Lauf (~760 Tests, ~21 min) killt auf 7,5-GiB-Hosts ohne
    Swap den session-scoped Chromium per OOM im letzten Datei-Cluster — der
    Speicherverbrauch eines Browser-Prozesses waechst ueber die Session monoton
    (Renderer-Reste, Caches, SW-Registrierungen), auch wenn jeder Test seinen
    Context sauber schliesst. Modul-Scope kappt dieses Wachstum: Am Dateiende
    stirbt der Prozess und der naechste startet frisch.

    Ueberschreibt bewusst die session-scoped ``browser``-Fixture von
    pytest-playwright. Alle Consumer (``authenticated_page`` & Co., die
    function-scoped Fixtures in den Testdateien, ``browser.new_context()`` in
    Lockout-/MFA-/Offline-Tests) sind function-scoped und sehen davon nichts.
    Die Storage-States bleiben session-scoped — sie sind reines Cookie-JSON und
    haengen deshalb nicht mehr am ``browser``, sondern erzeugen sich ueber einen
    kurzlebigen eigenen Browser (siehe ``_create_storage_state``).
    """
    browser = launch_browser()
    _install_checked_replay_helper(browser)
    yield browser
    browser.close()


def _create_storage_state(launch_browser, base_url, username, password=SEED_PASSWORD):
    """Einmalig einloggen und Storage-State für einen User speichern.

    Nutzt einen eigenen, sofort wieder geschlossenen Browser statt der
    ``browser``-Fixture (Refs #1787): Die Storage-States sind session-scoped,
    ``browser`` ist seit dem Browser-Recycling nur noch modul-scoped — eine
    direkte Abhaengigkeit waere ein ScopeMismatch. Der zurueckgegebene
    Storage-State ist reines Cookie-JSON und ueberlebt das Schliessen des
    Browsers; die zugehoerige Server-Session bleibt ohnehin unabhaengig davon
    gueltig.
    """
    browser = launch_browser()
    try:
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.goto(f"{base_url}/login/")
        page.fill('input[name="username"]', username)
        page.fill('input[name="password"]', password)
        page.click('button[type="submit"]')
        page.wait_for_url(lambda url: "/login/" not in url, timeout=10000)
        storage = context.storage_state()
        context.close()
    finally:
        browser.close()
    return storage


# Der Login-View setzt ``set_expiry(session_timeout_minutes * 60)`` (Seed:
# 30 Minuten). Bis #1858 lief die Server-Session ABSOLUT ab — auf langsamen
# Runnern (Stage-CI: 38 min Gesamtlauf) landete ab Minute 30 jeder Test mit
# geteiltem Storage-State auf ``/login/`` (v0.80.0-Schnitt). Seit #1858
# erneuert Aktivitaet das SERVER-Fenster, der Cookie im hier eingefrorenen
# Snapshot behaelt aber sein ``expires`` vom Login-Zeitpunkt: ein gecachter
# State ist ~30 Minuten nach seiner ERZEUGUNG tot, egal wie oft er benutzt
# wurde (Set-Cookie-Refreshes erreichen nur den lebenden Kontext, nie den
# Snapshot). Deshalb sind die Storage-States function-scoped mit
# session-weitem Cache: ein State wird wiederverwendet, bis er
# _STORAGE_STATE_TTL_S alt ist, und dann per frischem Login ersetzt.
# TTL = halbes Session-Fenster, damit auch der langsamste Einzeltest
# (Timeout 120 s) nie in den Ablauf laeuft. Das absolute Sitzungs-Maximum aus
# #1868 (Default 12 h ab Login) liegt um Groessenordnungen darueber und ist
# fuer diese TTL folgenlos — es begrenzt die Server-Session, nicht den
# eingefrorenen Snapshot.
_STORAGE_STATE_TTL_S = 15 * 60

# Seed-Benutzername je Storage-State/Page-Fixture. Der Cache ist nach diesem
# Namen geschluesselt; die Page-Fixtures brauchen ihn beim Ruecksynchronisieren
# (siehe ``_sync_storage_state``).
_ADMIN_USER = "admin"
_LEAD_USER = "emma"
_STAFF_USER = "miriam"
_ASSISTANT_USER = "lena"
_SUPER_ADMIN_USER = "superadmin"


@pytest.fixture(scope="session")
def _storage_state_cache():
    """Session-weiter Cache ``username -> (monotonic-Zeitstempel, state)``."""
    return {}


def _session_cookie_value(cookies) -> str:
    """Wert des ``sessionid``-Cookies aus einer Playwright-Cookie-Liste."""
    for cookie in cookies:
        if cookie.get("name") == "sessionid":
            return cookie.get("value") or ""
    return ""


def _sync_storage_state(cache, username, context) -> None:
    """Gecachten Storage-State aus dem LEBENDEN Kontext nachziehen (Refs #2034).

    Seit #2034 rotiert der Server die Django-Session-ID bei jeder
    Rechteanhebung innerhalb der Sitzung — MFA-Verify (TOTP/Backup-Code),
    Setup-Verify und -Abschluss, Backup-Code-Neuerzeugung, Passkey-Assertion,
    Sudo-Grant und die Uebernahme eines Reset-/Invite-Tokens. ``cycle_key()``
    legt eine neue Zeile an und **loescht die alte**.

    Der Cache oben haelt aber einen EINGEFRORENEN Snapshot: sobald ein Test in
    seinem Kontext eine solche Anhebung durchlaeuft (typisch
    ``_helpers.enter_sudo_mode``), traegt der Snapshot einen Cookie, dessen
    Server-Session es nicht mehr gibt. Jeder spaetere Kontext aus demselben
    Snapshot startet abgemeldet und landet auf ``/login/?next=…`` — dieselbe
    Klasse wie die bekannte ``set_password``-Storage-State-Falle. Beobachtet im
    A1-Lauf: ``test_auth_roles.py::TestZZAccountLockout::test_admin_unlock_action_restores_login``
    holt Sudo auf der ``authenticated_page``; der unmittelbar folgende
    ``test_case_deletion_workflow.py::…::test_only_the_admin_sees_the_trash_link``
    war der erste von 47 Fehlschlaegen.

    Deshalb wird der Snapshot beim Abbau JEDER Page-Fixture aus dem lebenden
    Kontext erneuert. Zwei Faelle:

    * Der Kontext hat noch einen ``sessionid``-Cookie → Snapshot ersetzen. Der
      urspruengliche Zeitstempel bleibt stehen: die TTL misst weiterhin ab dem
      Login, und der rotierte Cookie laeuft ohnehin spaeter ab als der alte.
    * Kein ``sessionid`` mehr (der Test hat abgemeldet, Quick-Exit ausgeloest
      oder die Sitzung wurde serverseitig geflusht) → Eintrag verwerfen, damit
      der naechste Consumer sich frisch anmeldet statt einen toten Cookie zu erben.

    Das Produktionsverhalten bleibt unangetastet — die Rotation ist die Zusage
    aus #2034, nicht der Fehler. Nicht abgedeckt (und in dieser Testfamilie
    nicht vorhanden): ein Test, der INNERHALB seines Laufs eine Anhebung
    durchfuehrt und danach einen ZWEITEN Kontext aus demselben gecachten State
    oeffnet. Ein solcher Test muesste den Kontext selbst neu aufbauen.
    """
    entry = cache.get(username)
    if entry is None:
        return
    try:
        # ``context.cookies()`` statt ``context.storage_state()``: Letzteres
        # laedt bei einer Page ohne Utility-World (Test endet mitten in einer
        # Navigation) intern eine Storage-State-Seite nach, faellt auf den
        # nackten Origin zurueck (302 auf ``/login/``) und bleibt dort ohne
        # Timeout sporadisch haengen. ``cookies()`` liest die Cookies direkt
        # aus dem Kontext, ohne eigene Navigation.
        cookies = context.cookies()
    except Exception:
        # Kontext/Browser schon zu oder abgestuerzt: konservativ verwerfen.
        cache.pop(username, None)
        return
    if _session_cookie_value(cookies):
        # Nur die Cookies uebernehmen, kein ``origins``-Eintrag (localStorage)
        # — z. B. die Einklapp-Einstellung des Zeitstroms. Die wanderte sonst
        # als Snapshot in jeden folgenden Kontext desselben Nutzers und machte
        # ``test_zeitstrom_collapse`` reihenfolgeabhaengig rot (Voll-Lauf
        # Welle J). Der Login-Snapshot selbst ist reines Cookie-JSON;
        # localStorage gehoert jedem Kontext allein.
        cache[username] = (entry[0], {"cookies": cookies, "origins": []})
    else:
        cache.pop(username, None)


def _fresh_storage_state(cache, launch_browser, base_url, username):
    """Gecachten Storage-State liefern; nach Ablauf der TTL neu einloggen."""
    entry = cache.get(username)
    if entry is None or time.monotonic() - entry[0] > _STORAGE_STATE_TTL_S:
        state = _create_storage_state(launch_browser, base_url, username)
        cache[username] = (time.monotonic(), state)
    return cache[username][1]


@pytest.fixture
def _login_storage_state(base_url, launch_browser, _storage_state_cache):
    """Storage-State fuer Admin-User (Session-Cookies, TTL-erneuert)."""
    return _fresh_storage_state(_storage_state_cache, launch_browser, base_url, _ADMIN_USER)


@pytest.fixture
def _lead_storage_state(base_url, launch_browser, _storage_state_cache):
    """Storage-State für Lead-User (emma)."""
    return _fresh_storage_state(_storage_state_cache, launch_browser, base_url, _LEAD_USER)


@pytest.fixture
def _staff_storage_state(base_url, launch_browser, _storage_state_cache):
    """Storage-State für Staff-User (miriam)."""
    return _fresh_storage_state(_storage_state_cache, launch_browser, base_url, _STAFF_USER)


@pytest.fixture
def _assistant_storage_state(base_url, launch_browser, _storage_state_cache):
    """Storage-State für Assistant-User (lena)."""
    return _fresh_storage_state(_storage_state_cache, launch_browser, base_url, _ASSISTANT_USER)


@pytest.fixture
def _super_admin_storage_state(base_url, launch_browser, _storage_state_cache):
    """Storage-State für Super-Admin (superadmin) — Refs #867."""
    return _fresh_storage_state(_storage_state_cache, launch_browser, base_url, _SUPER_ADMIN_USER)


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
def authenticated_page(base_url, browser, _login_storage_state, _storage_state_cache):
    """Playwright-Page mit eingeloggtem Admin-User (wiederverwendete Session)."""
    context = browser.new_context(storage_state=_login_storage_state, locale="de-DE")
    page = _setup_page(context)
    page.goto(f"{base_url}/")
    try:
        yield page
    finally:
        # Refs #2034: Rotation waehrend des Tests in den Cache nachziehen.
        _sync_storage_state(_storage_state_cache, _ADMIN_USER, context)
        _cleanup_browser_state(page)
        context.close()


@pytest.fixture
def mobile_authenticated_page(base_url, browser, _login_storage_state, _storage_state_cache):
    """Playwright-Page mit iPhone-Viewport (375x667) und Touch-Emulation.

    Refs #928. Nutzbar für Mobile-Workflow-Tests, die das
    Card-Layout, Touch-Targets oder mobile Navigation prüfen.

    Verwendet ``_login_storage_state`` (Admin-User) wie ``authenticated_page``;
    Mobile-spezifische Rollen-Tests sollen weiterhin per ``login_as`` mit
    dedizierter Rolle laufen.
    """
    context = browser.new_context(
        storage_state=_login_storage_state,
        locale="de-DE",
        viewport={"width": 375, "height": 667},
        is_mobile=True,
        has_touch=True,
        device_scale_factor=2,
        user_agent=(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        ),
    )
    page = _setup_page(context)
    page.goto(f"{base_url}/")
    try:
        yield page
    finally:
        # Refs #2034: Rotation waehrend des Tests in den Cache nachziehen.
        _sync_storage_state(_storage_state_cache, _ADMIN_USER, context)
        _cleanup_browser_state(page)
        context.close()


@pytest.fixture
def lead_page(base_url, browser, _lead_storage_state, _storage_state_cache):
    """Playwright-Page mit eingeloggtem Lead-User (emma)."""
    context = browser.new_context(storage_state=_lead_storage_state, locale="de-DE")
    page = _setup_page(context)
    page.goto(f"{base_url}/")
    try:
        yield page
    finally:
        # Refs #2034: Rotation waehrend des Tests in den Cache nachziehen.
        _sync_storage_state(_storage_state_cache, _LEAD_USER, context)
        _cleanup_browser_state(page)
        context.close()


@pytest.fixture
def staff_page(base_url, browser, _staff_storage_state, _storage_state_cache):
    """Playwright-Page mit eingeloggtem Staff-User (miriam)."""
    context = browser.new_context(storage_state=_staff_storage_state, locale="de-DE")
    page = _setup_page(context)
    page.goto(f"{base_url}/")
    try:
        yield page
    finally:
        # Refs #2034: Rotation waehrend des Tests in den Cache nachziehen.
        _sync_storage_state(_storage_state_cache, _STAFF_USER, context)
        _cleanup_browser_state(page)
        context.close()


@pytest.fixture
def assistant_page(base_url, browser, _assistant_storage_state, _storage_state_cache):
    """Playwright-Page mit eingeloggtem Assistant-User (lena)."""
    context = browser.new_context(storage_state=_assistant_storage_state, locale="de-DE")
    page = _setup_page(context)
    page.goto(f"{base_url}/")
    try:
        yield page
    finally:
        # Refs #2034: Rotation waehrend des Tests in den Cache nachziehen.
        _sync_storage_state(_storage_state_cache, _ASSISTANT_USER, context)
        _cleanup_browser_state(page)
        context.close()


@pytest.fixture
def super_admin_page(base_url, browser, _super_admin_storage_state, _storage_state_cache):
    """Playwright-Page mit eingeloggtem Super-Admin (superadmin) — Refs #867.

    Landet nach Login auf ``/system/`` (siehe ``CustomLoginView.get_success_url``).
    Hier startet die Fixture trotzdem auf ``/system/`` direkt fuer
    deterministisches Verhalten.
    """
    context = browser.new_context(storage_state=_super_admin_storage_state, locale="de-DE")
    page = _setup_page(context)
    page.goto(f"{base_url}/system/")
    try:
        yield page
    finally:
        # Refs #2034: Rotation waehrend des Tests in den Cache nachziehen.
        _sync_storage_state(_storage_state_cache, _SUPER_ADMIN_USER, context)
        _cleanup_browser_state(page)
        context.close()


def login_as(browser, base_url, username, password=SEED_PASSWORD):
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


def pytest_collection_modifyitems(config, items):
    """Playwright-WebKit-Emulationsgrenzen als xfail werten (Refs #1418).

    Sonden-Befund 2026-08-26 (Kommentar am Ticket): der SW ist unter
    Playwright-WebKit aktiv UND kontrolliert die Seite (controllerchange
    erreicht, Precache gefuellt), aber ``context.set_offline`` kappt den
    Transport UNTERHALB des SW — fetch() und Navigation schlagen fehl, bevor
    der Fetch-Handler aus dem Precache antworten kann; zudem fehlt
    ``navigator.storage.persist`` im Linux-WebKit-Build komplett. Betroffene
    Tests tragen ``webkit_transport_artifact``: unter Chromium laufen sie
    unveraendert scharf, unter WebKit werden sie xfail (nicht strict — ein
    kuenftiges Playwright koennte die Luecke schliessen). Der massgebliche
    Nachweis fuer echtes iOS-Verhalten bleibt der manuelle Durchlauf
    (Anhang G, #1319).
    """
    for item in items:
        if "webkit_transport_artifact" not in item.keywords:
            continue
        callspec = getattr(item, "callspec", None)
        browser = callspec.params.get("browser_name") if callspec else None
        if browser == "webkit" or (browser is None and item.nodeid.endswith("[webkit]")):
            item.add_marker(
                pytest.mark.xfail(
                    reason="Playwright-WebKit-Emulationsgrenze (Refs #1418): "
                    "set_offline unterschreitet den SW-Transport bzw. "
                    "storage.persist-API fehlt im Linux-WebKit-Build",
                    strict=False,
                )
            )
