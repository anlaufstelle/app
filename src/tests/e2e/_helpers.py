"""Wiederverwendbare Helfer für E2E-Tests."""


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
