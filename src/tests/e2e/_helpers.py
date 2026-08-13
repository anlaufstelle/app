"""Wiederverwendbare Helfer für E2E-Tests."""

import os
import subprocess
import sys


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


def enter_sudo_mode(page, base_url, password="anlaufstelle2026"):
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
