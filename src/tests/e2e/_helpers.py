"""Wiederverwendbare Helfer für E2E-Tests."""

import os
import re
import subprocess
import sys

SEED_PASSWORD = "anlaufstelle2026"
"""Passwort aller geseedeten E2E-Benutzer (Refs #1839).

Stand vorher: 49× als Literal in 29 Dateien unter ``src/tests/e2e/``.
NICHT verwenden, wo ein Test das Passwort selbst ÄNDERT und danach mit dem
neuen Wert weiterarbeitet — ``test_password_change_unsynced_guard.py`` und
``test_onboarding_journey.py`` tragen dafür bewusst eigene Konstanten.
"""


def python_bin() -> str:
    """Pfad zum Python-Interpreter fuer ``subprocess``-Aufrufe aus E2E-Tests.

    Refs #1829: ``.venv/bin/python`` falls vorhanden, sonst ``sys.executable``
    — stand als ``_python()`` byte-identisch in 10 E2E-Testdateien. Diese
    Fassung wird ueberall unter dem lokalen Namen ``_python`` importiert
    (``from ._helpers import python_bin as _python``), damit die Aufrufstellen
    unveraendert bleiben.
    """
    return ".venv/bin/python" if os.path.exists(".venv/bin/python") else sys.executable


def django_shell(e2e_env, script):
    """``manage.py shell -c`` gegen die E2E-Datenbank; liefert die Ausgabezeilen.

    Für Vorbedingungen, die sich nicht durchklicken lassen (eine Person mit
    einem bestimmten Pseudonym, ein Ereignis genau ohne Fallbezug). ``e2e_env``
    ist die Session-Fixture aus ``conftest.py`` — sie trägt
    ``DJANGO_SETTINGS_MODULE=anlaufstelle.settings.e2e``, ohne das das Skript
    die falsche Datenbank träfe.
    """
    out = subprocess.run(
        [python_bin(), "src/manage.py", "shell", "--no-imports", "-c", script],
        capture_output=True,
        text=True,
        env=e2e_env,
        check=True,
    )
    return [ln for ln in out.stdout.strip().splitlines() if ln.strip()]


def wait_until(page, predicate, timeout_ms=15000, interval_ms=250):
    """Python-seitig auf eine Bedingung pollen; ``True`` wenn sie eintrat.

    **Warum es diesen Helfer gibt (Harness-Falle):** ``page.wait_for_function``
    mit einer **async** Callback ist ein garantierter False-Positive.
    ``wait_for_function`` prüft den Rückgabewert auf Truthiness und ``await``et
    ihn NICHT — eine ``async``-Funktion liefert aber immer ein ``Promise``, und
    ein Promise-Objekt ist immer truthy. Der Aufruf kehrt also sofort zurück
    (gemessen: 0,006 s), egal ob die Bedingung gilt. Jede Zusicherung, die auf
    so einer Barriere aufsetzt, ist in Wahrheit ein Rennen gegen die Uhr.

    Genau daran ist ``test_reedit_of_unsynced_new_event_keeps_new_status_and_
    replays_via_create`` gescheitert, als der Sync-Orchestrator um einen
    Vorab-Schritt (Widerrufsliste, Refs #1760) wuchs: der zusätzliche
    Netz-Roundtrip schob den Replay-POST hinter das bis dahin zufällig
    gewonnene Rennen.

    Alles, was im Browser ein ``await`` braucht (IndexedDB-Zählungen,
    ``offlineStore``-Lookups), gehört deshalb hierher — ``page.evaluate``
    ``await``et Promises korrekt, also ist die Prüfung im Predicate sicher::

        assert wait_until(
            page,
            lambda: page.evaluate("async (pk) => window.offlineStore.getOfflineEvent(pk)", pk) is None,
        ), "Replay hat die lokale Row nicht aufgelöst"

    Rein synchrone Bedingungen (``() => window.foo``) dürfen weiterhin direkt
    mit ``page.wait_for_function`` geprüft werden — die Falle betrifft
    ausschließlich ``async``-Callbacks.

    ACHTUNG: Der Rückgabewert MUSS ausgewertet werden (``assert`` oder
    ``if not ...``). Ein Aufruf als blosse Anweisung wäre wieder genau die
    wirkungslose Barriere, die dieser Helfer ersetzt.
    """
    waited = 0
    while True:
        if predicate():
            return True
        if waited >= timeout_ms:
            return False
        page.wait_for_timeout(interval_ms)
        waited += interval_ms


def wait_until_htmx_bound(page, locator, timeout_ms=10000, interval_ms=50):
    """Warten, bis htmx ein frisch eingeswapptes Element wirklich verdrahtet hat.

    **Warum es diesen Helfer gibt (Refs #1855):** htmx meldet ``htmx:afterSwap``,
    sobald der neue Knoten im DOM haengt — seine Event-Listener bindet es aber
    erst in der Settle-Phase, also ``htmx.config.settleDelay`` (Default 20 ms)
    spaeter (``processNode`` laeuft als Settle-Task). In diesem Fenster ist das
    Element sichtbar *und* bedienbar — ein ``select_option``/``click``
    loest darin aber KEINEN Request aus: das DOM-Event verpufft, htmx bleibt
    still (kein Konsolen-Fehler, kein 4xx, kein Netzverkehr). Ein nachgelagertes
    ``expect_response`` laeuft dann in seinen vollen Timeout — genau das
    Fehlerbild ``Timeout ... while waiting for event "response"``, das #1855
    fuer mehrere E2E-Tests beschreibt.

    Playwrights auto-wartende Assertions decken das NICHT ab:
    ``to_be_visible``/``to_have_count`` sind bereits mit ``htmx:afterSwap``
    erfuellt, also eine Settle-Runde zu frueh — sie sind der Grund, warum der
    Test genau in dieses Fenster hineinlaeuft. Geprueft wird deshalb die
    Bindung selbst: htmx legt seinen Elementzustand als Property
    ``htmx-internal-data`` ab, und zwar erst in ``processNode``.

    Das Fenster ist ~20 ms breit, also eine reine Zeitlotterie: mehr Timeout
    hilft nicht (der Request kommt nie), mehr Host-Last verschiebt es nur — mal
    davor, mal dahinter. Das erklaert "im Voll-Lauf rot, isoliert gruen" ohne
    Codeaenderung.

    Python-seitig pollen (kein ``wait_for_function``, s. :func:`wait_until`).
    """
    if not wait_until(
        page,
        lambda: locator.evaluate("el => !!el['htmx-internal-data']"),
        timeout_ms=timeout_ms,
        interval_ms=interval_ms,
    ):
        raise AssertionError("htmx hat das frisch eingeswappte Element nicht verarbeitet (Settle blieb aus)")


def track_console_errors(page):
    """Sammelt Browser-Konsolen-Errors und JS-Exceptions einer Page in eine Liste.

    Wofür: CSP-Violations tauchen im Browser als Konsolen-``error`` auf ("Refused
    to load … because it violates the following Content Security Policy …"); nicht
    abgefangene JS-Exceptions als ``pageerror``. Beides einzusammeln ist der
    E2E-Verifikationspunkt für „keine CSP-Verstöße / keine JS-Fehler" (M0 P9,
    Refs #1056).

    Muster der Verwendung — **vor** der Navigation registrieren, danach asserten::

        errors = track_console_errors(page)
        page.goto(f"{base_url}/config/document-types/")
        page.wait_for_load_state("domcontentloaded")
        # … weitere Interaktionen …
        assert errors == [], f"Konsolen-Errors aufgetreten: {errors}"

    Rückgabe: die (fortlaufend befüllte) Liste der Fehler-Texte. Konsolen-Events
    feuern asynchron während Playwright-Aktionen/Waits — die Liste daher erst am
    Ende des Tests (nach allen erwarteten Loads/Swaps) prüfen.
    """
    errors: list[str] = []

    def _on_console(msg):
        # msg.type == "error" deckt console.error(...) UND Browser-Fehler wie
        # CSP-Verstöße und fehlgeschlagene Ressourcen-Loads ab.
        if msg.type == "error":
            errors.append(f"console.error: {msg.text}")

    def _on_pageerror(exc):
        errors.append(f"pageerror: {exc}")

    page.on("console", _on_console)
    page.on("pageerror", _on_pageerror)
    return errors


def enter_sudo_mode(page, base_url, password=SEED_PASSWORD):
    """SudoMode für die laufende Session aktivieren (Refs #683).

    Sicherheitskritische Aktionen (z.B. Datenauskunft-Export, 2FA-Deaktivierung)
    erfordern eine zusätzliche Passwort-Bestätigung, die 15 Minuten gültig ist.
    E2E-Tests müssen diesen Schritt vor solchen Aktionen einmalig durchlaufen.

    Nach Aufruf navigiert die Page zurück zur Startseite — der Test kann dann
    den eigentlichen Flow (z.B. Download-Klick) starten.
    """
    page.goto(f"{base_url}/sudo/?next=/", wait_until="domcontentloaded")
    page.fill('input[name="password"]', password)
    # Form mit Passwort-Feld hat einen einzigen Submit-Button "Bestätigen und fortfahren";
    # die anderen Submit-Buttons auf der Seite (Sprach-Switch, Logout) gehören zu
    # eigenständigen Forms — ``form:has(input[name="password"])`` selektiert
    # eindeutig die Sudo-Form.
    page.locator('form:has(input[name="password"]) button[type="submit"]').click()
    page.wait_for_url(lambda url: "/sudo/" not in url, timeout=10000)


def do_real_login(page, base_url, username="miriam", password=SEED_PASSWORD):
    """Echter Login-POST inkl. ``crypto_session``-Handshake (kein storage_state).

    ``storage_state`` würde den Browser zwar mit gültigem Session-Cookie
    ausstatten, aber den ``login.html``-fetch-Hook überspringen, der
    ``crypto_session.deriveSessionKey`` auslöst und den Schlüssel in IndexedDB
    persistiert. Ohne diesen In-Memory-Schlüssel scheitert
    ``offlineStore.saveClientBundle`` an ``EncryptionKeyMissing`` — verschlüsselte
    Person-Bundles lassen sich weder schreiben noch offline entschlüsseln.

    Die Storage-State-Fixtures aus ``conftest.py`` (``authenticated_page`` &
    Verwandte) sind daher **kein** Ersatz und umgekehrt: Dateien, die beide
    Pfade nutzen, tun das bewusst.

    Refs #1839: stand vorher 9× dateilokal als ``_do_real_login`` (7×
    ``#id_*``-Selektoren + Regex-Wait, 2× ``input[name=…]`` + Lambda-Wait —
    Playwright-äquivalent, kein dokumentierter Flake-Fix). Wird per Konvention
    unter dem alten lokalen Namen importiert
    (``from ._helpers import do_real_login as _do_real_login``), damit die
    Aufrufstellen unverändert bleiben.
    """
    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
    page.fill("#id_username", username)
    page.fill("#id_password", password)
    page.click("button[type=submit]")
    page.wait_for_url(re.compile(r"^(?!.*/login/)"), timeout=15000)
    # Warten, bis der In-Memory-Key aus IndexedDB rehydriert wurde.
    page.evaluate("async () => { await window.crypto_session.ready(); }")


def wait_for_active_service_worker(page, timeout_ms=8000):
    """Service Worker ist registriert, aktiviert UND kontrolliert die Page.

    Das erste ``page.goto`` registriert den SW; ein zweites ``goto``/``reload``
    ist nötig, damit er die Page tatsächlich kontrolliert
    (``navigator.serviceWorker.controller`` ist sonst ``null`` und Requests
    laufen am SW vorbei — der Precache greift dann offline nicht).

    Standardmuster der Offline-Journeys: erst diese Barriere, DANN
    ``context.set_offline(True)``.

    ``timeout_ms`` deckt nur den abschließenden ``controller``-Wait nach dem
    Reload ab. Refs #1839: stand vorher 10× dateilokal, 9× mit 5000 ms und
    1× (``test_offline_workitem_ui.py``) mit 8000 ms; die strengste Fassung ist
    hier Default, damit kein Aufrufer einen kürzeren Wait bekommt als vorher.
    Die Herkunft der 8000 ms ist nicht durch einen Fix-Commit belegt (sie stand
    schon im Erstcommit der Datei).
    """
    page.evaluate(
        """
        async () => {
            const reg = await navigator.serviceWorker.getRegistration('/');
            if (!reg) return;
            const sw = reg.active || reg.installing || reg.waiting;
            if (!sw || sw.state === 'activated') return;
            return new Promise((resolve) => {
                sw.addEventListener('statechange', () => {
                    if (sw.state === 'activated') resolve();
                });
                setTimeout(resolve, 5000);
            });
        }
        """
    )
    if not page.evaluate("() => !!navigator.serviceWorker.controller"):
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("() => !!navigator.serviceWorker.controller", timeout=timeout_ms)
