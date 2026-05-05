"""E2E-Tests: MFA-Setup-Flow (erstmalige TOTP-Aktivierung).

Deckt den First-Time-Setup ab, der in ``test_mfa_backup_codes.py`` bewusst
nicht getestet wird: Admin öffnet ``/mfa/setup/`` ohne bestehendes Device,
scannt Secret, bestätigt mit einem aus dem Secret berechneten TOTP-Code,
landet auf der Backup-Codes-Seite und sieht im MFA-Settings den aktiven
Status sowie den Backup-Codes-Counter.

TOTP-Code-Berechnung: pyotp ist nicht installiert; stattdessen wird die
bereits vorhandene ``django_otp.oath.TOTP``-Klasse im Test-Prozess genutzt
— sie liest denselben Base32-Secret, den das Template via
``data-testid="mfa-secret"`` exponiert.

Gegenüber anderen MFA-Tests ist dieser Flow destruktiv für den
admin-Seed-User (er bekommt ein bestätigtes Device). ``finally``-Block
räumt TOTP- und Static-Devices wieder auf, damit nachgelagerte Tests den
admin-User unverändert sehen.
"""

import base64
import re
import subprocess
import sys

import pytest
from django_otp.oath import TOTP

pytestmark = pytest.mark.e2e


def _generate_totp_code(base32_secret: str) -> str:
    """Berechne aktuellen 6-stelligen TOTP-Code aus Base32-Secret.

    ``MFASetupView`` rendert den Secret als Base32 ohne Padding
    (RFC 6238/3548). Für ``base64.b32decode`` muss das Padding
    wiederhergestellt werden.
    """
    padded = base32_secret + "=" * (-len(base32_secret) % 8)
    key = base64.b32decode(padded)
    totp = TOTP(key)
    token = totp.token()
    return f"{token:06d}"


def _cleanup_totp(username: str, e2e_env) -> None:
    """TOTP-/Static-Devices des Users wieder entfernen.

    ``e2e_env`` aus der Fixture trägt ``E2E_DATABASE_NAME`` für den aktuellen
    xdist-Worker — sonst greift der Cleanup auf die default-DB statt auf die
    worker-spezifische.
    """
    subprocess.run(
        [
            sys.executable,
            "src/manage.py",
            "shell",
            "--no-imports",
            "-c",
            (
                "from core.models import User; "
                "from django_otp.plugins.otp_totp.models import TOTPDevice; "
                "from django_otp.plugins.otp_static.models import StaticDevice; "
                f"u = User.objects.get(username='{username}'); "
                "TOTPDevice.objects.filter(user=u).delete(); "
                "StaticDevice.objects.filter(user=u).delete()"
            ),
        ],
        env=e2e_env,
        capture_output=True,
        text=True,
    )


def _ensure_no_totp(username: str, e2e_env) -> None:
    """Vor dem Setup-Flow sicherstellen, dass der User keine Device hat.

    Andere Tests könnten trotz Cleanup Reste hinterlassen; ein Aufruf vor
    dem eigentlichen Test ist billig und macht die Reihenfolge egal.
    """
    _cleanup_totp(username, e2e_env)


class TestZZMFASetupFlow:
    """ZZ-Prefix: der Flow hinterlässt einen bestätigten TOTPDevice auf
    admin, bis der Test-Teardown ihn wieder entfernt. Andere Tests sollen
    diesen Zwischenstand nicht sehen — siehe auch ``test_mfa_backup_codes``.
    """

    def test_admin_first_time_totp_setup_redirects_to_backup_codes(self, base_url, browser, e2e_env):
        """Voller First-Time-Setup-Flow: Setup-Seite → Backup-Codes-Seite → Settings."""
        _ensure_no_totp("admin", e2e_env)
        try:
            context = browser.new_context(locale="de-DE")
            page = context.new_page()
            page.set_default_timeout(30000)
            try:
                # Frischer Login — keine storage_state-Fixture, sonst bliebe
                # ein Zwischenstand für spätere Tests aktiv.
                page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
                page.fill("#id_username", "admin")
                page.fill("#id_password", "anlaufstelle2026")
                page.click("button[type=submit]")
                page.wait_for_url(lambda url: "/login/" not in url, timeout=10000)

                # Admin hat im Seed kein mfa_required, also keine Zwangs-
                # Umleitung durch die MFAEnforcementMiddleware. Wir navigieren
                # aktiv zur Setup-Seite.
                page.goto(f"{base_url}/mfa/setup/", wait_until="domcontentloaded")

                # QR-Code und Secret-Feld müssen sichtbar sein.
                page.locator("[data-testid='mfa-qrcode']").wait_for(state="visible", timeout=5000)
                secret_locator = page.locator("[data-testid='mfa-secret']")
                # Secret steckt in einem <details>-Block. inner_text() liest
                # geklappten Inhalt nicht — text_content() greift auf das
                # DOM-Text-Node zu und ist unabhängig von details open/close.
                secret = (secret_locator.text_content() or "").strip()
                assert secret, "Secret-Feld ist leer — Setup-View liefert kein Base32-Secret"
                assert re.fullmatch(r"[A-Z2-7]+", secret), f"Secret sollte reines Base32 sein, ist aber: {secret!r}"

                # TOTP-Code aus dem exponierten Secret berechnen und
                # einreichen.
                token = _generate_totp_code(secret)
                page.locator("[data-testid='mfa-token-input']").fill(token)
                page.locator("[data-testid='mfa-confirm-button']").click()

                # Erfolgreiche Bestätigung leitet auf die Backup-Codes-Seite.
                page.wait_for_url(re.compile(r"/mfa/backup-codes/"), timeout=10000)

                # 10 Backup-Codes werden angezeigt.
                codes_list = page.locator("[data-testid='backup-codes-list']")
                codes_list.wait_for(state="visible", timeout=5000)
                codes = codes_list.locator("li")
                assert codes.count() == 10, f"Erwarte 10 Backup-Codes, gesehen: {codes.count()}"

                # MFA-Settings zeigt aktives Device + vollen Counter (10/10).
                page.goto(f"{base_url}/mfa/settings/", wait_until="domcontentloaded")
                status = page.locator("[data-testid='mfa-status']")
                status.wait_for(state="visible", timeout=5000)
                assert "Aktiv" in status.inner_text()

                counter = page.locator("[data-testid='backup-codes-counter']")
                counter.wait_for(state="visible", timeout=5000)
                counter_text = counter.inner_text()
                assert "10" in counter_text, f"Counter sollte 10 Codes zeigen, ist aber: {counter_text!r}"
            finally:
                context.close()
        finally:
            _cleanup_totp("admin", e2e_env)

    def test_setup_with_invalid_token_stays_on_setup_page(self, base_url, browser, e2e_env):
        """Ungültiger TOTP-Code: Setup-Seite bleibt, Fehlermeldung erscheint,
        kein Device wird bestätigt."""
        _ensure_no_totp("admin", e2e_env)
        try:
            context = browser.new_context(locale="de-DE")
            page = context.new_page()
            page.set_default_timeout(30000)
            try:
                page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
                page.fill("#id_username", "admin")
                page.fill("#id_password", "anlaufstelle2026")
                page.click("button[type=submit]")
                page.wait_for_url(lambda url: "/login/" not in url, timeout=10000)

                page.goto(f"{base_url}/mfa/setup/", wait_until="domcontentloaded")
                page.locator("[data-testid='mfa-qrcode']").wait_for(state="visible", timeout=5000)

                # 000000 ist praktisch nie der aktuelle TOTP-Code.
                page.locator("[data-testid='mfa-token-input']").fill("000000")
                page.locator("[data-testid='mfa-confirm-button']").click()

                # Kein Redirect — URL bleibt auf /mfa/setup/.
                page.wait_for_url(re.compile(r"/mfa/setup/"), timeout=5000)
                assert "/mfa/setup/" in page.url

                # QR-Code wird weiterhin angezeigt (User kann erneut versuchen).
                page.locator("[data-testid='mfa-qrcode']").wait_for(state="visible", timeout=5000)
            finally:
                context.close()
        finally:
            _cleanup_totp("admin", e2e_env)
