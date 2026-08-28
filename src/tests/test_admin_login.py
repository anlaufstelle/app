"""Der Admin-Login traegt dieselben Brute-Force-Schranken wie der App-Login (Refs #2020).

Der App-Login (``CustomLoginView``, ``views/auth.py``) haengt seit #598 an einem
Zwei-Ebenen-Ratelimit (5/m je IP gegen klassischen Brute-Force, 10/h je
Username gegen verteilte Angriffe von rotierenden IPs) und prueft seit #612 den
Account-Lockout. ``/admin-mgmt/login/`` fiel dagegen auf Djangos Stock-Auth
zurueck: Raten gegen dieses Formular war gratis, und ein gesperrter Account
kam dort mit korrektem Passwort weiterhin durch.

Nachgelagerte Gates (MFA-Pflicht, Sudo-Mode) begrenzten den Schaden, aber sie
sind kein Ersatz fuer die Schranke am Formular selbst — die Fehlversuche
wandern ausserdem in dieselben ``LOGIN_FAILED``-Zeilen, aus denen Lockout und
Breach-Detection ihre Signale ziehen.

Die Tests spiegeln bewusst ``test_auth.TestRateLimiting`` /
``TestLoginLockoutService``: dieselben Schwellen, dieselbe 429-Erwartung ueber
``handler403`` (Refs #1354).
"""

from __future__ import annotations

import pytest

from core.models import AuditLog

pytestmark = pytest.mark.django_db

ADMIN_LOGIN_URL = "/admin-mgmt/login/"


class TestAdminLoginRateLimit:
    @pytest.fixture(autouse=True)
    def _enable_ratelimit(self, ratelimit_enabled):
        """Wie in ``test_auth``: Limit scharf schalten, Zaehlfenster festnageln."""
        return ratelimit_enabled

    def test_ip_bucket_blocks_after_five_attempts(self, client, admin_user):
        """5/m je IP — der sechste POST von derselben IP wird abgewiesen."""
        for _ in range(5):
            client.post(
                ADMIN_LOGIN_URL,
                {"username": admin_user.username, "password": "falsch"},
                REMOTE_ADDR="10.77.0.1",
            )

        response = client.post(
            ADMIN_LOGIN_URL,
            {"username": admin_user.username, "password": "falsch"},
            REMOTE_ADDR="10.77.0.1",
        )

        assert response.status_code == 429

    def test_username_bucket_blocks_distributed_attempts(self, client, admin_user):
        """10/h je Username — rotierende IPs entkommen dem Limit nicht."""
        for i in range(10):
            client.post(
                ADMIN_LOGIN_URL,
                {"username": admin_user.username, "password": "falsch"},
                REMOTE_ADDR=f"10.78.0.{i + 1}",
            )

        response = client.post(
            ADMIN_LOGIN_URL,
            {"username": admin_user.username, "password": "falsch"},
            REMOTE_ADDR="10.78.0.99",
        )

        assert response.status_code == 429

    def test_get_is_not_rate_limited(self, client):
        """Die Login-SEITE bleibt erreichbar — das Limit gilt nur dem POST."""
        for _ in range(8):
            response = client.get(ADMIN_LOGIN_URL, REMOTE_ADDR="10.79.0.1")
        assert response.status_code == 200


class TestAdminLoginLockout:
    _IP = "192.0.2.55"

    def _fail(self, user, count):
        from core.services.security import LOCKOUT_THRESHOLD

        for _ in range(count if count is not None else LOCKOUT_THRESHOLD):
            AuditLog.objects.create(
                facility=user.facility,
                user=user,
                action=AuditLog.Action.LOGIN_FAILED,
                detail={"username": user.username},
                ip_address=self._IP,
            )

    def test_locked_account_cannot_log_in_with_correct_password(self, client, admin_user):
        """Wie ``CustomLoginView``: korrektes Passwort reicht bei Sperre nicht."""
        self._fail(admin_user, None)

        response = client.post(
            ADMIN_LOGIN_URL,
            {"username": admin_user.username, "password": "testpass123"},
            REMOTE_ADDR=self._IP,
        )

        assert response.status_code == 200, "Formular wird mit Fehler neu gerendert, kein Redirect"
        assert "_auth_user_id" not in client.session, "Ein gesperrter Account darf keine Session bekommen"

    def test_lockout_writes_audit_entry_with_reason(self, client, admin_user):
        self._fail(admin_user, None)

        client.post(
            ADMIN_LOGIN_URL,
            {"username": admin_user.username, "password": "testpass123"},
            REMOTE_ADDR=self._IP,
        )

        blocked = AuditLog.objects.filter(
            action=AuditLog.Action.LOGIN_FAILED,
            user=admin_user,
            detail__reason="locked",
        )
        assert blocked.count() == 1

    def test_unlocked_account_still_logs_in(self, client, admin_user):
        """Gegenprobe: ohne Sperre bleibt der Admin-Login unveraendert nutzbar."""
        response = client.post(
            ADMIN_LOGIN_URL,
            {"username": admin_user.username, "password": "testpass123"},
            REMOTE_ADDR=self._IP,
        )

        assert response.status_code == 302
        assert client.session.get("_auth_user_id") == str(admin_user.pk)

    def test_lockout_is_scoped_to_source_ip(self, client, admin_user):
        """N9 (Refs #1446): fremde Fehlversuche sperren die eigene IP nicht."""
        self._fail(admin_user, None)

        response = client.post(
            ADMIN_LOGIN_URL,
            {"username": admin_user.username, "password": "testpass123"},
            REMOTE_ADDR="198.51.100.200",
        )

        assert response.status_code == 302
        assert client.session.get("_auth_user_id") == str(admin_user.pk)
