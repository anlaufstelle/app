"""Tests for the offline_key_salt endpoint and password-change rotation (Refs #573, #576)."""

import base64

import pytest

from core.models import AuditLog
from core.services.offline_keys import ensure_offline_key_salt


@pytest.mark.django_db
class TestEnsureOfflineKeySalt:
    """Service-level invariants of ensure_offline_key_salt()."""

    def test_lazy_generates_on_first_call(self, staff_user):
        assert staff_user.offline_key_salt == ""
        salt = ensure_offline_key_salt(staff_user)
        assert salt
        assert staff_user.offline_key_salt == salt

    def test_returns_same_value_on_second_call(self, staff_user):
        first = ensure_offline_key_salt(staff_user)
        second = ensure_offline_key_salt(staff_user)
        assert first == second

    def test_salt_is_base64url(self, staff_user):
        salt = ensure_offline_key_salt(staff_user)
        # 16 random bytes → base64url without padding → 22 chars
        assert len(salt) == 22
        # Must round-trip after re-padding
        padded = salt + "=" * (4 - len(salt) % 4)
        raw = base64.urlsafe_b64decode(padded)
        assert len(raw) == 16


@pytest.mark.django_db
class TestOfflineKeySaltView:
    """HTTP-level behaviour of POST /auth/offline-key-salt/."""

    URL = "/auth/offline-key-salt/"

    def test_requires_login(self, client):
        response = client.post(self.URL)
        assert response.status_code in (302, 403)

    def test_post_returns_salt_json(self, client, staff_user):
        client.force_login(staff_user)
        response = client.post(self.URL)
        assert response.status_code == 200
        body = response.json()
        assert "salt" in body
        assert len(body["salt"]) == 22

    def test_get_not_allowed(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(self.URL)
        assert response.status_code == 405

    def test_returns_same_salt_on_repeated_calls(self, client, staff_user):
        client.force_login(staff_user)
        first = client.post(self.URL).json()["salt"]
        second = client.post(self.URL).json()["salt"]
        assert first == second

    def test_audit_log_created(self, client, staff_user):
        client.force_login(staff_user)
        before = AuditLog.objects.filter(action=AuditLog.Action.OFFLINE_KEY_FETCH).count()
        client.post(self.URL)
        after = AuditLog.objects.filter(
            action=AuditLog.Action.OFFLINE_KEY_FETCH,
            user=staff_user,
        ).count()
        assert after == before + 1

    def test_different_users_get_different_salts(self, client, staff_user, lead_user):
        client.force_login(staff_user)
        salt_staff = client.post(self.URL).json()["salt"]
        client.logout()
        client.force_login(lead_user)
        salt_lead = client.post(self.URL).json()["salt"]
        assert salt_staff != salt_lead


@pytest.mark.django_db
class TestPasswordChangeRotatesSalt:
    """A successful password change must wipe the offline salt."""

    def test_salt_cleared_on_password_change(self, client, staff_user):
        client.force_login(staff_user)
        original = client.post("/auth/offline-key-salt/").json()["salt"]
        assert original

        # Change password
        staff_user.set_password("oldpassword")
        staff_user.save()
        client.logout()
        client.login(username=staff_user.username, password="oldpassword")
        response = client.post(
            "/password-change/",
            {
                "old_password": "oldpassword",
                "new_password1": "AnewStrongPw123!",
                "new_password2": "AnewStrongPw123!",
            },
        )
        # Password-change view redirects on success
        assert response.status_code == 302

        staff_user.refresh_from_db()
        assert staff_user.offline_key_salt == ""

        # Next salt fetch generates a new value
        new_salt = client.post("/auth/offline-key-salt/").json()["salt"]
        assert new_salt != original
