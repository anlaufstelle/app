"""Guard: ein Faktorwechsel verlangt Sudo, das Erstsetup nicht (Refs #2328).

Eine MFA-verifizierte Sitzung konnte per ``POST /mfa/setup/`` ohne erneute
Passwortbestaetigung ein weiteres TOTP-Geraet anlegen und dabei den
Backup-Code-Satz ersetzen — waehrend das Abschalten der MFA
(``MFADisableView``) und das Loeschen eines Passkeys
(``MFAPasskeyDeleteView``) Sudo verlangen. Ein gestohlenes, bereits
verifiziertes Sitzungs-Cookie reichte also, um dem Konto einen eigenen
zweiten Faktor unterzuschieben.

Vertrag: besteht ein bestaetigter Faktor (TOTP oder Passkey, ADR-032), fuehrt
``/mfa/setup/`` in jeder Methode ohne Sudo auf die Sudo-Seite, ``next`` zeigt
zurueck auf ``/mfa/setup/``. Kein neues Geraet, kein QR-Code, kein neuer
Code-Satz, keine Audit-Zeile. Mit Sudo laeuft der Wechsel wie bisher. Das
Erstsetup (kein bestaetigter Faktor) verlangt nie Sudo. Die Reihenfolge der
Schranken bleibt: erst der MFA-Nachweis (``/mfa/verify/``), dann Sudo.

``settings/test.py`` schaltet Sudo ab — jede Klasse hier schaltet es per
Settings-Override (Fixture ``sudo_an``) wieder ein, sonst waere der Guard
trivial gruen.
"""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlsplit

import pytest
from django_otp.oath import totp as oath_totp
from django_otp.plugins.otp_static.models import StaticDevice

from core.models import AuditLog
from core.models import EncryptedTOTPDevice as TOTPDevice
from core.services.security import generate_backup_codes
from tests.test_rls_functional import (  # noqa: F401
    as_rls_role,
    rls_test_role,
)

SETUP_URL = "/mfa/setup/"
BACKUP_CODES_URL = "/mfa/backup-codes/"
SETTINGS_URL = "/mfa/settings/"
VERIFY_URL = "/mfa/verify/"
SUDO_URL = "/sudo/"
SETUP_SESSION_KEYS = ("mfa_backup_codes", "mfa_setup_pending", "mfa_setup_device_pk")


@pytest.fixture
def sudo_an(settings):
    settings.SUDO_MODE_ENABLED = True


@pytest.fixture
def sudo_aus(settings):
    settings.SUDO_MODE_ENABLED = False


def _valid_token(device: TOTPDevice) -> str:
    token = oath_totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits)
    return f"{token:0{device.digits}d}"


def _confirmed_passkey(user):
    from django_otp_webauthn.models import WebAuthnCredential

    return WebAuthnCredential.objects.create(
        user=user,
        name="Bestandsschlüssel",
        confirmed=True,
        credential_id=b"cred-2328-sudo",
        public_key=b"pubkey-bytes",
        aaguid="00000000-0000-0000-0000-000000000000",
        sign_count=0,
    )


@pytest.fixture(params=["totp", "totp+passkey"])
def bestandsfaktor(request, staff_user):
    """Bestaetigter Bestandsfaktor samt Backup-Code-Satz."""
    device = TOTPDevice.objects.create(user=staff_user, name="bestand", confirmed=True)
    if request.param == "totp+passkey":
        _confirmed_passkey(staff_user)
    generate_backup_codes(staff_user)
    return device


def _verified(client, user):
    client.force_login(user)
    session = client.session
    session["mfa_verified"] = True
    session.save()


def _in_sudo(client):
    session = client.session
    session["sudo_until"] = int(time.time()) + 600
    session.save()


def _stored_backup_tokens(user) -> list[str]:
    device = StaticDevice.objects.filter(user=user, name="backup").first()
    if device is None:
        return []
    return sorted(device.token_set.values_list("token", flat=True))


def _audit_count(user, action) -> int:
    return AuditLog.objects.filter(user=user, action=action).count()


def _assert_sudo_redirect(response):
    assert response.status_code == 302
    target = urlsplit(response.url)
    assert target.path == SUDO_URL
    assert parse_qs(target.query).get("next") == [SETUP_URL]


def _assert_no_setup_state(client):
    for key in SETUP_SESSION_KEYS:
        assert key not in client.session, key


@pytest.mark.django_db
@pytest.mark.usefixtures("sudo_an")
class TestFactorChangeWithoutSudoIsRefused:
    """Verifizierte Sitzung, bestaetigter Faktor, kein Sudo."""

    def test_post_with_valid_code_redirects_to_sudo(self, client, staff_user, bestandsfaktor):
        neu = TOTPDevice.objects.create(user=staff_user, name="neu", confirmed=False)
        codes_vorher = _stored_backup_tokens(staff_user)
        _verified(client, staff_user)

        response = client.post(SETUP_URL, {"token": _valid_token(neu)})

        _assert_sudo_redirect(response)
        neu.refresh_from_db()
        assert neu.confirmed is False
        assert _stored_backup_tokens(staff_user) == codes_vorher
        assert _audit_count(staff_user, AuditLog.Action.BACKUP_CODES_GENERATED) == 0
        _assert_no_setup_state(client)

    def test_post_with_invalid_code_creates_no_device(self, client, staff_user, bestandsfaktor):
        """Ohne Sudo wird auch kein neues Geraet samt Secret gerendert."""
        _verified(client, staff_user)

        response = client.post(SETUP_URL, {"token": "000000"})

        _assert_sudo_redirect(response)
        assert TOTPDevice.objects.filter(user=staff_user, confirmed=False).count() == 0
        assert b'data-testid="mfa-secret"' not in response.content

    def test_get_redirects_to_sudo(self, client, staff_user, bestandsfaktor):
        _verified(client, staff_user)

        response = client.get(SETUP_URL)

        _assert_sudo_redirect(response)
        assert TOTPDevice.objects.filter(user=staff_user, confirmed=False).count() == 0

    def test_passkey_only_account_get_redirects_to_sudo(self, client, staff_user):
        """Einziger Einstieg der Oberflaeche: Passkey ohne TOTP sieht das Setup."""
        _confirmed_passkey(staff_user)
        _verified(client, staff_user)

        response = client.get(SETUP_URL)

        _assert_sudo_redirect(response)
        assert TOTPDevice.objects.filter(user=staff_user).count() == 0


@pytest.mark.django_db
@pytest.mark.usefixtures("sudo_an")
class TestFactorChangeWithSudo:
    """Mit Sudo laeuft der Wechsel wie bisher bis zur Quittung."""

    def test_post_with_valid_code_replaces_codes_and_ack_confirms_device(self, client, staff_user, bestandsfaktor):
        neu = TOTPDevice.objects.create(user=staff_user, name="neu", confirmed=False)
        codes_vorher = _stored_backup_tokens(staff_user)
        _verified(client, staff_user)
        _in_sudo(client)

        response = client.post(SETUP_URL, {"token": _valid_token(neu)})

        assert response.status_code == 302
        assert response.url == BACKUP_CODES_URL
        assert _stored_backup_tokens(staff_user) != codes_vorher
        assert _audit_count(staff_user, AuditLog.Action.BACKUP_CODES_GENERATED) == 1

        ack = client.post(BACKUP_CODES_URL, {"confirm_saved": "1"})

        assert ack.status_code == 302
        assert ack.url == SETTINGS_URL
        neu.refresh_from_db()
        assert neu.confirmed is True
        assert _audit_count(staff_user, AuditLog.Action.MFA_ENABLED) == 1

    def test_passkey_only_account_get_renders_setup(self, client, staff_user):
        _confirmed_passkey(staff_user)
        _verified(client, staff_user)
        _in_sudo(client)

        response = client.get(SETUP_URL)

        assert response.status_code == 200
        assert response.context["qr_data_url"]
        assert TOTPDevice.objects.filter(user=staff_user, confirmed=False).count() == 1

    def test_totp_account_get_leads_to_settings(self, client, staff_user, bestandsfaktor):
        """Ziel nach der Sudo-Bestaetigung fuer ein TOTP-Konto: die Einstellungen."""
        _verified(client, staff_user)
        _in_sudo(client)

        response = client.get(SETUP_URL)

        assert response.status_code == 302
        assert response.url == SETTINGS_URL


@pytest.mark.django_db
@pytest.mark.usefixtures("sudo_an")
class TestGateOrder:
    """Erst der MFA-Nachweis, dann Sudo — eine nicht verifizierte Sitzung sieht nie die Sudo-Seite."""

    @pytest.mark.parametrize("method", ["get", "post"])
    def test_unverified_session_goes_to_verify(self, client, staff_user, bestandsfaktor, method):
        client.force_login(staff_user)

        response = getattr(client, method)(SETUP_URL)

        assert response.status_code == 302
        assert response.url == VERIFY_URL


@pytest.mark.django_db
@pytest.mark.usefixtures("sudo_an")
class TestFirstSetupStaysSudoFree:
    """Ohne bestaetigten Faktor ist ``/mfa/setup/`` die Einrichtung selbst."""

    def test_get_renders_setup_without_sudo(self, client, staff_user):
        client.force_login(staff_user)

        response = client.get(SETUP_URL)

        assert response.status_code == 200
        assert response.context["qr_data_url"]

    def test_post_and_ack_complete_setup_without_sudo(self, client, staff_user):
        client.force_login(staff_user)
        client.get(SETUP_URL)
        device = TOTPDevice.objects.get(user=staff_user, confirmed=False)

        response = client.post(SETUP_URL, {"token": _valid_token(device)})

        assert response.status_code == 302
        assert response.url == BACKUP_CODES_URL
        ack = client.post(BACKUP_CODES_URL, {"confirm_saved": "1"})
        assert ack.status_code == 302
        assert ack.url == SETTINGS_URL
        device.refresh_from_db()
        assert device.confirmed is True

    def test_leftover_unconfirmed_device_needs_no_sudo(self, client, staff_user):
        """Ein abgebrochenes Erstsetup ist kein bestaetigter Faktor."""
        TOTPDevice.objects.create(user=staff_user, name="abgebrochen", confirmed=False)
        client.force_login(staff_user)

        response = client.get(SETUP_URL)

        assert response.status_code == 200


@pytest.mark.django_db
@pytest.mark.usefixtures("sudo_aus")
class TestSudoToggleOff:
    """Ist Sudo per Setting abgeschaltet, gilt dasselbe wie beim Sudo-Mixin: kein Sudo-Schritt."""

    def test_factor_change_without_sudo_session_proceeds(self, client, staff_user, bestandsfaktor):
        neu = TOTPDevice.objects.create(user=staff_user, name="neu", confirmed=False)
        _verified(client, staff_user)

        response = client.post(SETUP_URL, {"token": _valid_token(neu)})

        assert response.status_code == 302
        assert response.url == BACKUP_CODES_URL


@pytest.mark.django_db
@pytest.mark.usefixtures("sudo_an")
class TestFactorChangeUnderRlsRole:
    """Der Schreibpfad des Wechsels (``BACKUP_CODES_GENERATED``) unter NOSUPERUSER/NOBYPASSRLS."""

    def test_factor_change_writes_audit_with_account_facility(
        self,
        client,
        staff_user,
        rls_test_role,  # noqa: F811
    ):
        TOTPDevice.objects.create(user=staff_user, name="bestand", confirmed=True)
        generate_backup_codes(staff_user)
        neu = TOTPDevice.objects.create(user=staff_user, name="neu", confirmed=False)
        _verified(client, staff_user)
        _in_sudo(client)

        with as_rls_role(rls_test_role, facility_id=staff_user.facility_id):
            response = client.post(SETUP_URL, {"token": _valid_token(neu)})

        assert response.status_code == 302
        assert response.url == BACKUP_CODES_URL
        entry = AuditLog.objects.get(user=staff_user, action=AuditLog.Action.BACKUP_CODES_GENERATED)
        assert entry.facility_id == staff_user.facility_id

    def test_factor_change_writes_audit_for_account_without_facility(
        self,
        client,
        super_admin_user,
        rls_test_role,  # noqa: F811
    ):
        TOTPDevice.objects.create(user=super_admin_user, name="bestand", confirmed=True)
        generate_backup_codes(super_admin_user)
        neu = TOTPDevice.objects.create(user=super_admin_user, name="neu", confirmed=False)
        _verified(client, super_admin_user)
        _in_sudo(client)

        with as_rls_role(rls_test_role, facility_id=""):
            response = client.post(SETUP_URL, {"token": _valid_token(neu)})

        assert response.status_code == 302
        assert response.url == BACKUP_CODES_URL
        entry = AuditLog.objects.get(user=super_admin_user, action=AuditLog.Action.BACKUP_CODES_GENERATED)
        assert entry.facility_id is None
