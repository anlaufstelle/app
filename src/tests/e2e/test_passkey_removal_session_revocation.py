"""E2E: removing a passkey signs out the account's other sessions (Refs #2640).

Two independent browser contexts share one account (A acts, B is the "other device"):
A removes the passkey under sudo; B is signed out on its next request, A stays
signed in, the passkey is gone and the second factor (authenticator app) remains.
Runs in German and English; the English run also checks that the settings page and
the two flash messages contain no German text.

The account carries a seeded authenticator device and a passkey record while a test
runs; the teardown removes both. Logins and the sudo confirmation use backup codes.
"""

from __future__ import annotations

import re
import uuid
from contextlib import suppress

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell, wait_for_active_service_worker
from .test_mfa_session_revocation import (
    USERNAME,
    _cleanup,
    _confirm_sudo,
    _confirmed_device_count,
    _is_signed_in,
    _is_signed_out,
    _login_with_backup_code,
    _seed_totp_with_codes,
    _shell,
)

pytestmark = pytest.mark.e2e

FLASH = {
    "de": ("Passkey entfernt.", "Andere angemeldete Geräte wurden abgemeldet."),
    "en": ("Passkey removed.", "Other signed-in devices have been signed out."),
}
GERMAN_WORDS = re.compile(
    r"\b(und|oder|nicht|ist|für|mit|der|die|das|bitte|kein|wird|entfernt|abgemeldet|Passkey entfernen)\b"
)


def _seed_passkey(e2e_env) -> str:
    name = f"Key-{uuid.uuid4().hex[:6]}"
    _shell(
        e2e_env,
        "from core.models import User; from django_otp_webauthn.models import WebAuthnCredential; "
        f"u = User.objects.get(username='{USERNAME}'); "
        f"WebAuthnCredential.objects.create(user=u, name='{name}', confirmed=True, "
        f"credential_id=b'{name}'.ljust(32, b'x'), public_key=b'public-key', "
        "aaguid='00000000-0000-0000-0000-000000000000', sign_count=0)",
    )
    return name


def _passkey_count(e2e_env) -> int:
    lines = django_shell(
        e2e_env,
        "from django_otp_webauthn.models import WebAuthnCredential; "
        f"print('N|%d' % WebAuthnCredential.objects.filter(user__username='{USERNAME}').count())",
    )
    return int([line for line in lines if line.startswith("N|")][-1].split("|")[1])


def _set_language(e2e_env, code: str) -> str:
    lines = django_shell(
        e2e_env,
        "from core.models import User; "
        f"u = User.objects.get(username='{USERNAME}'); print('prev=' + u.preferred_language); "
        f"User.objects.filter(pk=u.pk).update(preferred_language='{code}')",
    )
    return next(line for line in lines if line.startswith("prev=")).removeprefix("prev=")


def _settle(page) -> None:
    """Let the page stop navigating on its own before the form is submitted.

    The service worker takes control of a page shortly after it loaded and the page then
    reloads itself. A removal submitted inside that window completes on the server, but the
    browser drops the response, so the new session cookie never arrives and the acting
    session looks signed out.
    """
    wait_for_active_service_worker(page)
    page.wait_for_function("() => !!navigator.serviceWorker.controller", timeout=15000)
    page.wait_for_load_state("load")
    page.wait_for_timeout(2000)


def _new_context(browser, language: str):
    context = browser.new_context(locale="de-DE" if language == "de" else "en-US", timezone_id="Europe/Berlin")
    page = context.new_page()
    page.set_default_timeout(30000)
    return context, page


@pytest.mark.parametrize("language", ["de", "en"])
class TestZZPasskeyRemovalRevokesOtherSessions:
    """ZZ prefix: the account carries seeded devices while the tests run."""

    def test_other_session_is_signed_out_and_own_session_stays(self, base_url, browser, e2e_env, language):
        codes = _seed_totp_with_codes(e2e_env)
        _seed_passkey(e2e_env)
        previous_language = _set_language(e2e_env, language)
        context_a = context_b = None
        try:
            context_a, page_a = _new_context(browser, language)
            context_b, page_b = _new_context(browser, language)
            _login_with_backup_code(page_a, base_url, codes[0])
            _login_with_backup_code(page_b, base_url, codes[1])
            assert _is_signed_in(page_b, base_url), "precondition: B is signed in before the removal"

            page_a.goto(f"{base_url}/mfa/settings/", wait_until="domcontentloaded")
            _settle(page_a)
            delete_button = page_a.locator("[data-testid='passkey-delete-button']")
            expect(delete_button).to_have_count(1)
            delete_button.click()
            # Removal requires a fresh sudo confirmation; the form may be re-submitted afterwards.
            page_a.wait_for_load_state("domcontentloaded")
            if "/sudo/" in page_a.url:
                _confirm_sudo(page_a, codes[2])
                page_a.goto(f"{base_url}/mfa/settings/", wait_until="domcontentloaded")
                _settle(page_a)
                page_a.locator("[data-testid='passkey-delete-button']").click()
            expect(page_a.locator("[data-testid='passkey-empty']")).to_be_visible()
            page_a.wait_for_load_state("load")

            first, second = FLASH[language]
            main_text = " ".join(page_a.locator("#main-content").inner_text().split())
            assert first in main_text and second in main_text, f"flash messages missing: {main_text[:300]}"
            if language == "en":
                residue = GERMAN_WORDS.findall(main_text)
                assert not residue, f"German text on the English settings page: {residue}"

            assert _passkey_count(e2e_env) == 0, "the passkey must be gone"
            assert _confirmed_device_count(e2e_env) == 1, "the authenticator device must remain"
            assert _is_signed_out(page_b, base_url), "B must be signed out on its next request"
            assert _is_signed_in(page_a, base_url), "A must stay signed in after its own removal"
        finally:
            for context in (context_a, context_b):
                if context is not None:
                    with suppress(Exception):
                        context.close()
            _set_language(e2e_env, previous_language)
            _shell(
                e2e_env,
                "from django_otp_webauthn.models import WebAuthnCredential; "
                f"WebAuthnCredential.objects.filter(user__username='{USERNAME}').delete()",
            )
            _cleanup(e2e_env)
