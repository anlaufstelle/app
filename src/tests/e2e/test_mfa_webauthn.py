"""E2E: Passkey/WebAuthn als zweiter Faktor (ADR-032, Refs #1492).

Voller Round-Trip gegen einen Chrome-CDP-Virtual-Authenticator:
TOTP einrichten (Passkey ist nur NEBEN TOTP möglich) → Passkey registrieren →
abmelden → mit Passkey verifizieren → Dashboard.

Wichtig zur Origin/RP-ID: Der E2E-Server lauscht auf ``127.0.0.1:<port>``, die
Relying-Party-ID der e2e-Settings ist aber ``localhost`` (Browser behandeln
localhost als secure context). WebAuthn verlangt, dass die RP-ID ein
registrierbarer Suffix der Seiten-Origin ist — daher fährt dieser Test bewusst
über ``http://localhost:<port>`` (löst auf denselben Server auf), passend zu
``OTP_WEBAUTHN_ALLOWED_ORIGINS``.

Destruktiv für den admin-Seed-User (bekommt TOTP + Passkey); der
``finally``-Block räumt beide wieder ab, damit nachgelagerte Tests den
admin-User unverändert sehen. ZZ-Prefix hält den Zwischenstand aus anderen Tests.
"""

import base64
import re
import subprocess
import sys

import pytest
from django_otp.oath import TOTP

# ``TargetClosedError`` ist in dieser Playwright-Version (1.61) noch nicht über
# ``playwright.sync_api`` exportiert, die Basisklasse ``Error`` schon. Wir
# importieren die konkrete Klasse gezielt aus dem Impl-Modul, um im Roundtrip
# AUSSCHLIESSLICH den Browser-Tod abzufangen und NICHT etwa ``TimeoutError``
# (ebenfalls ``Error``-Subklasse), der seine aussagekräftige Meldung behalten
# soll. Refs #1606.
from playwright._impl._errors import TargetClosedError

from ._helpers import SEED_PASSWORD

pytestmark = pytest.mark.e2e


def _generate_totp_code(base32_secret: str) -> str:
    padded = base32_secret + "=" * (-len(base32_secret) % 8)
    key = base64.b32decode(padded)
    return f"{TOTP(key).token():06d}"


def _cleanup(username: str, e2e_env) -> None:
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
                "from django_otp_webauthn.models import WebAuthnCredential; "
                f"u = User.objects.get(username='{username}'); "
                "TOTPDevice.objects.filter(user=u).delete(); "
                "StaticDevice.objects.filter(user=u).delete(); "
                "WebAuthnCredential.objects.filter(user=u).delete()"
            ),
        ],
        env=e2e_env,
        capture_output=True,
        text=True,
    )


def _localhost_base(base_url: str) -> str:
    # RP-ID ist ``localhost`` — Origin muss dazu passen (nicht 127.0.0.1).
    return base_url.replace("127.0.0.1", "localhost")


def _login(page, base, username="admin", password=SEED_PASSWORD):
    page.goto(f"{base}/login/", wait_until="domcontentloaded")
    page.fill("#id_username", username)
    page.fill("#id_password", password)
    page.click("button[type=submit]")
    page.wait_for_url(lambda url: "/login/" not in url, timeout=15000)


def _setup_totp(page, base):
    """Vollständiger TOTP-Setup inkl. Backup-Code-Quittung → bestätigtes Device."""
    page.goto(f"{base}/mfa/setup/", wait_until="domcontentloaded")
    page.locator("[data-testid='mfa-qrcode']").wait_for(state="visible", timeout=5000)
    secret = (page.locator("[data-testid='mfa-secret']").text_content() or "").strip()
    page.locator("[data-testid='mfa-token-input']").fill(_generate_totp_code(secret))
    page.locator("[data-testid='mfa-confirm-button']").click()
    page.wait_for_url(re.compile(r"/mfa/backup-codes/"), timeout=10000)
    page.locator("[data-testid='backup-codes-confirm']").check()
    page.locator("[data-testid='backup-codes-continue']").click()
    page.wait_for_url(re.compile(r"/mfa/settings/"), timeout=10000)


class TestZZMfaWebAuthn:
    def test_register_and_verify_passkey_roundtrip(self, base_url, browser_type, browser_type_launch_args, e2e_env):
        base = _localhost_base(base_url)
        _cleanup("admin", e2e_env)

        # Eigener Browser statt des session-weiten ``browser``-Fixtures (Refs #1606):
        # Der Virtual-Authenticator-Pfad (CDP/``WebAuthn.addVirtualAuthenticator``)
        # hängt stärker als üblich an den Browser-Internals. Stirbt Chromium unter
        # CPU-Knappheit weg (``-n 2`` auf 2 Kernen), darf das NICHT den geteilten
        # Browser der übrigen Tests auf diesem xdist-Worker beschädigen. Ein
        # dedizierter, im ``finally`` geschlossener Browser kapselt den Ausfall.
        browser = browser_type.launch(**browser_type_launch_args)
        console_msgs: list[str] = []
        try:
            context = browser.new_context(locale="de-DE")
            try:
                page = context.new_page()
                page.set_default_timeout(30000)
                page.on("console", lambda m: console_msgs.append(f"{m.type}: {m.text}"))

                # Virtual Authenticator (CTAP2, intern, user-verified) an den Context.
                # Resident-Key + User-Verification sind für Passkey-als-2FA zwingend
                # (discoverable credential, UV im Verify-Schritt) — daher nicht
                # weiter abspeckbar. ``automaticPresenceSimulation`` ist Default.
                cdp = context.new_cdp_session(page)
                cdp.send("WebAuthn.enable")
                cdp.send(
                    "WebAuthn.addVirtualAuthenticator",
                    {
                        "options": {
                            "protocol": "ctap2",
                            "transport": "internal",
                            "hasResidentKey": True,
                            "hasUserVerification": True,
                            "isUserVerified": True,
                            "automaticPresenceSimulation": True,
                        }
                    },
                )

                _login(page, base)
                _setup_totp(page, base)

                # --- Passkey registrieren ---
                page.goto(f"{base}/mfa/settings/", wait_until="domcontentloaded")
                register_btn = page.locator("[data-testid='passkey-register-button']")
                register_btn.wait_for(state="visible", timeout=5000)
                with page.expect_response(re.compile(r"/webauthn/registration/complete/")) as resp_info:
                    register_btn.click()
                complete = resp_info.value
                assert complete.status == 200, (
                    f"Passkey-Registrierung fehlgeschlagen: {complete.status} {complete.text()}\n"
                    f"Console: {console_msgs}"
                )

                # Nach frischem Laden muss der Passkey in der Liste stehen.
                page.goto(f"{base}/mfa/settings/", wait_until="domcontentloaded")
                assert page.locator("[data-testid='passkey-list'] li").count() == 1

                # --- Abmelden (Session verwerfen; der Virtual Authenticator
                #     bleibt am Context erhalten und behält den Passkey) ---
                context.clear_cookies()

                # --- Erneut anmelden → Verify-Seite mit Chooser ---
                _login(page, base)
                page.wait_for_url(re.compile(r"/mfa/verify/"), timeout=10000)
                passkey_btn = page.locator("[data-testid='mfa-passkey-button']")
                passkey_btn.wait_for(state="visible", timeout=5000)
                passkey_btn.click()

                # Erfolgreiche Assertion setzt mfa_verified und leitet weiter.
                page.wait_for_url(lambda url: "/mfa/verify/" not in url and "/login/" not in url, timeout=15000)
                assert "/mfa/" not in page.url
            except TargetClosedError as exc:
                # Stirbt der Browser unter Ressourcenknappheit weg, wirft die
                # Playwright-Sync-API ``TargetClosedError``. Hier als sauberen
                # Fehlschlag quittieren, statt ihn eine Assertion verschleiern
                # zu lassen. Den Endlos-Dreher der Sync-Greenlets (der Fall, in
                # dem KEINE Exception fliegt) fängt zusätzlich der harte
                # ``--timeout-method=thread`` der E2E-Läufe ab. Refs #1606.
                pytest.fail(
                    "Passkey-Roundtrip abgebrochen: Browser/Target wurde geschlossen "
                    f"({exc}). Unter CPU-Knappheit stirbt Chromium weg; dieser saubere "
                    f"Fehlschlag ersetzt den früheren Endlos-Dreher.\nConsole: {console_msgs}"
                )
            finally:
                context.close()
        finally:
            browser.close()
            _cleanup("admin", e2e_env)
