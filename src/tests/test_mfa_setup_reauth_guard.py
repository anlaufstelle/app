"""Guard: erneutes MFA-Setup verlangt den Nachweis des bestehenden Faktors (Refs #2287).

``MFASetupView.get`` gatete den erneuten Einstieg, ``post`` nicht — eine nur
passwortauthentifizierte Sitzung (``mfa_verified`` fehlt) konnte damit trotz
bestehendem bestaetigtem zweiten Faktor ein zusaetzliches Geraet einrichten,
frische Backup-Codes erzeugen (die den alten Satz ersetzen) und sich ueber den
Setup-Abschluss selbst auf ``mfa_verified`` heben. Bestandsfaktor ist dabei
TOTP **oder** Passkey (ADR-032).

Die Tests hier beschreiben ausschliesslich die Ablehnung. Der regulaere
Erstsetup-Pfad (einschliesslich Backup-Code-Quittierung) ist in
``test_mfa_setup.py`` und ``test_mutation_pins_0992_security_mfa.py`` verankert.
"""

from __future__ import annotations

import json

import pytest
from django_otp.oath import totp as oath_totp
from django_otp.plugins.otp_static.models import StaticDevice

from core.models import AuditLog
from core.models import EncryptedTOTPDevice as TOTPDevice

SETUP_URL = "/mfa/setup/"
VERIFY_URL = "/mfa/verify/"
BACKUP_CODES_URL = "/mfa/backup-codes/"


def _confirmed_totp(user, *, name="bestand"):
    """Bestehender, bestaetigter zweiter Faktor."""
    return TOTPDevice.objects.create(user=user, name=name, confirmed=True)


def _unconfirmed_totp(user, *, name="neu"):
    """Frisch angelegtes, noch unbestaetigtes Geraet (wie ``_get_unconfirmed_device``)."""
    return TOTPDevice.objects.create(user=user, name=name, confirmed=False)


def _valid_token(device: TOTPDevice) -> str:
    token = oath_totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits)
    return f"{token:0{device.digits}d}"


def _confirmed_passkey(user, *, credential_id=b"cred-2287"):
    from django_otp_webauthn.models import WebAuthnCredential

    return WebAuthnCredential.objects.create(
        user=user,
        name="Bestandsschlüssel",
        confirmed=True,
        credential_id=credential_id,
        public_key=b"pubkey-bytes",
        aaguid="00000000-0000-0000-0000-000000000000",
        sign_count=0,
    )


def _login_password_only(client):
    """Passwortauthentifizierte Sitzung OHNE ``mfa_verified``."""
    assert client.login(username="teststaff", password="testpass123")
    assert client.session.get("mfa_verified") is not True


def _assert_no_setup_side_effects(client, user):
    """Weder neuer Faktor noch neuer Backup-Code-Satz noch Rechteanhebung."""
    assert client.session.get("mfa_verified") is not True
    assert client.session.get("mfa_setup_pending") is not True
    assert client.session.get("mfa_backup_codes") is None
    assert TOTPDevice.objects.filter(user=user, confirmed=True).count() <= 1
    assert not StaticDevice.objects.filter(user=user, name="backup").exists()
    assert not AuditLog.objects.filter(user=user, action=AuditLog.Action.BACKUP_CODES_GENERATED).exists()
    assert not AuditLog.objects.filter(user=user, action=AuditLog.Action.MFA_ENABLED).exists()


@pytest.mark.django_db
class TestSetupGateWithConfirmedTotp:
    """Bestandsfaktor TOTP, Sitzung nicht MFA-verifiziert."""

    def test_get_is_redirected_to_verify(self, client, staff_user):
        _confirmed_totp(staff_user)
        _login_password_only(client)

        response = client.get(SETUP_URL)

        assert response.status_code == 302
        assert response.url == VERIFY_URL
        _assert_no_setup_side_effects(client, staff_user)

    def test_post_with_valid_token_is_redirected_to_verify(self, client, staff_user):
        """(a) Direkter POST mit gueltigem Code fuer ein neues Geraet."""
        _confirmed_totp(staff_user)
        pending = _unconfirmed_totp(staff_user)
        _login_password_only(client)

        response = client.post(SETUP_URL, {"token": _valid_token(pending)})

        assert response.status_code == 302
        assert response.url == VERIFY_URL
        pending.refresh_from_db()
        assert pending.confirmed is False
        _assert_no_setup_side_effects(client, staff_user)

    def test_post_with_invalid_token_does_not_rebuild_setup_context(self, client, staff_user):
        """(b) Der Fehlerpfad darf keinen neuen Einrichtungskontext aufbauen."""
        _confirmed_totp(staff_user)
        _login_password_only(client)

        response = client.post(SETUP_URL, {"token": "000000"})

        assert response.status_code == 302
        assert response.url == VERIFY_URL
        assert TOTPDevice.objects.filter(user=staff_user, confirmed=False).count() == 0
        _assert_no_setup_side_effects(client, staff_user)

    def test_post_without_token_is_redirected_to_verify(self, client, staff_user):
        _confirmed_totp(staff_user)
        _login_password_only(client)

        response = client.post(SETUP_URL, {})

        assert response.status_code == 302
        assert response.url == VERIFY_URL
        assert TOTPDevice.objects.filter(user=staff_user, confirmed=False).count() == 0


@pytest.mark.django_db
class TestSetupGateWithConfirmedPasskeyOnly:
    """Bestandsfaktor nur Passkey/WebAuthn (ADR-032), Sitzung nicht MFA-verifiziert."""

    def test_get_is_redirected_to_verify(self, client, staff_user):
        _confirmed_passkey(staff_user)
        _login_password_only(client)

        response = client.get(SETUP_URL)

        assert response.status_code == 302
        assert response.url == VERIFY_URL
        assert TOTPDevice.objects.filter(user=staff_user).count() == 0

    def test_post_with_valid_token_is_redirected_to_verify(self, client, staff_user):
        _confirmed_passkey(staff_user)
        pending = _unconfirmed_totp(staff_user)
        _login_password_only(client)

        response = client.post(SETUP_URL, {"token": _valid_token(pending)})

        assert response.status_code == 302
        assert response.url == VERIFY_URL
        pending.refresh_from_db()
        assert pending.confirmed is False
        _assert_no_setup_side_effects(client, staff_user)


@pytest.mark.django_db
class TestSetupCompletionIsBoundToAuthorisedDevice:
    """(c) Der Setup-Abschluss haengt am konkret autorisierten Geraet.

    Refs #2328: Die Faelle laufen im Erstsetup (kein bestaetigter Faktor). Mit
    Bestandsfaktor erreicht eine nicht verifizierte Sitzung die Quittung gar
    nicht mehr — das ``dispatch``-Gate leitet vorher nach ``/mfa/verify/``
    (``test_mfa_backup_views_dispatch_gate.py``). Hier bleibt die
    Geraetebindung im Rumpf der View selbst unter Beobachtung.
    """

    @staticmethod
    def _seed_pending_backup_codes(client, codes):
        from core.services.file_vault import encrypt_field

        session = client.session
        session["mfa_backup_codes"] = encrypt_field(json.dumps(codes))
        session["mfa_setup_pending"] = True
        session.save()

    def test_ack_without_device_marker_does_not_confirm_any_device(self, client, staff_user):
        """Ohne Sitzungsmarker fuer das autorisierte Geraet kein Abschluss."""
        pending = _unconfirmed_totp(staff_user)
        _login_password_only(client)
        self._seed_pending_backup_codes(client, ["Aaaaaaaaaaaaaaaaaaaaaa"])

        response = client.post(BACKUP_CODES_URL, {"confirm_saved": "1"})

        assert response.status_code == 302
        assert response.url == SETUP_URL
        pending.refresh_from_db()
        assert pending.confirmed is False
        assert client.session.get("mfa_verified") is not True
        assert client.session.get("mfa_setup_pending") is not True
        assert not AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.MFA_ENABLED).exists()

    def test_ack_with_stale_device_marker_does_not_confirm_other_device(self, client, staff_user):
        """Der Marker zeigt auf ein verschwundenes Geraet — kein Ersatz-Geraet."""
        fremd = _unconfirmed_totp(staff_user, name="fremd")
        _login_password_only(client)
        self._seed_pending_backup_codes(client, ["Bbbbbbbbbbbbbbbbbbbbbb"])
        session = client.session
        session["mfa_setup_device_pk"] = fremd.pk + 1000
        session.save()

        response = client.post(BACKUP_CODES_URL, {"confirm_saved": "1"})

        assert response.status_code == 302
        fremd.refresh_from_db()
        assert fremd.confirmed is False
        assert client.session.get("mfa_verified") is not True
        assert not AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.MFA_ENABLED).exists()
