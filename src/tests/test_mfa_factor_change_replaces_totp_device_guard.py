"""Guard: ein abgeschlossener Faktorwechsel entfernt das ersetzte TOTP-Geraet (Refs #2328).

Ein Faktorwechsel (erneutes ``/mfa/setup/`` bei bestehendem Faktor) richtet ein
neues TOTP-Geraet ein; die Quittung der neuen Backup-Codes schaltet es scharf.
Ab dann gilt nur noch sein Code. Das ersetzte Geraet blieb bisher als
bestaetigte Zeile stehen — ungeprueft, aber mit seinem Secret im Bestand und
als zweites "bestaetigtes Geraet" des Kontos.

Vertrag:

* In dem Schritt, in dem das neue Geraet wirksam wird (Quittung), und in
  derselben Transaktion werden alle anderen bestaetigten TOTP-Geraete des
  Kontos entfernt. Danach hat das Konto genau EIN bestaetigtes TOTP-Geraet.
* Der Code des alten Geraets wird an jeder Pruefstelle abgewiesen: Anmeldung
  (``/mfa/verify/``), Sudo (``/sudo/``) und Neuerzeugen der Backup-Codes.
* Passkeys und die Geraete anderer Konten bleiben unberuehrt.
* Ein abgebrochener Wechsel (neues Geraet nie quittiert) laesst das bisherige
  Geraet bestehen und gueltig.
* Das Audit der Quittung (``MFA_ENABLED``) nennt die Zahl der entfernten
  Geraete unter ``count`` — 0 bei der erstmaligen Einrichtung.
"""

from __future__ import annotations

import time

import pytest
from django_otp.oath import TOTP
from django_otp.oath import totp as oath_totp

from core.models import AuditLog
from core.models import EncryptedTOTPDevice as TOTPDevice
from core.services.security import generate_backup_codes, verify_totp_or_backup

VERIFY_URL = "/mfa/verify/"
SUDO_URL = "/sudo/"
SETUP_URL = "/mfa/setup/"
BACKUP_CODES_URL = "/mfa/backup-codes/"
REGENERATE_URL = "/mfa/backup-codes/regenerate/"
PASSWORD = "testpass123"


def _code(device: TOTPDevice, *, steps_ahead: int = 0) -> str:
    token = oath_totp(
        device.bin_key,
        step=device.step,
        t0=device.t0 - steps_ahead * device.step,
        digits=device.digits,
    )
    return f"{token:0{device.digits}d}"


def _next_window_code(device: TOTPDevice) -> str:
    """Code for the window right after the device's last accepted one.

    Relative to the device, not the clock: a confirmation posted across a window
    boundary stores a drift of -1, and "now + 1" would then fall outside the tolerance.
    """
    device = TOTPDevice.objects.get(pk=device.pk)
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits)
    totp.time = device.t0 + (device.last_t + 1) * device.step
    return f"{totp.token():0{device.digits}d}"


def _verified_with_sudo(client, user) -> None:
    client.force_login(user)
    session = client.session
    session["mfa_verified"] = True
    session["sudo_until"] = int(time.time()) + 600
    session.save()


def _password_only(client, user) -> None:
    client.logout()
    client.force_login(user)


def _start_change(client, user) -> TOTPDevice:
    """Setup-Seite oeffnen und das neue Geraet mit seinem Code bestaetigen (vor der Quittung)."""
    client.post(SETUP_URL, {"token": ""})
    neu = TOTPDevice.objects.get(user=user, confirmed=False)
    response = client.post(SETUP_URL, {"token": _code(neu)})
    assert response.url == BACKUP_CODES_URL
    return neu


def _complete_change(client, user) -> TOTPDevice:
    neu = _start_change(client, user)
    response = client.post(BACKUP_CODES_URL, {"confirm_saved": "1"})
    assert response.status_code == 302
    neu.refresh_from_db()
    assert neu.confirmed is True
    return neu


@pytest.fixture
def sudo_an(settings):
    settings.SUDO_MODE_ENABLED = True


@pytest.fixture
def alt(staff_user):
    device = TOTPDevice.objects.create(user=staff_user, name="alt", confirmed=True, key="a" * 40)
    generate_backup_codes(staff_user)
    return device


def _confirmed(user):
    return list(TOTPDevice.objects.filter(user=user, confirmed=True).values_list("pk", flat=True))


@pytest.mark.django_db
@pytest.mark.usefixtures("sudo_an")
class TestCompletedChangeLeavesOneDevice:
    def test_only_the_new_device_remains(self, client, staff_user, alt):
        _verified_with_sudo(client, staff_user)
        neu = _complete_change(client, staff_user)
        assert _confirmed(staff_user) == [neu.pk]
        assert not TOTPDevice.objects.filter(pk=alt.pk).exists()

    def test_every_earlier_confirmed_device_is_removed(self, client, staff_user, alt):
        zweites = TOTPDevice.objects.create(user=staff_user, name="zweites", confirmed=True, key="c" * 40)
        _verified_with_sudo(client, staff_user)
        neu = _complete_change(client, staff_user)
        assert _confirmed(staff_user) == [neu.pk]
        assert not TOTPDevice.objects.filter(pk__in=[alt.pk, zweites.pk]).exists()

    def test_devices_of_other_accounts_stay(self, client, staff_user, alt, lead_user):
        fremd = TOTPDevice.objects.create(user=lead_user, name="fremd", confirmed=True, key="d" * 40)
        _verified_with_sudo(client, staff_user)
        _complete_change(client, staff_user)
        assert TOTPDevice.objects.filter(pk=fremd.pk, confirmed=True).exists()

    def test_passkeys_stay(self, client, staff_user, alt):
        from django_otp_webauthn.models import WebAuthnCredential

        passkey = WebAuthnCredential.objects.create(
            user=staff_user,
            name="Bestandsschluessel",
            confirmed=True,
            credential_id=b"cred-2328-replace",
            public_key=b"pubkey-bytes",
            aaguid="00000000-0000-0000-0000-000000000000",
            sign_count=0,
        )
        _verified_with_sudo(client, staff_user)
        _complete_change(client, staff_user)
        assert WebAuthnCredential.objects.filter(pk=passkey.pk, confirmed=True).exists()

    def test_audit_names_the_removed_devices(self, client, staff_user, alt):
        _verified_with_sudo(client, staff_user)
        _complete_change(client, staff_user)
        entries = AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.MFA_ENABLED)
        assert [entry.detail for entry in entries] == [{"event": "mfa_setup_confirmed", "count": 1}]


@pytest.mark.django_db
@pytest.mark.usefixtures("sudo_an")
class TestOldCodeIsRefusedEverywhere:
    """Nach dem Wechsel: der Code des alten Geraets oeffnet keine Pruefstelle mehr."""

    @pytest.fixture
    def gewechselt(self, client, staff_user, alt):
        _verified_with_sudo(client, staff_user)
        return _complete_change(client, staff_user)

    def test_login_verify(self, client, staff_user, alt, gewechselt):
        _password_only(client, staff_user)
        response = client.post(VERIFY_URL, {"token": _code(alt)})
        assert response.status_code == 200
        assert client.session.get("mfa_verified") is not True

    def test_sudo(self, client, staff_user, alt, gewechselt):
        _password_only(client, staff_user)
        session = client.session
        session["mfa_verified"] = True
        session.save()
        response = client.post(SUDO_URL, {"password": PASSWORD, "otp_token": _code(alt), "next": "/"})
        assert response.status_code == 403
        assert "sudo_until" not in client.session

    def test_regenerate_backup_codes(self, client, staff_user, alt, gewechselt):
        _password_only(client, staff_user)
        session = client.session
        session["mfa_verified"] = True
        session.save()
        response = client.post(REGENERATE_URL, {"token": _code(alt)})
        assert response.url == "/mfa/settings/"

    def test_service(self, staff_user, alt, gewechselt):
        assert verify_totp_or_backup(staff_user, _code(alt)) is False

    def test_new_device_signs_in(self, client, staff_user, gewechselt):
        _password_only(client, staff_user)
        # Der Setup-Code hat das aktuelle Zeitfenster verbraucht; das naechste liegt in der Toleranz.
        response = client.post(VERIFY_URL, {"token": _next_window_code(gewechselt)})
        assert response.status_code == 302
        assert client.session.get("mfa_verified") is True


@pytest.mark.django_db
@pytest.mark.usefixtures("sudo_an")
class TestAbortedChangeChangesNothing:
    def test_new_device_never_confirmed_keeps_the_old_one(self, client, staff_user, alt):
        _verified_with_sudo(client, staff_user)
        neu = _start_change(client, staff_user)
        # Abbruch: die Quittung der neuen Backup-Codes bleibt aus.
        assert _confirmed(staff_user) == [alt.pk]
        assert TOTPDevice.objects.filter(pk=neu.pk, confirmed=False).exists()

        _password_only(client, staff_user)
        response = client.post(VERIFY_URL, {"token": _code(alt)})
        assert response.status_code == 302
        assert client.session.get("mfa_verified") is True

    def test_setup_page_opened_but_nothing_entered(self, client, staff_user, alt):
        _verified_with_sudo(client, staff_user)
        client.post(SETUP_URL, {"token": ""})
        assert _confirmed(staff_user) == [alt.pk]
        assert verify_totp_or_backup(staff_user, _code(alt)) is True

    def test_quittung_without_confirmation_keeps_the_old_one(self, client, staff_user, alt):
        _verified_with_sudo(client, staff_user)
        _start_change(client, staff_user)
        response = client.post(BACKUP_CODES_URL, {})
        assert response.status_code == 200
        assert _confirmed(staff_user) == [alt.pk]


@pytest.mark.django_db
class TestFirstSetupRemovesNothing:
    def test_first_setup_audit_detail(self, client, staff_user):
        client.force_login(staff_user)
        neu = _complete_change(client, staff_user)
        assert _confirmed(staff_user) == [neu.pk]
        entries = AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.MFA_ENABLED)
        assert [entry.detail for entry in entries] == [{"event": "mfa_setup_confirmed", "count": 0}]
