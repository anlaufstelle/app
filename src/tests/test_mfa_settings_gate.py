"""Tests für das mfa_verified-Gate der MFASettingsView (Refs #1590).

Die MFA-Enforcement-Middleware nimmt den gesamten ``/mfa/``-Prefix aus
(``EXEMPT_URLS``), damit die Setup-/Verify-Zeremonie erreichbar bleibt. Die
read-only Settings-Seite gehört aber NICHT zur Zeremonie: eine passwort-
authentifizierte, aber noch nicht per zweitem Faktor verifizierte Session darf
die Passkey-Aliasse/Backup-Code-Zahl nicht sehen (Info-Disclosure, Low).
"""

import pytest
from django_otp.plugins.otp_totp.models import TOTPDevice


@pytest.mark.django_db
class TestMFASettingsVerifiedGate:
    def test_password_only_session_with_device_redirects_to_verify(self, client, staff_user):
        """Bestätigtes Gerät vorhanden, Session aber nicht verifiziert →
        GET /mfa/settings/ leitet auf /mfa/verify/ um statt 200 zu liefern."""
        TOTPDevice.objects.create(user=staff_user, name="active", confirmed=True)
        client.force_login(staff_user)
        # force_login setzt kein mfa_verified — genau die passwort-only-Session.
        assert client.session.get("mfa_verified") is not True
        response = client.get("/mfa/settings/")
        assert response.status_code == 302
        assert response.url == "/mfa/verify/"

    def test_verified_session_can_open_settings(self, client, staff_user):
        """Verifizierte Session sieht die Einstellungen wie bisher (200)."""
        TOTPDevice.objects.create(user=staff_user, name="active", confirmed=True)
        client.force_login(staff_user)
        session = client.session
        session["mfa_verified"] = True
        session.save()
        response = client.get("/mfa/settings/")
        assert response.status_code == 200

    def test_user_without_device_can_open_settings(self, client, staff_user):
        """Ohne bestätigtes Gerät gibt es nichts zu verifizieren — kein Gate,
        damit der Erstsetup-Einstieg über die Settings-Seite erreichbar bleibt."""
        client.force_login(staff_user)
        response = client.get("/mfa/settings/")
        assert response.status_code == 200

    def test_requires_login(self, client):
        response = client.get("/mfa/settings/")
        assert response.status_code == 302
        assert "/login/" in response.url
