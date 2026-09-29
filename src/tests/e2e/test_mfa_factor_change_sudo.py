"""E2E: ein Faktorwechsel verlangt Sudo, das Erstsetup nicht (Refs #2328).

Nutzerweg im echten Browser:

1. Konto mit bestaetigtem TOTP-Geraet und Backup-Codes (per Shell geseedet),
   Anmeldung mit Passwort und Backup-Code.
2. ``/mfa/setup/`` fuehrt auf die Sudo-Seite (``next`` = ``/mfa/setup/``);
   nach Passwort und einem weiteren Backup-Code geht es fuer ein TOTP-Konto
   auf die Einstellungsseite.
3. Der Faktorwechsel selbst ist fuer ein TOTP-Konto kein Link der Oberflaeche,
   sondern ein ``POST /mfa/setup/``. Der Test schickt ihn als Formular aus der
   Einstellungsseite ab: der erste POST ohne Code zeigt QR-Code und Secret
   eines neuen Geraets, ab dort laeuft die Einrichtung ueber die Oberflaeche
   bis zur Quittung der neuen Backup-Codes. Bewusst ohne Code: ein
   Fehlversuch (etwa ``000000``) sperrt das neue Geraet nach django-otp fuer
   eine Sekunde, der gueltige Code aus dem Browser kaeme zu frueh und wuerde
   als ungueltig abgewiesen.
4. Gegenprobe ohne Sudo: derselbe POST landet auf der Sudo-Seite, es entsteht
   kein neues Geraet.
5. Gegenprobe Erstsetup: ein Konto ohne Faktor sieht die Einrichtung direkt,
   ohne Sudo-Seite.

Kein zweiter Tab: der Sudo-Schritt vergibt die Sitzung neu, jeder Test
arbeitet in genau einem frischen Kontext.
"""

import base64
import re
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

import pytest
from django_otp.oath import TOTP

from ._helpers import SEED_PASSWORD

pytestmark = pytest.mark.e2e

USERNAME = "felix"  # kein Storage-State-Konto der E2E-Fixtures


def _shell(e2e_env, code: str) -> str:
    result = subprocess.run(
        [sys.executable, "src/manage.py", "shell", "--no-imports", "-c", code],
        env=e2e_env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Shell fehlgeschlagen: {result.stderr}"
    return result.stdout


def _seed_totp_with_codes(e2e_env) -> list[str]:
    """Bestaetigtes TOTP-Geraet + frische Backup-Codes; gibt die Codes zurueck."""
    stdout = _shell(
        e2e_env,
        "from core.models import User; "
        "from django_otp.plugins.otp_totp.models import TOTPDevice; "
        "from django_otp.plugins.otp_static.models import StaticDevice; "
        "from core.services.security import generate_backup_codes; "
        f"u = User.objects.get(username='{USERNAME}'); "
        "TOTPDevice.objects.filter(user=u).delete(); "
        "StaticDevice.objects.filter(user=u).delete(); "
        "TOTPDevice.objects.create(user=u, name='bestand', confirmed=True); "
        "print('CODES|' + '|'.join(generate_backup_codes(u)))",
    )
    lines = [line for line in stdout.splitlines() if line.startswith("CODES|")]
    assert lines, f"Keine Codes ausgegeben: {stdout}"
    return lines[-1].split("|")[1:]


def _cleanup(e2e_env) -> None:
    _shell(
        e2e_env,
        "from core.models import User; "
        "from django_otp.plugins.otp_totp.models import TOTPDevice; "
        "from django_otp.plugins.otp_static.models import StaticDevice; "
        f"u = User.objects.get(username='{USERNAME}'); "
        "TOTPDevice.objects.filter(user=u).delete(); "
        "StaticDevice.objects.filter(user=u).delete()",
    )


def _device_state(e2e_env, old_code: str | None = None) -> tuple[int, int, bool]:
    """(bestaetigte TOTP-Geraete, unbestaetigte, alter Backup-Code noch gespeichert)."""
    probe = "False"
    if old_code is not None:
        probe = (
            "StaticToken.objects.filter(device__user=u, device__name='backup', "
            f"token=_hash_code('{old_code}')).exists()"
        )
    stdout = _shell(
        e2e_env,
        "from core.models import User; "
        "from django_otp.plugins.otp_totp.models import TOTPDevice; "
        "from django_otp.plugins.otp_static.models import StaticToken; "
        "from core.services.security.mfa import _hash_code; "
        f"u = User.objects.get(username='{USERNAME}'); "
        "print('STATE|%d|%d|%s' % ("
        "TOTPDevice.objects.filter(user=u, confirmed=True).count(), "
        "TOTPDevice.objects.filter(user=u, confirmed=False).count(), "
        f"{probe}))",
    )
    line = [entry for entry in stdout.splitlines() if entry.startswith("STATE|")][-1]
    _, confirmed, unconfirmed, old_code_stored = line.split("|")
    return int(confirmed), int(unconfirmed), old_code_stored == "True"


def _totp_code(base32_secret: str) -> str:
    padded = base32_secret + "=" * (-len(base32_secret) % 8)
    return f"{TOTP(base64.b32decode(padded)).token():06d}"


def _new_context(browser):
    context = browser.new_context(locale="de-DE", timezone_id="Europe/Berlin")
    page = context.new_page()
    page.set_default_timeout(30000)
    return context, page


def _login(page, base_url, backup_code: str | None = None) -> None:
    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
    page.fill("#id_username", USERNAME)
    page.fill("#id_password", SEED_PASSWORD)
    page.click("button[type=submit]")
    if backup_code is None:
        page.wait_for_url(lambda url: "/login/" not in url, timeout=10000)
        return
    page.wait_for_url(re.compile(r"/mfa/verify/"), timeout=10000)
    page.locator("[data-testid='mfa-toggle-mode']").click()
    backup_input = page.locator("[data-testid='mfa-backup-input']")
    backup_input.wait_for(state="visible", timeout=10000)
    backup_input.fill(backup_code)
    page.locator("[data-testid='mfa-verify-button']").click()
    page.wait_for_url(lambda url: "/mfa/verify/" not in url, timeout=10000)


def _assert_on_sudo_page_for_setup(page) -> None:
    target = urlsplit(page.url)
    assert target.path == "/sudo/", page.url
    assert parse_qs(target.query).get("next") == ["/mfa/setup/"], page.url
    page.locator("input[name='password']").wait_for(state="visible", timeout=5000)


def _confirm_sudo(page, backup_code: str) -> None:
    page.fill("input[name='password']", SEED_PASSWORD)
    page.fill("input[name='otp_token']", backup_code)
    page.locator("form:has(input[name='password']) button[type='submit']").click()
    page.wait_for_url(lambda url: "/sudo/" not in url, timeout=10000)


def _submit_setup_post(page, token: str) -> None:
    """``POST /mfa/setup/`` als Formular aus der aktuellen Seite (mit deren CSRF-Token)."""
    csrf = page.locator("input[name='csrfmiddlewaretoken']").first.get_attribute("value")
    assert csrf, "Seite traegt kein CSRF-Token"
    page.evaluate(
        """([csrf, token]) => {
            const form = document.createElement('form');
            form.method = 'post';
            form.action = '/mfa/setup/';
            for (const [name, value] of [['csrfmiddlewaretoken', csrf], ['token', token]]) {
                const input = document.createElement('input');
                input.type = 'hidden';
                input.name = name;
                input.value = value;
                form.appendChild(input);
            }
            document.body.appendChild(form);
            form.submit();
        }""",
        [csrf, token],
    )


def _finish_setup_from_setup_page(page) -> None:
    """Auf der Setup-Seite: Code aus dem Secret, Backup-Codes quittieren, Einstellungen."""
    page.locator("[data-testid='mfa-qrcode']").wait_for(state="visible", timeout=5000)
    secret = (page.locator("[data-testid='mfa-secret']").text_content() or "").strip()
    assert re.fullmatch(r"[A-Z2-7]+", secret), secret
    page.locator("[data-testid='mfa-token-input']").fill(_totp_code(secret))
    page.locator("[data-testid='mfa-confirm-button']").click()
    page.wait_for_url(re.compile(r"/mfa/backup-codes/"), timeout=10000)
    codes = page.locator("[data-testid='backup-codes-list'] li")
    assert codes.count() == 10
    page.locator("[data-testid='backup-codes-confirm']").check()
    page.locator("[data-testid='backup-codes-continue']").click()
    page.wait_for_url(re.compile(r"/mfa/settings/"), timeout=10000)
    assert "Aktiv" in page.locator("[data-testid='mfa-status']").inner_text()


class TestZZMFAFactorChangeSudo:
    """ZZ-Prefix: das Konto traegt waehrend des Tests geseedete Geraete; der
    Abbau entfernt sie wieder."""

    def test_factor_change_requires_sudo_then_completes_with_new_codes(self, base_url, browser, e2e_env):
        codes = _seed_totp_with_codes(e2e_env)
        try:
            context, page = _new_context(browser)
            try:
                _login(page, base_url, backup_code=codes[0])

                page.goto(f"{base_url}/mfa/setup/", wait_until="domcontentloaded")
                _assert_on_sudo_page_for_setup(page)

                _confirm_sudo(page, codes[1])
                page.wait_for_url(re.compile(r"/mfa/settings/"), timeout=10000)

                # Ohne Code: kein Pruefversuch, also keine Sperre am neuen Geraet.
                _submit_setup_post(page, "")
                page.wait_for_url(re.compile(r"/mfa/setup/"), timeout=10000)
                _finish_setup_from_setup_page(page)
            finally:
                context.close()

            confirmed, unconfirmed, old_code_stored = _device_state(e2e_env, old_code=codes[2])
            assert confirmed == 2
            assert unconfirmed == 0
            assert old_code_stored is False, "Der alte Backup-Code-Satz muss ersetzt sein"
        finally:
            _cleanup(e2e_env)

    def test_factor_change_without_sudo_lands_on_sudo_page(self, base_url, browser, e2e_env):
        codes = _seed_totp_with_codes(e2e_env)
        try:
            context, page = _new_context(browser)
            try:
                _login(page, base_url, backup_code=codes[0])
                page.goto(f"{base_url}/mfa/settings/", wait_until="domcontentloaded")

                _submit_setup_post(page, "000000")
                page.wait_for_url(re.compile(r"/sudo/"), timeout=10000)
                _assert_on_sudo_page_for_setup(page)
                assert page.locator("[data-testid='mfa-qrcode']").count() == 0
            finally:
                context.close()

            confirmed, unconfirmed, old_code_stored = _device_state(e2e_env, old_code=codes[2])
            assert confirmed == 1
            assert unconfirmed == 0
            assert old_code_stored is True
        finally:
            _cleanup(e2e_env)

    def test_first_setup_needs_no_sudo(self, base_url, browser, e2e_env):
        _cleanup(e2e_env)
        try:
            context, page = _new_context(browser)
            try:
                _login(page, base_url)

                page.goto(f"{base_url}/mfa/setup/", wait_until="domcontentloaded")
                assert urlsplit(page.url).path == "/mfa/setup/", page.url
                _finish_setup_from_setup_page(page)
            finally:
                context.close()

            confirmed, unconfirmed, _ = _device_state(e2e_env)
            assert confirmed == 1
            assert unconfirmed == 0
        finally:
            _cleanup(e2e_env)
