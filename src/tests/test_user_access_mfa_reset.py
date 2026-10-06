"""Second-factor reset by facility administration (Refs #2469).

A person who lost their authenticator and used up their backup codes had no
way back in: ``MFADisableView`` only removes the factors of the signed-in
account and is closed while 2FA is mandatory, and no TOTP, static or WebAuthn
model is registered on ``/admin-mgmt/``.

Contract:

* ``facility_admin`` resets accounts of the own facility under
  Verwaltung -> Zugaenge; ``super_admin`` resets any account through the user
  admin action. Every other role is refused, so are foreign-facility accounts,
  ``super_admin`` accounts for a ``facility_admin`` and the own account.
* Both entry points sit behind sudo (view mixin, admin site gate).
* The reset removes every TOTP device, passkey and backup code of the target,
  ends the target's sessions and writes exactly one ``MFA_RESET_BY_ADMIN`` row
  (target, actor, target facility; counters only, no secrets).
* The existing enforcement switch then sends the next sign-in of a person with
  mandatory 2FA to ``/mfa/setup/``.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import urlsplit

import pytest
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import Client, override_settings
from django.urls import reverse
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken

from core.models import AuditLog, User
from core.models import EncryptedTOTPDevice as TOTPDevice
from core.services.security import generate_backup_codes
from core.services.user_access import has_second_factor, reset_managed_user_mfa, reset_user_mfa
from tests.test_rls_functional import (  # noqa: F401
    as_rls_role,
    rls_test_role,
)

pytestmark = pytest.mark.django_db

USER_ADMIN_CHANGELIST = "/admin-mgmt/core/user/"
CREDENTIALS_LOGGER = "core.services.security.credentials"
# The admin action runs through the real changelist, including the sudo gate of
# the admin site. unfold's ``ModelAdmin.get_action_choices`` still lacks the
# ``action_location`` parameter, which Django 6.1 reports as a deprecation on
# every changelist render; ``filterwarnings = error`` would turn that into a
# 500. Only this one message is ignored, and only for the changelist tests.
IGNORE_UNFOLD_ACTION_CHOICES = pytest.mark.filterwarnings(
    "ignore:Overriding get_action_choices\\(\\) without the 'action_location' parameter is deprecated"
)
ADMIN_ACTION = "reset_mfa_for_selected_users"
DETAIL_KEYS = {
    "username",
    "actor_id",
    "actor_username",
    "totp_devices_removed",
    "passkeys_removed",
    "backup_codes_removed",
}


def _credential_log_lines(caplog) -> list[tuple[int, str]]:
    return [(record.levelno, record.getMessage()) for record in caplog.records if record.name == CREDENTIALS_LOGGER]


def _reset_url(user) -> str:
    return reverse("core:user_access_mfa_reset", args=[user.pk])


def _passkey(user, credential_id: bytes):
    from django_otp_webauthn.models import WebAuthnCredential

    return WebAuthnCredential.objects.create(
        user=user,
        name="Schlüssel",
        confirmed=True,
        credential_id=credential_id,
        public_key=b"pubkey-bytes",
        aaguid="00000000-0000-0000-0000-000000000000",
        sign_count=0,
    )


def _enroll(user, *, passkey: bool = True) -> list[str]:
    """Confirmed TOTP device, a leftover unconfirmed one, a passkey and backup codes."""
    TOTPDevice.objects.create(user=user, name="phone", confirmed=True)
    TOTPDevice.objects.create(user=user, name="pending", confirmed=False)
    if passkey:
        _passkey(user, credential_id=f"cred-{user.pk}".encode())
    return generate_backup_codes(user)


def _factor_rows(user) -> tuple[int, int, int, int]:
    from django_otp_webauthn.models import WebAuthnCredential

    return (
        TOTPDevice.objects.filter(user=user).count(),
        WebAuthnCredential.objects.filter(user=user).count(),
        StaticDevice.objects.filter(user=user).count(),
        StaticToken.objects.filter(device__user=user).count(),
    )


def _reset_rows(user):
    return AuditLog.objects.filter(user=user, action=AuditLog.Action.MFA_RESET_BY_ADMIN)


def _verified_client(user) -> Client:
    browser = Client()
    browser.force_login(user)
    session = browser.session
    session["mfa_verified"] = True
    session.save()
    return browser


def _in_sudo(browser) -> None:
    session = browser.session
    session["sudo_until"] = int(time.time()) + 600
    session.save()


def _admin_action(browser, *users):
    return browser.post(
        USER_ADMIN_CHANGELIST,
        {"action": ADMIN_ACTION, "_selected_action": [str(user.pk) for user in users]},
    )


class TestResetService:
    def test_removes_every_factor_of_the_target_only(self, admin_user, staff_user, lead_user):
        _enroll(staff_user)
        _enroll(lead_user)

        result = reset_user_mfa(staff_user, actor=admin_user)

        assert (result.totp_devices, result.passkeys, result.backup_codes) == (2, 1, 10)
        assert _factor_rows(staff_user) == (0, 0, 0, 0)
        assert _factor_rows(lead_user) == (2, 1, 1, 10)
        assert not has_second_factor(staff_user)
        assert has_second_factor(lead_user)

    def test_writes_one_attributed_audit_row_without_secrets(self, admin_user, staff_user):
        codes = _enroll(staff_user)
        secrets = [device.key for device in TOTPDevice.objects.filter(user=staff_user)] + codes

        reset_user_mfa(staff_user, actor=admin_user, ip_address="203.0.113.7")

        entry = _reset_rows(staff_user).get()
        assert entry.actor_id == admin_user.pk
        assert entry.facility_id == staff_user.facility_id
        assert entry.target_type == "User"
        assert entry.target_id == str(staff_user.pk)
        assert entry.ip_address == "203.0.113.7"
        assert set(entry.detail) == DETAIL_KEYS
        assert entry.detail["username"] == staff_user.username
        assert entry.detail["actor_id"] == admin_user.pk
        assert entry.detail["actor_username"] == admin_user.username
        assert entry.detail["totp_devices_removed"] == 2
        assert entry.detail["passkeys_removed"] == 1
        assert entry.detail["backup_codes_removed"] == 10
        rendered = str(entry.detail)
        for secret in secrets:
            assert secret not in rendered

    def test_account_without_factor_is_left_alone_and_not_audited(self, admin_user, staff_user):
        result = reset_user_mfa(staff_user, actor=admin_user)

        assert not result.removed_anything
        assert not _reset_rows(staff_user).exists()

    def test_backup_codes_alone_count_as_a_factor(self, admin_user, staff_user):
        generate_backup_codes(staff_user)
        assert has_second_factor(staff_user)

        result = reset_user_mfa(staff_user, actor=admin_user)

        assert result.backup_codes == 10
        assert _factor_rows(staff_user) == (0, 0, 0, 0)
        assert _reset_rows(staff_user).count() == 1

    def test_ends_the_sessions_of_the_target_but_not_of_the_actor(self, admin_user, staff_user):
        _enroll(staff_user)
        target_browser = _verified_client(staff_user)
        actor_browser = _verified_client(admin_user)

        reset_user_mfa(staff_user, actor=admin_user)

        response = target_browser.get("/")
        assert response.status_code == 302
        assert urlsplit(response.url).path == "/login/"
        assert actor_browser.get(reverse("core:user_access_list")).status_code == 200

    def test_session_flush_log_names_no_salt_rotation(self, admin_user, staff_user, caplog):
        """The reset rotates no offline salt; its flush log line must not claim one."""
        _enroll(staff_user)
        _verified_client(staff_user)

        with caplog.at_level(logging.INFO, logger=CREDENTIALS_LOGGER):
            reset_user_mfa(staff_user, actor=admin_user)

        assert _credential_log_lines(caplog) == [
            (logging.INFO, f"Session flush without salt rotation: 1 session(s) of user {staff_user.pk} deleted.")
        ]

    def test_session_scan_cap_log_names_no_salt_rotation(self, admin_user, staff_user, settings, caplog):
        settings.OFFLINE_REVOCATION_SESSION_SCAN_LIMIT = 0
        _enroll(staff_user)
        _verified_client(staff_user)

        with caplog.at_level(logging.INFO, logger=CREDENTIALS_LOGGER):
            reset_user_mfa(staff_user, actor=admin_user)

        assert _credential_log_lines(caplog) == [
            (
                logging.ERROR,
                f"Session flush without salt rotation: session scan cap (0) reached for user {staff_user.pk} - "
                "remaining sessions not flushed (they expire on inactivity, at the latest at the absolute "
                "session maximum, Refs #1858/#1868).",
            )
        ]

    def test_own_account_is_refused(self, admin_user, super_admin_user):
        _enroll(admin_user)
        _enroll(super_admin_user, passkey=False)

        with pytest.raises(PermissionDenied):
            reset_user_mfa(admin_user, actor=admin_user)
        with pytest.raises(PermissionDenied):
            reset_user_mfa(super_admin_user, actor=super_admin_user)

        assert _factor_rows(admin_user) == (2, 1, 1, 10)
        assert _factor_rows(super_admin_user) == (2, 0, 1, 10)
        assert not AuditLog.objects.filter(action=AuditLog.Action.MFA_RESET_BY_ADMIN).exists()

    @pytest.mark.parametrize("actor_fixture", ["lead_user", "staff_user", "assistant_user"])
    def test_roles_below_facility_admin_are_refused(self, request, actor_fixture, admin_user):
        _enroll(admin_user)
        actor = request.getfixturevalue(actor_fixture)

        with pytest.raises(PermissionDenied):
            reset_user_mfa(admin_user, actor=actor)

        assert _factor_rows(admin_user) == (2, 1, 1, 10)

    def test_facility_admin_cannot_reach_foreign_facility_or_super_admin(
        self, facility, admin_user, foreign_staff, super_admin_user
    ):
        _enroll(foreign_staff)
        _enroll(super_admin_user, passkey=False)
        User.objects.filter(pk=super_admin_user.pk).update(facility=facility)
        super_admin_user.refresh_from_db()

        with pytest.raises(PermissionDenied):
            reset_user_mfa(foreign_staff, actor=admin_user)
        with pytest.raises(PermissionDenied):
            reset_user_mfa(super_admin_user, actor=admin_user)

        assert _factor_rows(foreign_staff) == (2, 1, 1, 10)
        assert _factor_rows(super_admin_user) == (2, 0, 1, 10)

    def test_facility_admin_without_facility_is_refused(self, admin_user, staff_user):
        _enroll(staff_user)
        User.objects.filter(pk=admin_user.pk).update(facility=None)
        admin_user.refresh_from_db()
        User.objects.filter(pk=staff_user.pk).update(facility=None)
        staff_user.refresh_from_db()

        with pytest.raises(PermissionDenied):
            reset_user_mfa(staff_user, actor=admin_user)

        assert _factor_rows(staff_user) == (2, 1, 1, 10)

    def test_inactive_or_anonymous_actor_is_refused(self, admin_user, staff_user):
        from django.contrib.auth.models import AnonymousUser

        _enroll(staff_user)
        User.objects.filter(pk=admin_user.pk).update(is_active=False)
        admin_user.refresh_from_db()

        with pytest.raises(PermissionDenied):
            reset_user_mfa(staff_user, actor=admin_user)
        with pytest.raises(PermissionDenied):
            reset_user_mfa(staff_user, actor=AnonymousUser())

        assert _factor_rows(staff_user) == (2, 1, 1, 10)

    def test_super_admin_resets_accounts_of_any_facility_and_other_super_admins(
        self, db, super_admin_user, foreign_staff
    ):
        other_super_admin = User.objects.create_user(username="second_super_admin", role=User.Role.SUPER_ADMIN)
        _enroll(foreign_staff)
        _enroll(other_super_admin, passkey=False)

        reset_user_mfa(foreign_staff, actor=super_admin_user)
        reset_user_mfa(other_super_admin, actor=super_admin_user)

        assert _factor_rows(foreign_staff) == (0, 0, 0, 0)
        assert _factor_rows(other_super_admin) == (0, 0, 0, 0)
        assert _reset_rows(foreign_staff).get().facility_id == foreign_staff.facility_id
        assert _reset_rows(other_super_admin).get().facility_id is None

    def test_account_moved_after_lookup_is_not_reset(self, admin_user, staff_user, second_facility):
        _enroll(staff_user)
        # ``staff_user`` still carries the facility the caller looked up; the
        # row has moved on in the meantime.
        User.objects.filter(pk=staff_user.pk).update(facility=second_facility)

        with pytest.raises(PermissionDenied):
            reset_user_mfa(staff_user, actor=admin_user)

        assert _factor_rows(staff_user) == (2, 1, 1, 10)
        assert not _reset_rows(staff_user).exists()

    def test_role_change_after_lookup_is_rechecked_on_the_locked_row(self, admin_user, staff_user):
        _enroll(staff_user)
        User.objects.filter(pk=staff_user.pk).update(role=User.Role.SUPER_ADMIN)

        with pytest.raises(PermissionDenied):
            reset_user_mfa(staff_user, actor=admin_user)

        assert _factor_rows(staff_user) == (2, 1, 1, 10)

    def test_managed_variant_scopes_to_the_given_facility(self, facility, admin_user, staff_user, foreign_staff):
        _enroll(staff_user)
        _enroll(foreign_staff)

        with pytest.raises(Http404):
            reset_managed_user_mfa(foreign_staff, facility=facility, actor=admin_user)
        result = reset_managed_user_mfa(staff_user, facility=facility, actor=admin_user)

        assert result.removed_anything
        assert _factor_rows(foreign_staff) == (2, 1, 1, 10)

    def test_managed_variant_requires_the_actor_facility(self, second_facility, admin_user, foreign_staff):
        _enroll(foreign_staff)

        with pytest.raises(PermissionDenied):
            reset_managed_user_mfa(foreign_staff, facility=second_facility, actor=admin_user)

        assert _factor_rows(foreign_staff) == (2, 1, 1, 10)


class TestResetViewAccess:
    def test_get_shows_confirmation_without_changing_anything(self, client, admin_user, staff_user):
        _enroll(staff_user)
        client.force_login(admin_user)

        response = client.get(_reset_url(staff_user))

        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        content = response.content.decode()
        assert 'data-testid="user-access-mfa-reset-confirm"' in content
        assert f'action="{_reset_url(staff_user)}"' in content
        assert _factor_rows(staff_user) == (2, 1, 1, 10)
        assert not _reset_rows(staff_user).exists()

    def test_post_resets_and_returns_to_the_account(self, client, admin_user, staff_user):
        _enroll(staff_user)
        client.force_login(admin_user)

        response = client.post(_reset_url(staff_user))

        assert response.status_code == 302
        assert response.url == reverse("core:user_access_update", args=[staff_user.pk])
        assert _factor_rows(staff_user) == (0, 0, 0, 0)
        entry = _reset_rows(staff_user).get()
        assert entry.actor_id == admin_user.pk
        assert entry.facility_id == staff_user.facility_id

    def test_post_without_factor_writes_nothing(self, client, admin_user, staff_user):
        client.force_login(admin_user)

        response = client.post(_reset_url(staff_user))

        assert response.status_code == 302
        assert not _reset_rows(staff_user).exists()

    @override_settings(SUDO_MODE_ENABLED=True)
    def test_without_sudo_both_methods_go_to_sudo_and_nothing_changes(self, admin_user, staff_user):
        _enroll(staff_user)
        browser = _verified_client(admin_user)

        for method in ("get", "post"):
            response = getattr(browser, method)(_reset_url(staff_user))
            assert response.status_code == 302
            assert urlsplit(response.url).path == reverse("sudo_mode")

        assert _factor_rows(staff_user) == (2, 1, 1, 10)
        assert not _reset_rows(staff_user).exists()

    @override_settings(SUDO_MODE_ENABLED=True)
    def test_with_sudo_the_reset_goes_through(self, admin_user, staff_user):
        _enroll(staff_user)
        browser = _verified_client(admin_user)
        _in_sudo(browser)

        response = browser.post(_reset_url(staff_user))

        assert response.status_code == 302
        assert _factor_rows(staff_user) == (0, 0, 0, 0)
        assert _reset_rows(staff_user).count() == 1

    def test_foreign_facility_account_is_404(self, client, admin_user, foreign_staff):
        _enroll(foreign_staff)
        client.force_login(admin_user)

        assert client.get(_reset_url(foreign_staff)).status_code == 404
        assert client.post(_reset_url(foreign_staff)).status_code == 404
        assert _factor_rows(foreign_staff) == (2, 1, 1, 10)
        assert not _reset_rows(foreign_staff).exists()

    def test_super_admin_account_in_own_facility_is_404(self, client, facility, admin_user, super_admin_user):
        _enroll(super_admin_user, passkey=False)
        User.objects.filter(pk=super_admin_user.pk).update(facility=facility)
        client.force_login(admin_user)

        assert client.post(_reset_url(super_admin_user)).status_code == 404
        assert _factor_rows(super_admin_user) == (2, 0, 1, 10)

    @pytest.mark.parametrize("actor_fixture", ["lead_user", "staff_user", "assistant_user", "super_admin_user"])
    def test_other_roles_are_forbidden(self, client, request, actor_fixture, admin_user):
        _enroll(admin_user)
        client.force_login(request.getfixturevalue(actor_fixture))

        assert client.get(_reset_url(admin_user)).status_code == 403
        assert client.post(_reset_url(admin_user)).status_code == 403
        assert _factor_rows(admin_user) == (2, 1, 1, 10)

    def test_own_account_is_forbidden(self, admin_user):
        _enroll(admin_user)
        browser = _verified_client(admin_user)

        assert browser.get(_reset_url(admin_user)).status_code == 403
        assert browser.post(_reset_url(admin_user)).status_code == 403
        assert _factor_rows(admin_user) == (2, 1, 1, 10)

    def test_anonymous_goes_to_login(self, client, staff_user):
        _enroll(staff_user)

        response = client.post(_reset_url(staff_user))

        assert response.status_code == 302
        assert urlsplit(response.url).path == "/login/"
        assert _factor_rows(staff_user) == (2, 1, 1, 10)


class TestResetButton:
    def _update_page(self, client, actor, target) -> str:
        browser = _verified_client(actor)
        response = browser.get(reverse("core:user_access_update", args=[target.pk]))
        assert response.status_code == 200
        return response.content.decode()

    def test_button_links_to_confirmation_when_a_factor_exists(self, client, admin_user, staff_user):
        _enroll(staff_user)

        content = self._update_page(client, admin_user, staff_user)

        assert 'data-testid="user-access-mfa-reset"' in content
        assert f'href="{_reset_url(staff_user)}"' in content

    def test_no_button_without_factor(self, client, admin_user, staff_user):
        content = self._update_page(client, admin_user, staff_user)

        assert 'data-testid="user-access-mfa-reset"' not in content

    def test_no_button_on_own_account(self, client, admin_user):
        _enroll(admin_user)

        content = self._update_page(client, admin_user, admin_user)

        assert 'data-testid="user-access-mfa-reset"' not in content


class TestNextSignIn:
    @override_settings(MFA_ENFORCE_PRIVILEGED_ROLES=True)
    def test_mandatory_2fa_sends_the_next_sign_in_to_setup(self, client, admin_user, staff_user):
        _enroll(staff_user)
        User.objects.filter(pk=staff_user.pk).update(mfa_required=True)
        before = Client()
        before.login(username="teststaff", password="testpass123")
        assert before.get("/").url.startswith("/mfa/verify/")

        reset_user_mfa(staff_user, actor=admin_user)

        after = Client()
        assert after.login(username="teststaff", password="testpass123")
        response = after.get("/")
        assert response.status_code == 302
        assert response.url == "/mfa/setup/"


@IGNORE_UNFOLD_ACTION_CHOICES
class TestAdminAction:
    def test_action_is_registered_on_the_user_admin(self):
        from core.admin import UserAdmin
        from core.admin.users import reset_mfa_for_selected_users

        assert reset_mfa_for_selected_users in UserAdmin.actions

    def test_super_admin_resets_selected_accounts_across_facilities(self, super_admin_user, staff_user, foreign_staff):
        _enroll(staff_user)
        _enroll(foreign_staff)
        browser = _verified_client(super_admin_user)

        response = _admin_action(browser, staff_user, foreign_staff)

        assert response.status_code == 302
        assert _factor_rows(staff_user) == (0, 0, 0, 0)
        assert _factor_rows(foreign_staff) == (0, 0, 0, 0)
        assert _reset_rows(staff_user).get().actor_id == super_admin_user.pk
        assert _reset_rows(foreign_staff).get().facility_id == foreign_staff.facility_id

    def test_account_without_factor_is_reported_and_not_audited(self, super_admin_user, staff_user):
        browser = _verified_client(super_admin_user)

        response = _admin_action(browser, staff_user)

        assert response.status_code == 302
        shown = [str(message) for message in browser.get(response.url).context["messages"]]
        assert f"{staff_user.username}: Kein zweiter Faktor eingerichtet." in shown
        assert not _reset_rows(staff_user).exists()

    def test_own_account_is_skipped(self, super_admin_user, staff_user):
        _enroll(super_admin_user, passkey=False)
        _enroll(staff_user)
        browser = _verified_client(super_admin_user)

        _admin_action(browser, super_admin_user, staff_user)

        assert _factor_rows(super_admin_user) == (2, 0, 1, 10)
        assert _factor_rows(staff_user) == (0, 0, 0, 0)
        assert not _reset_rows(super_admin_user).exists()

    def test_facility_admin_reaches_neither_foreign_facility_nor_super_admin(
        self, facility, admin_user, staff_user, foreign_staff, super_admin_user
    ):
        _enroll(staff_user)
        _enroll(foreign_staff)
        _enroll(super_admin_user, passkey=False)
        User.objects.filter(pk=super_admin_user.pk).update(facility=facility)
        browser = _verified_client(admin_user)

        _admin_action(browser, staff_user, foreign_staff, super_admin_user)

        assert _factor_rows(staff_user) == (0, 0, 0, 0)
        assert _factor_rows(foreign_staff) == (2, 1, 1, 10)
        assert _factor_rows(super_admin_user) == (2, 0, 1, 10)
        assert _reset_rows(staff_user).count() == 1
        assert not _reset_rows(foreign_staff).exists()
        assert not _reset_rows(super_admin_user).exists()

    @override_settings(SUDO_MODE_ENABLED=True)
    def test_admin_action_without_sudo_changes_nothing(self, super_admin_user, staff_user):
        _enroll(staff_user)
        browser = _verified_client(super_admin_user)

        response = _admin_action(browser, staff_user)

        # The admin site answers with its login view, which hands a signed-in
        # session without sudo on to the sudo step.
        assert response.status_code == 302
        assert urlsplit(response.url).path == "/admin-mgmt/login/"
        follow_up = browser.get(response.url)
        assert follow_up.status_code == 302
        assert urlsplit(follow_up.url).path == reverse("sudo_mode")
        assert _factor_rows(staff_user) == (2, 1, 1, 10)
        assert not _reset_rows(staff_user).exists()


@IGNORE_UNFOLD_ACTION_CHOICES
@pytest.mark.usefixtures("rls_test_role")
class TestResetUnderRlsRole:
    """The audit write under NOSUPERUSER/NOBYPASSRLS, as in production."""

    def test_facility_admin_reset_writes_audit_in_own_facility(
        self,
        admin_user,
        staff_user,
        rls_test_role,  # noqa: F811
    ):
        _enroll(staff_user)
        browser = _verified_client(admin_user)

        with as_rls_role(rls_test_role, facility_id=admin_user.facility_id):
            response = browser.post(_reset_url(staff_user))

        assert response.status_code == 302
        assert _factor_rows(staff_user) == (0, 0, 0, 0)
        assert _reset_rows(staff_user).get().facility_id == staff_user.facility_id

    def test_super_admin_action_writes_audit_in_target_facility(
        self,
        super_admin_user,
        foreign_staff,
        rls_test_role,  # noqa: F811
    ):
        _enroll(foreign_staff)
        browser = _verified_client(super_admin_user)

        with as_rls_role(rls_test_role, facility_id=""):
            response = _admin_action(browser, foreign_staff)

        assert response.status_code == 302
        assert _factor_rows(foreign_staff) == (0, 0, 0, 0)
        assert _reset_rows(foreign_staff).get().facility_id == foreign_staff.facility_id
