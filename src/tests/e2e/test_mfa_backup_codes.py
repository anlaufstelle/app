"""E2E-Tests: 2FA Backup-Codes (Refs #588).

Deckt den kompletten Flow ab: Login → Backup-Code-Eingabe statt TOTP →
Session ist als verifiziert markiert, User landet auf der Startseite.
"""

import re
import subprocess
import sys

import pytest

pytestmark = pytest.mark.e2e


def _enable_totp_and_generate_codes(username: str, e2e_env) -> list[str]:
    """Django-Shell-Helper: Confirmed TOTPDevice + frische Backup-Codes anlegen.

    Gibt die Codes zurück, damit der Test sie im UI eingeben kann.

    ``e2e_env`` aus der Fixture trägt ``E2E_DATABASE_NAME`` für den aktuellen
    xdist-Worker — sonst landen TOTPDevice und Backup-Codes in der default-
    DB ``anlaufstelle_e2e`` und kollidieren mit Tests anderer Workers.
    """
    result = subprocess.run(
        [
            sys.executable,
            "src/manage.py",
            "shell",
            "-c",
            (
                "from core.models import User; "
                "from django_otp.plugins.otp_totp.models import TOTPDevice; "
                "from core.services.mfa import generate_backup_codes; "
                f"u = User.objects.get(username='{username}'); "
                "TOTPDevice.objects.filter(user=u).delete(); "
                "TOTPDevice.objects.create(user=u, name='default', confirmed=True); "
                "print('|'.join(generate_backup_codes(u)))"
            ),
        ],
        env=e2e_env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Shell-Setup fehlgeschlagen: {result.stderr}"
    # Letzte Zeile (nach „System check" etc.) enthält die Codes.
    lines = [line for line in result.stdout.splitlines() if "|" in line]
    assert lines, f"Keine Codes ausgegeben: {result.stdout}"
    return lines[-1].split("|")


def _cleanup_totp(username: str, e2e_env) -> None:
    subprocess.run(
        [
            sys.executable,
            "src/manage.py",
            "shell",
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


class TestZZMFABackupCodeLogin:
    """ZZ-Prefix: wir seeden TOTP in die DB und räumen danach wieder auf; andere
    Tests sollen den ungewöhnlichen Zustand nicht sehen."""

    def test_user_can_login_with_backup_code(self, base_url, browser, e2e_env):
        codes = _enable_totp_and_generate_codes("lena", e2e_env)
        try:
            context = browser.new_context(locale="de-DE")
            page = context.new_page()
            try:
                page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
                page.fill("#id_username", "lena")
                page.fill("#id_password", "anlaufstelle2026")
                page.click("button[type=submit]")
                # Redirect zum MFA-Prompt
                page.wait_for_url(re.compile(r"/mfa/verify/"), timeout=8000)

                # Umschalten auf Backup-Code-Eingabe
                page.locator("[data-testid='mfa-toggle-mode']").click()
                backup_input = page.locator("[data-testid='mfa-backup-input']")
                backup_input.wait_for(state="visible", timeout=3000)
                backup_input.fill(codes[0])
                page.locator("[data-testid='mfa-verify-button']").click()

                # Zurück auf /
                page.wait_for_url(f"{base_url}/", timeout=5000)
                assert page.url == f"{base_url}/"
            finally:
                context.close()
        finally:
            _cleanup_totp("lena", e2e_env)

    def test_backup_codes_settings_page_shows_counter_after_login(self, base_url, browser, e2e_env):
        codes = _enable_totp_and_generate_codes("thomas", e2e_env)
        try:
            context = browser.new_context(locale="de-DE")
            page = context.new_page()
            try:
                page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
                page.fill("#id_username", "thomas")
                page.fill("#id_password", "anlaufstelle2026")
                page.click("button[type=submit]")
                page.wait_for_url(re.compile(r"/mfa/verify/"))

                page.locator("[data-testid='mfa-toggle-mode']").click()
                page.locator("[data-testid='mfa-backup-input']").fill(codes[1])
                page.locator("[data-testid='mfa-verify-button']").click()
                page.wait_for_url(f"{base_url}/", timeout=5000)

                # Settings zeigt verbleibende Codes (10 generiert, 1 verbraucht → 9)
                page.goto(f"{base_url}/mfa/settings/")
                counter = page.locator("[data-testid='backup-codes-counter']")
                counter.wait_for(state="visible", timeout=3000)
                assert "9" in counter.inner_text()
            finally:
                context.close()
        finally:
            _cleanup_totp("thomas", e2e_env)


@pytest.mark.smoke
class TestZZMFASettingsBackupSection:
    """MFA-Settings zeigt Backup-Codes-Sektion, wenn ein TOTP-Device existiert."""

    def test_settings_shows_backup_section_when_totp_enabled(self, base_url, browser, e2e_env):
        codes = _enable_totp_and_generate_codes("admin", e2e_env)
        try:
            context = browser.new_context(locale="de-DE")
            page = context.new_page()
            try:
                page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
                page.fill("#id_username", "admin")
                page.fill("#id_password", "anlaufstelle2026")
                page.click("button[type=submit]")
                page.wait_for_url(re.compile(r"/mfa/verify/"))
                page.locator("[data-testid='mfa-toggle-mode']").click()
                page.locator("[data-testid='mfa-backup-input']").fill(codes[0])
                page.locator("[data-testid='mfa-verify-button']").click()
                page.wait_for_url(f"{base_url}/")

                page.goto(f"{base_url}/mfa/settings/")
                assert page.locator("[data-testid='backup-codes-regenerate-toggle']").is_visible()
                assert page.locator("[data-testid='backup-codes-counter']").is_visible()
            finally:
                context.close()
        finally:
            _cleanup_totp("admin", e2e_env)
