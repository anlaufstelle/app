"""Tests for the offline_key_salt endpoint and password-change rotation (Refs #573, #576, #958)."""

import base64

import pytest
from django.db import DatabaseError
from django.db.models import QuerySet

from core.models import AuditLog, User


@pytest.mark.django_db
class TestEnsureOfflineKeySalt:
    """Model-level invariants of ``User.ensure_offline_key_salt()`` (Refs #958)."""

    def test_lazy_generates_on_first_call(self, staff_user):
        assert staff_user.offline_key_salt == ""
        salt = staff_user.ensure_offline_key_salt()
        assert salt
        assert staff_user.offline_key_salt == salt

    def test_returns_same_value_on_second_call(self, staff_user):
        first = staff_user.ensure_offline_key_salt()
        second = staff_user.ensure_offline_key_salt()
        assert first == second

    def test_concurrent_first_derivations_agree_on_one_salt(self, staff_user):
        """Refs #1968: zwei parallele Erstabrufe duerfen NICHT zwei Salts erzeugen.

        ``OfflineKeySaltView`` ist POST-only und wird direkt nach dem Login
        gerufen — zwei Tabs genuegen. Das fruehere Check-generate-save-Muster
        liess beide Anfragen je einen eigenen Salt wuerfeln; der zweite ``save``
        gewann, und der Client mit dem ersten Salt leitete einen Schluessel ab,
        der zu nichts mehr passte (seine IndexedDB-Saetze wurden unlesbar).

        Zwei getrennt geladene Model-Instanzen derselben Zeile bilden genau
        diesen Wettlauf ab: beide sehen ``offline_key_salt == ""``.
        """
        first_request = User.objects.get(pk=staff_user.pk)
        second_request = User.objects.get(pk=staff_user.pk)

        first_salt = first_request.ensure_offline_key_salt()
        second_salt = second_request.ensure_offline_key_salt()

        assert first_salt == second_salt
        staff_user.refresh_from_db()
        assert staff_user.offline_key_salt == first_salt

    def test_first_assignment_fails_loud_when_the_write_is_swallowed(self, staff_user, monkeypatch):
        """Refs #1968: ein wirkungsloses Compare-and-set darf keinen LEEREN Salt liefern.

        Trifft ``filter(pk=..., offline_key_salt="").update(...)`` keine Zeile
        und steht in der DB weiterhin der Leerwert, liest ``refresh_from_db``
        genau diesen nach — ``OfflineKeySaltView`` antwortete mit
        ``{"salt": ""}`` und der Client leitete seinen Offline-Schluessel aus
        einem leeren Salt ab. Das ersetzte ``save(update_fields=[...])`` war in
        derselben Lage fail-loud (``DatabaseError``); diese Zusage bleibt.

        Der Fall ist heute nicht erreichbar (auf ``core_user`` liegt keine
        RLS-Policy), waere aber bei einer kuenftigen Policy- oder
        Grant-Aenderung genau der stille Bruch. Der Stub auf ``QuerySet.update``
        stellt ihn nach: Schreibvorgang ohne Wirkung, Leerwert bleibt stehen.
        """
        monkeypatch.setattr(QuerySet, "update", lambda self, **kwargs: 0)

        with pytest.raises(DatabaseError):
            staff_user.ensure_offline_key_salt()

    def test_salt_is_base64url(self, staff_user):
        salt = staff_user.ensure_offline_key_salt()
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
