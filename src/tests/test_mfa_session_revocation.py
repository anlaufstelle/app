"""Account-local, atomic session revocation on self-service factor changes."""

from urllib.parse import urlsplit

import pytest
from django.contrib.auth import HASH_SESSION_KEY
from django.db.models.query import QuerySet
from django.test import Client
from django_otp.oath import totp

from core.models import AuditLog, EncryptedTOTPDevice, User
from core.services.security import verify_totp_or_backup

pytestmark = pytest.mark.django_db


def _browser(user):
    browser = Client()
    browser.force_login(user)
    session = browser.session
    session["mfa_verified"] = True
    session["local_work_marker"] = "keep"
    session.save()
    return browser


def _prepare(browser, user, action):
    if action == "disable":
        return "/mfa/disable/", {}, None
    assert browser.get("/mfa/change-device/").status_code == 200
    pending = EncryptedTOTPDevice.objects.get(user=user, confirmed=False)
    token = totp(pending.bin_key, step=pending.step, t0=pending.t0, digits=pending.digits)
    response = browser.post("/mfa/change-device/", {"token": f"{token:0{pending.digits}d}"})
    assert response.url == "/mfa/backup-codes/"
    return response.url, {"confirm_saved": "on"}, pending


def _audits(user, action):
    return AuditLog.objects.filter(
        user=user, action=AuditLog.Action.MFA_DISABLED if action == "disable" else AuditLog.Action.MFA_ENABLED
    )


@pytest.mark.parametrize("action", ["change", "disable"])
def test_only_other_account_sessions_end(action, settings, staff_user, lead_user):
    settings.OFFLINE_REVOCATION_SESSION_SCAN_LIMIT = 0
    old = EncryptedTOTPDevice.objects.create(user=staff_user, confirmed=True)
    salt = staff_user.ensure_offline_key_salt()
    current, other, foreign = _browser(staff_user), _browser(staff_user), _browser(lead_user)
    url, data, pending = _prepare(current, staff_user, action)
    old_key = current.session.session_key

    response = current.post(url, data)

    assert response.status_code == 302
    signed_out = other.get("/mfa/settings/")
    assert signed_out.status_code == 302
    assert signed_out.url.startswith("/login/")
    assert current.get("/mfa/settings/").status_code == 200
    assert foreign.get("/mfa/settings/").status_code == 200
    assert current.session.session_key != old_key
    assert current.session["local_work_marker"] == "keep"
    stored = User.objects.get(pk=staff_user.pk)
    assert current.session[HASH_SESSION_KEY] == stored.get_session_auth_hash()
    assert stored.offline_key_salt == salt
    assert not EncryptedTOTPDevice.objects.filter(pk=old.pk).exists()
    assert _audits(staff_user, action).get().detail["sessions_revoked"] is True
    assert current.session.get("mfa_verified") is (True if pending else None)
    if pending:
        assert current.session["otp_device_id"] == pending.persistent_id


@pytest.mark.parametrize("action", ["change", "disable"])
def test_offline_revocation_check_requires_reauthentication(action, staff_user):
    EncryptedTOTPDevice.objects.create(user=staff_user, confirmed=True)
    current, offline = _browser(staff_user), _browser(staff_user)
    assert offline.get("/api/v1/offline/revocations/").status_code == 200
    url, data, _pending = _prepare(current, staff_user, action)

    current.post(url, data)

    response = offline.get("/api/v1/offline/revocations/")
    assert response.status_code == 302
    assert urlsplit(response.url).path == "/login/"


@pytest.mark.parametrize("action", ["change", "disable"])
@pytest.mark.parametrize("failure", ["device", "audit", "revocation", "storage"])
def test_failure_preserves_factor_sessions_and_retry(action, failure, staff_user, monkeypatch):
    old = EncryptedTOTPDevice.objects.create(user=staff_user, confirmed=True)
    current, other = _browser(staff_user), _browser(staff_user)
    url, data, pending = _prepare(current, staff_user, action)
    hash_before = User.objects.get(pk=staff_user.pk).password
    session_before = dict(current.session)
    key_before = current.session.session_key

    def fail(*args, **kwargs):
        raise RuntimeError("Sensitive failure details must not reach the response.")

    if failure == "device":
        if pending:
            monkeypatch.setattr(EncryptedTOTPDevice, "save", fail)
        else:
            original_delete = QuerySet.delete

            def fail_totp_delete(queryset):
                if queryset.model is EncryptedTOTPDevice:
                    fail()
                return original_delete(queryset)

            monkeypatch.setattr(QuerySet, "delete", fail_totp_delete)
    elif failure == "audit":
        monkeypatch.setattr(AuditLog, "save", fail)
    elif failure == "storage":
        original_update = QuerySet.update

        def miss_password_update(queryset, **values):
            return 0 if "password" in values else original_update(queryset, **values)

        monkeypatch.setattr(QuerySet, "update", miss_password_update)
    else:
        User.objects.filter(pk=staff_user.pk).update(password="x" * 128)
        staff_user.refresh_from_db()
        current, other = _browser(staff_user), _browser(staff_user)
        # Carry the already authorized ceremony into the valid session.
        session = current.session
        session.update({key: value for key, value in session_before.items() if key.startswith("mfa_")})
        session.save()
        hash_before = staff_user.password
        key_before = current.session.session_key

    response = current.post(url, data)

    monkeypatch.undo()
    assert response.status_code == 302
    assert response.url == ("/mfa/backup-codes/" if pending else "/mfa/settings/")
    assert User.objects.get(pk=staff_user.pk).password == hash_before
    assert EncryptedTOTPDevice.objects.filter(pk=old.pk, confirmed=True).exists()
    assert not _audits(staff_user, action).exists()
    assert current.session.session_key == key_before
    failure_page = current.get(response.url)
    shown = [str(message) for message in failure_page.context["messages"]]
    assert current.get("/mfa/settings/").status_code == 200
    assert other.get("/mfa/settings/").status_code == 200
    assert any("nicht geändert" in message for message in shown)
    assert all("Sensitive" not in message for message in shown)
    token = totp(old.bin_key, step=old.step, t0=old.t0, digits=old.digits)
    assert verify_totp_or_backup(staff_user, f"{token:0{old.digits}d}")
    if pending:
        assert current.session["mfa_setup_pending"] is True
        assert current.session["mfa_setup_device_pk"] == pending.pk
        assert current.session["mfa_backup_codes"] == session_before["mfa_backup_codes"]
        assert EncryptedTOTPDevice.objects.filter(pk=pending.pk, confirmed=False).exists()
    if failure != "revocation":
        assert current.post(url, data).status_code == 302
        assert _audits(staff_user, action).count() == 1
        assert other.get("/mfa/settings/").url.startswith("/login/")


def test_first_setup_does_not_claim_session_revocation(staff_user):
    current, other = _browser(staff_user), _browser(staff_user)
    url, data, _pending = _prepare(current, staff_user, "change")

    current.post(url, data)

    assert _audits(staff_user, "change").get().detail["sessions_revoked"] is False
    assert other.get("/mfa/settings/").status_code == 200


@pytest.mark.parametrize("action", ["change", "disable"])
def test_stale_authorization_cannot_survive_a_concurrent_password_change(action, staff_user):
    from django.core.exceptions import PermissionDenied

    from core.services.security.mfa import confirm_totp_setup, disable_mfa

    old = EncryptedTOTPDevice.objects.create(user=staff_user, confirmed=True)
    pending = EncryptedTOTPDevice.objects.create(user=staff_user, confirmed=False)
    fresh = User.objects.get(pk=staff_user.pk)
    fresh.set_password("New-concurrent-password-2640")
    fresh.save(update_fields=["password"])
    hash_after_change = fresh.password

    with pytest.raises(PermissionDenied):
        if action == "change":
            confirm_totp_setup(staff_user, pending.pk)
        else:
            disable_mfa(staff_user)

    assert User.objects.get(pk=staff_user.pk).password == hash_after_change
    assert EncryptedTOTPDevice.objects.filter(pk=old.pk, confirmed=True).exists()
    assert not _audits(staff_user, action).exists()


@pytest.mark.parametrize("action", ["change", "disable"])
def test_confirmation_and_success_explain_session_revocation(action, staff_user):
    EncryptedTOTPDevice.objects.create(user=staff_user, confirmed=True)
    current = _browser(staff_user)
    url, data, _pending = _prepare(current, staff_user, action)
    confirmation = current.get(url)
    assert "Andere angemeldete Geräte werden abgemeldet." in confirmation.content.decode()

    response = current.post(url, data)

    success = current.get(response.url)
    assert "Andere angemeldete Geräte wurden abgemeldet." in success.content.decode()
