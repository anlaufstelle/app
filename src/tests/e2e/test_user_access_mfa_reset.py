"""E2E journey for the second-factor reset by facility administration (Refs #2469).

Covers both entry points:

* ``facility_admin`` (seed user ``admin``) under Verwaltung -> Zugaenge: sudo
  step, confirmation page, reset, audit row, and the next sign-in of the
  target landing on ``/mfa/setup/``. The own account answers 403.
* ``super_admin`` (seed user ``superadmin``) through the user admin action on
  ``/admin-mgmt/core/user/``.

The targets are created per test through ``manage.py shell`` with a confirmed
TOTP device, a passkey and a backup-code set; nothing in the seed is reused, so
the journey leaves the seed accounts untouched.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import uuid
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from ._helpers import SEED_PASSWORD, track_console_errors

pytestmark = pytest.mark.e2e

ADMIN_ACTION = "reset_mfa_for_selected_users"


def _shell(e2e_env, code: str) -> str:
    result = subprocess.run(
        [sys.executable, "src/manage.py", "shell", "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        env=e2e_env,
        check=True,
        timeout=120,
    )
    return result.stdout.strip().splitlines()[-1]


def _create_enrolled_user(e2e_env, username: str, facility_of: str) -> int:
    """Staff account with mandatory 2FA and all three factor kinds; returns its pk."""
    return int(
        _shell(
            e2e_env,
            f"""
            from django_otp_webauthn.models import WebAuthnCredential
            from core.models import EncryptedTOTPDevice, User
            from core.services.security import generate_backup_codes
            anchor = User.objects.get(username={facility_of!r})
            user = User.objects.create_user(
                username={username!r},
                role=User.Role.STAFF,
                facility=anchor.facility,
                mfa_required=True,
            )
            user.set_password({SEED_PASSWORD!r})
            user.save()
            EncryptedTOTPDevice.objects.create(user=user, name="phone", confirmed=True)
            WebAuthnCredential.objects.create(
                user=user,
                name="key",
                confirmed=True,
                credential_id=b"e2e-" + {username!r}.encode(),
                public_key=b"pubkey-bytes",
                aaguid="00000000-0000-0000-0000-000000000000",
                sign_count=0,
            )
            generate_backup_codes(user)
            print(user.pk)
            """,
        )
    )


def _factor_state(e2e_env, user_pk: int, actor_username: str) -> dict:
    return json.loads(
        _shell(
            e2e_env,
            f"""
            import json
            from django_otp.plugins.otp_static.models import StaticDevice
            from django_otp_webauthn.models import WebAuthnCredential
            from core.models import AuditLog, EncryptedTOTPDevice, User
            user = User.objects.get(pk={user_pk})
            actor = User.objects.get(username={actor_username!r})
            rows = AuditLog.objects.filter(user=user, action=AuditLog.Action.MFA_RESET_BY_ADMIN)
            print(json.dumps({{
                "totp": EncryptedTOTPDevice.objects.filter(user=user).count(),
                "passkeys": WebAuthnCredential.objects.filter(user=user).count(),
                "backup_devices": StaticDevice.objects.filter(user=user).count(),
                "audit_rows": rows.count(),
                "audit_by_actor": rows.filter(actor=actor).count(),
                "audit_in_facility": rows.filter(facility=user.facility).count(),
            }}))
            """,
        )
    )


def _close_sudo_window(e2e_env, username: str) -> None:
    """Remove ``sudo_until`` from every server session of ``username``.

    The page fixtures share one cached server session per user; a predecessor
    test may have left its 15-minute sudo window open. The journey asserts the
    sudo step, so the closed window is arranged, not assumed.
    """
    _shell(
        e2e_env,
        f"""
        from django.contrib.sessions.backends.db import SessionStore
        from django.contrib.sessions.models import Session
        from core.models import User
        pk = str(User.objects.get(username={username!r}).pk)
        keys = [
            s.session_key for s in Session.objects.all()
            if str(s.get_decoded().get("_auth_user_id")) == pk and "sudo_until" in s.get_decoded()
        ]
        for key in keys:
            store = SessionStore(session_key=key)
            store.pop("sudo_until", None)
            store.save()
        print(len(keys))
        """,
    )


@pytest.fixture
def closed_admin_sudo_window(e2e_env):
    _close_sudo_window(e2e_env, "admin")
    yield
    _close_sudo_window(e2e_env, "admin")


@pytest.fixture
def closed_superadmin_sudo_window(e2e_env):
    _close_sudo_window(e2e_env, "superadmin")
    yield
    _close_sudo_window(e2e_env, "superadmin")


def test_facility_admin_resets_second_factor_and_next_sign_in_sets_it_up(
    authenticated_page, browser, base_url, e2e_env, closed_admin_sudo_window
):
    page = authenticated_page
    console_errors = track_console_errors(page)
    username = f"e2e_mfa_reset_{uuid.uuid4().hex[:8]}"
    user_pk = _create_enrolled_user(e2e_env, username, facility_of="admin")
    detail_url = f"{base_url}/verwaltung/zugaenge/{user_pk}/"

    # The account page is a management page: the sudo step comes first.
    page.goto(detail_url, wait_until="domcontentloaded")
    page.wait_for_url(lambda url: "/sudo/" in url, timeout=30000, wait_until="domcontentloaded")
    page.fill('input[name="password"]', SEED_PASSWORD)
    page.get_by_role("button", name="Bestätigen und fortfahren").click()
    page.wait_for_url(lambda url: url.rstrip("/").endswith(f"/zugaenge/{user_pk}"), timeout=30000)

    reset_link = page.locator('[data-testid="user-access-mfa-reset"]')
    expect(reset_link).to_be_visible()
    reset_link.click()
    page.wait_for_url(lambda url: url.endswith(f"/zugaenge/{user_pk}/zweiter-faktor/"), timeout=30000)
    confirm = page.locator('[data-testid="user-access-mfa-reset-confirm"]')
    expect(confirm).to_contain_text("Authenticator-Apps, Passkeys und Backup-Codes")
    expect(page.locator("h1")).to_have_text("Zweiten Faktor zurücksetzen")

    # Cancel leaves everything in place.
    page.locator('[data-testid="user-access-mfa-reset-cancel"]').click()
    page.wait_for_url(lambda url: url.rstrip("/").endswith(f"/zugaenge/{user_pk}"), timeout=30000)
    assert _factor_state(e2e_env, user_pk, "admin")["totp"] == 1

    page.locator('[data-testid="user-access-mfa-reset"]').click()
    page.wait_for_url(lambda url: url.endswith("/zweiter-faktor/"), timeout=30000)
    page.locator('[data-testid="user-access-mfa-reset-submit"]').click()
    page.wait_for_url(lambda url: url.rstrip("/").endswith(f"/zugaenge/{user_pk}"), timeout=30000)
    expect(
        page.get_by_text("Der zweite Faktor wurde zurückgesetzt und alle Sitzungen des Zugangs wurden beendet.")
    ).to_be_visible()
    expect(page.locator('[data-testid="user-access-mfa-reset"]')).to_have_count(0)

    state = _factor_state(e2e_env, user_pk, "admin")
    assert state == {
        "totp": 0,
        "passkeys": 0,
        "backup_devices": 0,
        "audit_rows": 1,
        "audit_by_actor": 1,
        "audit_in_facility": 1,
    }

    # Checked before the deliberate 403 below: the browser logs that navigation as a console error.
    assert not console_errors, console_errors

    # The own account is not resettable through this path.
    own_pk = _shell(e2e_env, "from core.models import User; print(User.objects.get(username='admin').pk)")
    own_response = page.goto(f"{base_url}/verwaltung/zugaenge/{own_pk}/zweiter-faktor/")
    assert own_response is not None and own_response.status == 403

    # Next sign-in of the target: mandatory 2FA, no factor -> setup.
    target_context = browser.new_context(locale="de-DE", timezone_id="Europe/Berlin")
    try:
        target_page = target_context.new_page()
        target_page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
        target_page.fill('input[name="username"]', username)
        target_page.fill('input[name="password"]', SEED_PASSWORD)
        target_page.get_by_role("button", name="Anmelden").click()
        target_page.wait_for_url(lambda url: "/mfa/setup/" in url, timeout=30000, wait_until="domcontentloaded")
    finally:
        target_context.close()


def test_super_admin_resets_second_factor_through_admin_action(
    super_admin_page, base_url, e2e_env, closed_superadmin_sudo_window
):
    page = super_admin_page
    username = f"e2e_mfa_reset_sa_{uuid.uuid4().hex[:8]}"
    user_pk = _create_enrolled_user(e2e_env, username, facility_of="admin")

    page.goto(f"{base_url}/admin-mgmt/core/user/?q={username}", wait_until="domcontentloaded")
    page.locator("input[name='password']").wait_for(timeout=30000)
    page.fill('input[name="password"]', SEED_PASSWORD)
    page.get_by_role("button", name="Bestätigen und fortfahren").click()
    # The intermediate sudo and admin-login URLs carry the target in ``next``;
    # only the path tells that the changelist itself is reached.
    page.wait_for_url(
        lambda url: urlsplit(url).path == "/admin-mgmt/core/user/", timeout=30000, wait_until="domcontentloaded"
    )

    page.locator(f'input[name="_selected_action"][value="{user_pk}"]').check()
    page.select_option('select[name="action"]', ADMIN_ACTION)
    page.locator('button[name="index"]').click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.get_by_text(f"{username}: Zweiter Faktor zurückgesetzt, alle Sitzungen beendet.")).to_be_visible()

    state = _factor_state(e2e_env, user_pk, "superadmin")
    assert state == {
        "totp": 0,
        "passkeys": 0,
        "backup_devices": 0,
        "audit_rows": 1,
        "audit_by_actor": 1,
        "audit_in_facility": 1,
    }
