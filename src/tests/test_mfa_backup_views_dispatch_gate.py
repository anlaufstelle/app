"""Guard: die Backup-Code-Views verlangen bei bestehendem Faktor den MFA-Nachweis (Refs #2328).

``MFASetupView`` und ``MFASettingsView`` pruefen in ``dispatch`` fuer alle
HTTP-Methoden gemeinsam: bestaetigter zweiter Faktor (TOTP oder Passkey) ohne
``session["mfa_verified"]`` fuehrt vor jedem Kontextaufbau nach
``/mfa/verify/``. ``MFABackupCodesView`` und ``MFARegenerateBackupCodesView``
hatten dieses Gate nicht; ihre Sicherheit hing an Sitzungsmarkern und Mixins,
die an anderer Stelle gesetzt werden. Die MFA-Enforcement-Middleware greift
unter ``/mfa/`` nicht (``EXEMPT_URLS``).

Abgelehnt werden muss eine nur passwortauthentifizierte Sitzung in jeder
Methode: Weiterleitung nach ``/mfa/verify/``, keine Codes in der Antwort,
kein Abschluss einer Einrichtung, kein neuer Code-Satz, keine Audit-Zeile.

Gegenproben: das regulaere Erstsetup (Geraet noch unbestaetigt) erreicht die
Backup-Code-Seite und schaltet mit der Quittung scharf; eine verifizierte
Sitzung erzeugt neue Codes wie bisher; ``GET`` auf die Regenerate-Route bleibt
fuer eine verifizierte Sitzung ``405``.

Die Schreibpfade (Regenerieren schreibt ``BACKUP_CODES_REGENERATED``, die
Setup-Quittung ``MFA_ENABLED``) laufen zusaetzlich unter der
NOSUPERUSER/NOBYPASSRLS-Rolle — fuer ein Konto mit Einrichtung und fuer eines
ohne.
"""

from __future__ import annotations

import pytest
from django_otp.oath import totp as oath_totp
from django_otp.plugins.otp_static.models import StaticDevice

from core.models import AuditLog
from core.models import EncryptedTOTPDevice as TOTPDevice
from core.services.security import BACKUP_CODES_COUNT, generate_backup_codes
from tests.test_rls_functional import (  # noqa: F401
    as_rls_role,
    rls_test_role,
)

BACKUP_CODES_URL = "/mfa/backup-codes/"
REGENERATE_URL = "/mfa/backup-codes/regenerate/"
VERIFY_URL = "/mfa/verify/"
SETTINGS_URL = "/mfa/settings/"
SESSION_CODES = ["Aaaaaaaaaaaaaaaaaaaaa1", "Bbbbbbbbbbbbbbbbbbbbb2"]


def _valid_token(device: TOTPDevice) -> str:
    token = oath_totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits)
    return f"{token:0{device.digits}d}"


def _confirmed_passkey(user):
    from django_otp_webauthn.models import WebAuthnCredential

    return WebAuthnCredential.objects.create(
        user=user,
        name="Bestandsschlüssel",
        confirmed=True,
        credential_id=b"cred-2328",
        public_key=b"pubkey-bytes",
        aaguid="00000000-0000-0000-0000-000000000000",
        sign_count=0,
    )


@pytest.fixture(params=["totp", "totp+passkey"])
def bestandsfaktor(request, staff_user):
    """Bestaetigter Bestandsfaktor des Kontos: TOTP, oder Passkey neben TOTP."""
    device = TOTPDevice.objects.create(user=staff_user, name="bestand", confirmed=True)
    if request.param == "totp+passkey":
        _confirmed_passkey(staff_user)
    return device


def _password_only(client, user):
    """Passwortauthentifizierte Sitzung OHNE ``mfa_verified``."""
    client.force_login(user)
    assert client.session.get("mfa_verified") is not True


def _verified(client, user):
    client.force_login(user)
    session = client.session
    session["mfa_verified"] = True
    session.save()


def _seed_session(client, **values):
    session = client.session
    for key, value in values.items():
        session[key] = value
    session.save()


def _stored_backup_tokens(user) -> list[str]:
    device = StaticDevice.objects.filter(user=user, name="backup").first()
    if device is None:
        return []
    return sorted(device.token_set.values_list("token", flat=True))


def _audit_count(user, action) -> int:
    return AuditLog.objects.filter(user=user, action=action).count()


@pytest.mark.django_db
class TestBackupCodesViewGate:
    """``/mfa/backup-codes/`` — Anzeige und Quittung."""

    def test_get_with_codes_in_session_redirects_to_verify(self, client, staff_user, bestandsfaktor):
        _password_only(client, staff_user)
        _seed_session(client, mfa_backup_codes=SESSION_CODES)

        response = client.get(BACKUP_CODES_URL)

        assert response.status_code == 302
        assert response.url == VERIFY_URL
        body = response.content.decode()
        assert all(code not in body for code in SESSION_CODES)

    def test_get_without_codes_redirects_to_verify(self, client, staff_user, bestandsfaktor):
        _password_only(client, staff_user)

        response = client.get(BACKUP_CODES_URL)

        assert response.status_code == 302
        assert response.url == VERIFY_URL

    def test_post_ack_redirects_to_verify_without_completing_setup(self, client, staff_user, bestandsfaktor):
        pending = TOTPDevice.objects.create(user=staff_user, name="neu", confirmed=False)
        _password_only(client, staff_user)
        _seed_session(
            client,
            mfa_backup_codes=SESSION_CODES,
            mfa_setup_pending=True,
            mfa_setup_device_pk=pending.pk,
        )

        response = client.post(BACKUP_CODES_URL, {"confirm_saved": "1"})

        assert response.status_code == 302
        assert response.url == VERIFY_URL
        pending.refresh_from_db()
        assert pending.confirmed is False
        assert client.session.get("mfa_verified") is not True
        # Kein Abbau der Marker: die View wurde gar nicht betreten.
        assert client.session.get("mfa_setup_pending") is True
        assert _audit_count(staff_user, AuditLog.Action.MFA_ENABLED) == 0

    def test_post_without_ack_redirects_to_verify_without_rendering_codes(self, client, staff_user, bestandsfaktor):
        _password_only(client, staff_user)
        _seed_session(client, mfa_backup_codes=SESSION_CODES)

        response = client.post(BACKUP_CODES_URL, {})

        assert response.status_code == 302
        assert response.url == VERIFY_URL
        body = response.content.decode()
        assert all(code not in body for code in SESSION_CODES)


@pytest.mark.django_db
class TestRegenerateViewGate:
    """``/mfa/backup-codes/regenerate/`` — neuer Code-Satz."""

    def test_post_with_valid_token_redirects_to_verify(self, client, staff_user, bestandsfaktor):
        generate_backup_codes(staff_user)
        vorher = _stored_backup_tokens(staff_user)
        _password_only(client, staff_user)

        response = client.post(REGENERATE_URL, {"token": _valid_token(bestandsfaktor)})

        assert response.status_code == 302
        assert response.url == VERIFY_URL
        assert _stored_backup_tokens(staff_user) == vorher
        assert client.session.get("mfa_backup_codes") is None
        assert _audit_count(staff_user, AuditLog.Action.BACKUP_CODES_REGENERATED) == 0

    def test_post_without_token_redirects_to_verify(self, client, staff_user, bestandsfaktor):
        _password_only(client, staff_user)

        response = client.post(REGENERATE_URL, {})

        assert response.status_code == 302
        assert response.url == VERIFY_URL

    def test_get_redirects_to_verify(self, client, staff_user, bestandsfaktor):
        _password_only(client, staff_user)

        response = client.get(REGENERATE_URL)

        assert response.status_code == 302
        assert response.url == VERIFY_URL


@pytest.mark.django_db
class TestRegularPathsUnchanged:
    """Gegenproben: das Gate trifft nur den nicht verifizierten Bestandsfaktor."""

    def test_first_setup_reaches_backup_codes_and_ack_confirms(self, client, staff_user):
        pending = TOTPDevice.objects.create(user=staff_user, name="neu", confirmed=False)
        _password_only(client, staff_user)
        _seed_session(
            client,
            mfa_backup_codes=SESSION_CODES,
            mfa_setup_pending=True,
            mfa_setup_device_pk=pending.pk,
        )

        shown = client.get(BACKUP_CODES_URL)
        assert shown.status_code == 200
        assert all(code in shown.content.decode() for code in SESSION_CODES)

        response = client.post(BACKUP_CODES_URL, {"confirm_saved": "1"})

        assert response.status_code == 302
        assert response.url == SETTINGS_URL
        pending.refresh_from_db()
        assert pending.confirmed is True
        assert client.session.get("mfa_verified") is True
        assert _audit_count(staff_user, AuditLog.Action.MFA_ENABLED) == 1

    def test_verified_session_regenerates_codes(self, client, staff_user, bestandsfaktor):
        generate_backup_codes(staff_user)
        vorher = _stored_backup_tokens(staff_user)
        _verified(client, staff_user)

        response = client.post(REGENERATE_URL, {"token": _valid_token(bestandsfaktor)})

        assert response.status_code == 302
        assert response.url == BACKUP_CODES_URL
        nachher = _stored_backup_tokens(staff_user)
        assert len(nachher) == BACKUP_CODES_COUNT
        assert set(nachher).isdisjoint(vorher)
        assert _audit_count(staff_user, AuditLog.Action.BACKUP_CODES_REGENERATED) == 1

        shown = client.get(BACKUP_CODES_URL)
        assert shown.status_code == 200
        acked = client.post(BACKUP_CODES_URL, {"confirm_saved": "1"})
        assert acked.status_code == 302
        assert acked.url == SETTINGS_URL
        assert client.session.get("mfa_backup_codes") is None

    def test_verified_session_get_on_regenerate_stays_405(self, client, staff_user, bestandsfaktor):
        _verified(client, staff_user)

        response = client.get(REGENERATE_URL)

        assert response.status_code == 405


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
class TestBackupCodeWritesUnderProdRole:
    """Die Audit-Schreibpfade laufen unter NOSUPERUSER/NOBYPASSRLS."""

    def test_regenerate_writes_audit_with_account_facility(
        self,
        client,
        staff_user,
        rls_test_role,  # noqa: F811
    ):
        device = TOTPDevice.objects.create(user=staff_user, name="bestand", confirmed=True)
        generate_backup_codes(staff_user)
        _verified(client, staff_user)

        with as_rls_role(rls_test_role, facility_id=staff_user.facility_id):
            response = client.post(REGENERATE_URL, {"token": _valid_token(device)})

        assert response.status_code == 302
        assert response.url == BACKUP_CODES_URL
        entry = AuditLog.objects.get(user=staff_user, action=AuditLog.Action.BACKUP_CODES_REGENERATED)
        assert entry.facility_id == staff_user.facility_id

    def test_regenerate_writes_audit_for_account_without_facility(
        self,
        client,
        super_admin_user,
        rls_test_role,  # noqa: F811
    ):
        device = TOTPDevice.objects.create(user=super_admin_user, name="bestand", confirmed=True)
        generate_backup_codes(super_admin_user)
        _verified(client, super_admin_user)

        with as_rls_role(rls_test_role, facility_id=""):
            response = client.post(REGENERATE_URL, {"token": _valid_token(device)})

        assert response.status_code == 302
        assert response.url == BACKUP_CODES_URL
        entry = AuditLog.objects.get(user=super_admin_user, action=AuditLog.Action.BACKUP_CODES_REGENERATED)
        assert entry.facility_id is None

    def test_first_setup_ack_writes_audit_with_account_facility(
        self,
        client,
        staff_user,
        rls_test_role,  # noqa: F811
    ):
        pending = TOTPDevice.objects.create(user=staff_user, name="neu", confirmed=False)
        _password_only(client, staff_user)
        _seed_session(
            client,
            mfa_backup_codes=SESSION_CODES,
            mfa_setup_pending=True,
            mfa_setup_device_pk=pending.pk,
        )

        with as_rls_role(rls_test_role, facility_id=staff_user.facility_id):
            response = client.post(BACKUP_CODES_URL, {"confirm_saved": "1"})

        assert response.status_code == 302
        assert response.url == SETTINGS_URL
        entry = AuditLog.objects.get(user=staff_user, action=AuditLog.Action.MFA_ENABLED)
        assert entry.facility_id == staff_user.facility_id
