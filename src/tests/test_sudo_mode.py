"""Tests fuer SudoMode (Refs #683)."""

import time

import pytest
from django.test import RequestFactory
from django.urls import reverse
from django_otp.oath import totp as oath_totp
from django_otp.plugins.otp_totp.models import TOTPDevice

from core.models import AuditLog
from core.services.security import (
    SUDO_SESSION_KEY,
    clear_sudo,
    enter_sudo,
    generate_backup_codes,
    is_in_sudo,
)


class TestSudoModeService:
    """Service-Level: enter/is_in/clear."""

    def _request_with_session(self):
        rf = RequestFactory()
        request = rf.get("/")

        class _Sess(dict):
            modified = False

        request.session = _Sess()
        return request

    def test_enter_sudo_sets_session_key(self):
        request = self._request_with_session()
        enter_sudo(request)
        assert SUDO_SESSION_KEY in request.session
        assert is_in_sudo(request)

    def test_is_in_sudo_false_without_key(self):
        request = self._request_with_session()
        assert is_in_sudo(request) is False

    def test_is_in_sudo_false_after_ttl(self):
        request = self._request_with_session()
        # TTL in der Vergangenheit setzen
        request.session[SUDO_SESSION_KEY] = int(time.time()) - 10
        assert is_in_sudo(request) is False

    def test_clear_sudo_removes_key(self):
        request = self._request_with_session()
        enter_sudo(request)
        clear_sudo(request)
        assert is_in_sudo(request) is False


@pytest.mark.django_db
class TestSudoModeViewGET:
    def test_get_renders_form(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("sudo_mode") + "?next=/clients/")
        assert response.status_code == 200
        body = response.content.decode()
        assert 'name="password"' in body
        assert 'name="next"' in body


@pytest.mark.django_db
class TestSudoModeViewPOST:
    def test_correct_password_enters_sudo(self, client, admin_user):
        admin_user.set_password("test-pw-123")
        admin_user.save()
        client.force_login(admin_user)
        response = client.post(
            reverse("sudo_mode"),
            {"password": "test-pw-123", "next": "/clients/"},
        )
        assert response.status_code == 302
        assert response.url == "/clients/"
        # Session muss SudoMode-Key tragen
        assert SUDO_SESSION_KEY in client.session
        # AuditLog SUDO_MODE_ENTERED geschrieben
        assert AuditLog.objects.filter(
            user=admin_user,
            action=AuditLog.Action.SUDO_MODE_ENTERED,
        ).exists()

    def test_wrong_password_returns_403(self, client, admin_user):
        admin_user.set_password("test-pw-123")
        admin_user.save()
        client.force_login(admin_user)
        response = client.post(
            reverse("sudo_mode"),
            {"password": "WRONG", "next": "/clients/"},
        )
        assert response.status_code == 403
        assert SUDO_SESSION_KEY not in client.session

    def test_wrong_password_writes_sudo_mode_failed_audit(self, client, admin_user):
        """S2 (Refs #1084): fehlgeschlagene Re-Auth muss im Audit-Trail
        sichtbar sein — asymmetrisch zu LOGIN_FAILED/MFA_FAILED wurde bisher
        nur der Erfolg (SUDO_MODE_ENTERED) geloggt."""
        admin_user.set_password("test-pw-123")
        admin_user.save()
        client.force_login(admin_user)
        response = client.post(
            reverse("sudo_mode"),
            {"password": "WRONG", "next": "/clients/"},
        )
        assert response.status_code == 403
        log = AuditLog.objects.filter(
            user=admin_user,
            action=AuditLog.Action.SUDO_MODE_FAILED,
        ).latest("timestamp")
        assert log.detail == {"factor": "password"}
        # Fehlversuch darf keinen Erfolgs-Eintrag erzeugen.
        assert not AuditLog.objects.filter(
            user=admin_user,
            action=AuditLog.Action.SUDO_MODE_ENTERED,
        ).exists()

    def test_wrong_password_does_not_write_login_failed(self, client, admin_user):
        """N7 (Refs #1444): Sudo-Re-Auth darf keinen LOGIN_FAILED-Eintrag
        erzeugen — sonst speisen Sudo-Fehlversuche den Login-Lockout
        (is_locked() in login_lockout.py zaehlt genau diese Action)."""
        admin_user.set_password("test-pw-123")
        admin_user.save()
        client.force_login(admin_user)
        response = client.post(
            reverse("sudo_mode"),
            {"password": "WRONG", "next": "/clients/"},
        )
        assert response.status_code == 403
        assert not AuditLog.objects.filter(
            user=admin_user,
            action=AuditLog.Action.LOGIN_FAILED,
        ).exists()
        # SUDO_MODE_FAILED bleibt das eigene Audit-Signal fuer den Fehlversuch.
        assert AuditLog.objects.filter(
            user=admin_user,
            action=AuditLog.Action.SUDO_MODE_FAILED,
        ).exists()

    def test_sudo_failures_do_not_feed_login_lockout(self, client, admin_user):
        """N7 (Refs #1444): Sudo-Fehlversuche duerfen den Login-Lockout weder
        ausloesen noch daran mitzaehlen — der regulaere Login desselben Users
        muss trotz kurz vor der Schwelle stehender LOGIN_FAILED-Historie
        weiter funktionieren."""
        from core.services.security import LOCKOUT_THRESHOLD, is_locked

        admin_user.set_password("test-pw-123")
        admin_user.save()
        for _ in range(LOCKOUT_THRESHOLD - 1):
            AuditLog.objects.create(
                facility=admin_user.facility,
                user=admin_user,
                action=AuditLog.Action.LOGIN_FAILED,
                detail={"username": admin_user.username},
            )
        client.force_login(admin_user)
        response = client.post(
            reverse("sudo_mode"),
            {"password": "WRONG", "next": "/clients/"},
        )
        assert response.status_code == 403
        assert is_locked(admin_user) is False

        # Regulaerer Login mit korrektem Passwort ist weiterhin moeglich —
        # frischer Client, da der obige bereits eingeloggt ist.
        from django.test import Client

        fresh_client = Client()
        login_response = fresh_client.post(
            reverse("login"),
            {"username": admin_user.username, "password": "test-pw-123"},
        )
        assert login_response.status_code == 302

    def test_open_redirect_protection(self, client, admin_user):
        admin_user.set_password("test-pw-123")
        admin_user.save()
        client.force_login(admin_user)
        response = client.post(
            reverse("sudo_mode"),
            {"password": "test-pw-123", "next": "https://evil.example.com/"},
        )
        assert response.status_code == 302
        assert response.url == "/", "Externer Redirect muss auf '/' gefiltert werden."


@pytest.mark.django_db
class TestSudoModeTwoFactor:
    """A3.2 (Refs #1024): bei aktivem TOTP verlangt der Sudo-Mode zusätzlich
    einen frischen 2. Faktor (OTP oder Backup-Code).

    Ein über eine gestohlene Session + Passwort eindringender Angreifer darf
    sensible Aktionen (MFA-Disable, DSGVO-Export) nicht ohne zweiten Faktor
    freischalten.
    """

    def _login_with_password(self, client, admin_user):
        admin_user.set_password("test-pw-123")
        admin_user.save()
        client.force_login(admin_user)
        # Login-MFA bereits absolviert — sonst fängt die MFAEnforcementMiddleware
        # den Request ab, bevor der Sudo-View greift. A3.2 verlangt darüber hinaus
        # einen FRISCHEN 2. Faktor für die sensible Aktion.
        session = client.session
        session["mfa_verified"] = True
        session.save()

    def test_totp_user_without_otp_is_rejected(self, client, admin_user):
        self._login_with_password(client, admin_user)
        TOTPDevice.objects.create(user=admin_user, name="default", confirmed=True)
        response = client.post(reverse("sudo_mode"), {"password": "test-pw-123", "next": "/clients/"})
        assert response.status_code == 403
        assert SUDO_SESSION_KEY not in client.session
        log = AuditLog.objects.filter(
            user=admin_user,
            action=AuditLog.Action.SUDO_MODE_FAILED,
        ).latest("timestamp")
        assert log.detail == {"factor": "otp"}
        assert not AuditLog.objects.filter(
            user=admin_user,
            action=AuditLog.Action.SUDO_MODE_ENTERED,
        ).exists()

    def test_totp_user_with_valid_otp_enters_sudo(self, client, admin_user):
        self._login_with_password(client, admin_user)
        device = TOTPDevice.objects.create(user=admin_user, name="default", confirmed=True)
        token = str(oath_totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits)).zfill(
            device.digits
        )
        response = client.post(
            reverse("sudo_mode"),
            {"password": "test-pw-123", "otp_token": token, "next": "/clients/"},
        )
        assert response.status_code == 302
        assert SUDO_SESSION_KEY in client.session

    def test_totp_user_with_wrong_otp_is_rejected(self, client, admin_user):
        self._login_with_password(client, admin_user)
        TOTPDevice.objects.create(user=admin_user, name="default", confirmed=True)
        response = client.post(
            reverse("sudo_mode"),
            {"password": "test-pw-123", "otp_token": "000000", "next": "/clients/"},
        )
        assert response.status_code == 403
        assert SUDO_SESSION_KEY not in client.session

    def test_wrong_otp_writes_sudo_mode_failed_audit(self, client, admin_user):
        """S2 (Refs #1084): auch der OTP-Pfad der Re-Auth muss Fehlversuche
        auditieren (detail.factor unterscheidet Passwort- vs. OTP-Faktor)."""
        self._login_with_password(client, admin_user)
        TOTPDevice.objects.create(user=admin_user, name="default", confirmed=True)
        response = client.post(
            reverse("sudo_mode"),
            {"password": "test-pw-123", "otp_token": "000000", "next": "/clients/"},
        )
        assert response.status_code == 403
        log = AuditLog.objects.filter(
            user=admin_user,
            action=AuditLog.Action.SUDO_MODE_FAILED,
        ).latest("timestamp")
        assert log.detail == {"factor": "otp"}
        assert not AuditLog.objects.filter(
            user=admin_user,
            action=AuditLog.Action.SUDO_MODE_ENTERED,
        ).exists()

    def test_backup_code_enters_sudo(self, client, admin_user):
        self._login_with_password(client, admin_user)
        TOTPDevice.objects.create(user=admin_user, name="default", confirmed=True)
        codes = generate_backup_codes(admin_user)
        response = client.post(
            reverse("sudo_mode"),
            {"password": "test-pw-123", "otp_token": codes[0], "next": "/clients/"},
        )
        assert response.status_code == 302
        assert SUDO_SESSION_KEY in client.session

    def test_non_totp_user_needs_only_password(self, client, admin_user):
        """Regression: User ohne TOTP-Gerät kommen weiterhin mit Passwort durch."""
        self._login_with_password(client, admin_user)
        response = client.post(reverse("sudo_mode"), {"password": "test-pw-123", "next": "/clients/"})
        assert response.status_code == 302
        assert SUDO_SESSION_KEY in client.session

    def test_get_shows_otp_field_for_totp_user(self, client, admin_user):
        TOTPDevice.objects.create(user=admin_user, name="default", confirmed=True)
        self._login_with_password(client, admin_user)
        body = client.get(reverse("sudo_mode")).content.decode()
        assert 'name="otp_token"' in body

    def test_get_hides_otp_field_for_non_totp_user(self, client, admin_user):
        client.force_login(admin_user)
        body = client.get(reverse("sudo_mode")).content.decode()
        assert 'name="otp_token"' not in body


@pytest.mark.django_db
class TestDSGVODocsNotSudoGated:
    """Refs #1252: Das DSGVO-Doku-Paket ist bewusst NICHT sudo-pflichtig.

    SUDO_MODE_ENABLED ist in test.py auf False — diese Tests aktivieren es
    explizit per ``settings``-Fixture und belegen, dass die Vorlagen-Views
    auch bei aktivem SudoMode OHNE Re-Auth erreichbar sind (öffentliche
    Templates + Einrichtungsname, niedrige Sensibilität). #683 zielte auf
    den Rohdaten-Export — der bleibt sudo-pflichtig (``ClientDataExport*``).
    """

    def test_dsgvo_package_reachable_without_sudo(self, client, admin_user, settings):
        settings.SUDO_MODE_ENABLED = True
        client.force_login(admin_user)
        response = client.get(reverse("core:dsgvo_package"))
        assert response.status_code == 200

    def test_dsgvo_document_reachable_without_sudo(self, client, admin_user, settings):
        settings.SUDO_MODE_ENABLED = True
        client.force_login(admin_user)
        response = client.get(reverse("core:dsgvo_document", kwargs={"document": "verarbeitungsverzeichnis"}))
        assert response.status_code == 200


@pytest.mark.django_db
class TestSystemViewsRequireSudo:
    """Refs #1253: gezielte SudoMode-Pflicht auf den maechtigsten /system/-Aktionen.

    SUDO_MODE_ENABLED ist in test.py auf False — diese Tests aktivieren es
    explizit. Der Cross-Facility-Audit-Export (inkl. IP-Adressen), der
    Wartungsmodus-Toggle (installationsweites 503) und das Entsperren eines
    Kontos verlangen jetzt eine frische Re-Auth — auch aus einer bestehenden
    super_admin-Session heraus. Das Rollen-Gate greift weiterhin zuerst.
    """

    @staticmethod
    def _enter_sudo(client):
        session = client.session
        session[SUDO_SESSION_KEY] = int(time.time()) + 900
        session.save()

    def test_audit_export_redirects_without_sudo(self, client, super_admin_user, settings):
        settings.SUDO_MODE_ENABLED = True
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_audit_export"))
        assert response.status_code == 302
        assert "/sudo/" in response.url

    def test_audit_export_passes_with_sudo(self, client, super_admin_user, settings):
        settings.SUDO_MODE_ENABLED = True
        client.force_login(super_admin_user)
        self._enter_sudo(client)
        response = client.get(reverse("core:system_audit_export"))
        assert response.status_code == 200

    def test_maintenance_redirects_without_sudo(self, client, super_admin_user, settings):
        settings.SUDO_MODE_ENABLED = True
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_maintenance"))
        assert response.status_code == 302
        assert "/sudo/" in response.url

    def test_unlock_redirects_without_sudo(self, client, super_admin_user, settings):
        settings.SUDO_MODE_ENABLED = True
        client.force_login(super_admin_user)
        response = client.post(reverse("core:system_unlock"), {"username": "irgendwer"})
        assert response.status_code == 302
        assert "/sudo/" in response.url

    def test_role_gate_runs_before_sudo(self, client, admin_user, settings):
        """facility_admin -> 403 (Rollen-Gate vor SudoMode), kein /sudo/-Redirect."""
        settings.SUDO_MODE_ENABLED = True
        client.force_login(admin_user)
        response = client.get(reverse("core:system_audit_export"))
        assert response.status_code == 403
