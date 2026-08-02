"""Wiederverwendbare Helfer für E2E-Tests."""


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
