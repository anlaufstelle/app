"""Tests für den TOTP-Setup-Flow (Issue #521 Teil-Umsetzung)."""

import base64

import pytest
from django_otp.oath import totp as oath_totp
from django_otp.plugins.otp_totp.models import TOTPDevice

from core.models import AuditLog


@pytest.mark.django_db
class TestMFASetupFlow:
    def test_setup_page_requires_login(self, client):
        response = client.get("/mfa/setup/")
        assert response.status_code == 302
        assert "/login/" in response.url

    def test_setup_page_shows_qrcode_and_creates_unconfirmed_device(self, client, staff_user):
        client.login(username="teststaff", password="testpass123")
        # Login-Redirect-Flow durchlaufen, dabei setzt der Client eine Session mit mfa_verified=False
        response = client.get("/mfa/setup/")
        assert response.status_code == 200
        content = response.content.decode("utf-8")
        # Das gerenderte Template enthält das data-testid der QR-Grafik.
        assert "mfa-qrcode" in content
        assert "mfa-token-input" in content
        # Ein unbestätigtes TOTPDevice wurde beim Rendern angelegt.
        assert TOTPDevice.objects.filter(user=staff_user, confirmed=False).count() == 1

    def test_setup_post_confirms_device_with_valid_token(self, client, staff_user):
        client.login(username="teststaff", password="testpass123")
        # Zuerst GET, damit das Device erzeugt wird.
        client.get("/mfa/setup/")
        device = TOTPDevice.objects.get(user=staff_user, confirmed=False)
        # Gültigen Token direkt aus dem Secret berechnen.
        token = oath_totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits)
        response = client.post("/mfa/setup/", {"token": f"{token:0{device.digits}d}"})
        assert response.status_code == 302
        assert response.url == "/mfa/settings/"
        device.refresh_from_db()
        assert device.confirmed is True
        # AuditLog wurde geschrieben.
        assert AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.MFA_ENABLED).exists()

    def test_setup_post_rejects_invalid_token(self, client, staff_user):
        client.login(username="teststaff", password="testpass123")
        client.get("/mfa/setup/")
        device = TOTPDevice.objects.get(user=staff_user, confirmed=False)
        response = client.post("/mfa/setup/", {"token": "000000"})
        assert response.status_code == 200
        device.refresh_from_db()
        assert device.confirmed is False
        assert not AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.MFA_ENABLED).exists()

    def test_setup_secret_is_base32_of_device_key(self, client, staff_user):
        """Manuell anzeigbares Secret muss Base32 sein (RFC 6238/3548).

        Authenticator-Apps (FreeOTP+, Google Authenticator, …) interpretieren
        die ``secret``-Eingabe als Base32. Hex-Darstellung führt zu falsch
        initialisiertem Gerät und „Code ungültig"-Fehlern.
        """
        client.login(username="teststaff", password="testpass123")
        response = client.get("/mfa/setup/")
        assert response.status_code == 200
        device = TOTPDevice.objects.get(user=staff_user, confirmed=False)
        secret = response.context["secret"]
        padding = "=" * (-len(secret) % 8)
        decoded = base64.b32decode(secret + padding)
        assert decoded == device.bin_key

    def test_setup_redirects_when_device_already_confirmed(self, client, staff_user):
        TOTPDevice.objects.create(user=staff_user, name="existing", confirmed=True)
        client.login(username="teststaff", password="testpass123")
        # Session als bereits verifiziert markieren, damit die Middleware nicht umleitet.
        session = client.session
        session["mfa_verified"] = True
        session.save()
        response = client.get("/mfa/setup/")
        assert response.status_code == 302
        assert response.url == "/mfa/settings/"


@pytest.mark.django_db
class TestMFASettingsPage:
    def test_shows_setup_button_when_no_device(self, client, staff_user):
        client.login(username="teststaff", password="testpass123")
        response = client.get("/mfa/settings/")
        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "mfa-setup-button" in content

    def test_shows_disable_button_when_optional_and_enabled(self, client, staff_user):
        TOTPDevice.objects.create(user=staff_user, name="test", confirmed=True)
        client.login(username="teststaff", password="testpass123")
        session = client.session
        session["mfa_verified"] = True
        session.save()
        response = client.get("/mfa/settings/")
        content = response.content.decode("utf-8")
        assert "mfa-disable-button" in content

    def test_hides_disable_button_when_facility_enforced(self, client, staff_user, facility):
        from core.models import Settings

        Settings.objects.create(facility=facility, mfa_enforced_facility_wide=True)
        TOTPDevice.objects.create(user=staff_user, name="test", confirmed=True)
        client.login(username="teststaff", password="testpass123")
        session = client.session
        session["mfa_verified"] = True
        session.save()
        response = client.get("/mfa/settings/")
        content = response.content.decode("utf-8")
        assert "mfa-disable-button" not in content

    def test_disable_view_removes_device(self, client, staff_user):
        TOTPDevice.objects.create(user=staff_user, name="test", confirmed=True)
        client.login(username="teststaff", password="testpass123")
        session = client.session
        session["mfa_verified"] = True
        session.save()
        response = client.post("/mfa/disable/")
        assert response.status_code == 302
        assert response.url == "/mfa/settings/"
        assert TOTPDevice.objects.filter(user=staff_user).count() == 0
        assert AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.MFA_DISABLED).exists()

    def test_disable_view_refuses_when_enforced(self, client, staff_user):
        staff_user.mfa_required = True
        staff_user.save(update_fields=["mfa_required"])
        TOTPDevice.objects.create(user=staff_user, name="test", confirmed=True)
        client.login(username="teststaff", password="testpass123")
        session = client.session
        session["mfa_verified"] = True
        session.save()
        response = client.post("/mfa/disable/")
        assert response.status_code == 302
        assert TOTPDevice.objects.filter(user=staff_user).count() == 1
