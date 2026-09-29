"""Guard: nach einem Faktorwechsel gilt der Code des neuen TOTP-Geraets (Refs #2328).

Ein Faktorwechsel (erneutes ``/mfa/setup/`` bei bestehendem Faktor) richtet
ein neues TOTP-Geraet ein und bestaetigt es mit der Quittung der neuen
Backup-Codes. Anmeldung (``/mfa/verify/``), Sudo (``verify_totp_or_backup``)
und das Neuerzeugen der Backup-Codes waehlten das zu pruefende Geraet aber per
``.first()`` ohne Ordering — also das aelteste. Der Code des neuen Geraets
wurde nie geprueft; der Wechsel ersetzte faktisch nur die Backup-Codes, und
das alte Geraet blieb allein gueltig.

Vertrag: geprueft wird genau ein Geraet, das zuletzt eingerichtete bestaetigte.
Der Code des neuen Geraets gilt, der des ersetzten nicht mehr — wie beim
Backup-Code-Satz, den derselbe Wechsel ersetzt. Genau ein Geraet, weil
django-otp von der Pruefung gegen alle Geraete abraet: jeder Fehlschlag
drosselt dann jedes Geraet.
"""

from __future__ import annotations

import time

import pytest
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.oath import totp as oath_totp

from core.models import EncryptedTOTPDevice as TOTPDevice
from core.services.security import generate_backup_codes, verify_totp_or_backup

VERIFY_URL = "/mfa/verify/"
SUDO_URL = "/sudo/"
REGENERATE_URL = "/mfa/backup-codes/regenerate/"
PASSWORD = "testpass123"


def _code(device: TOTPDevice, *, steps_ahead: int = 0) -> str:
    """TOTP-Code des Geraets; ``steps_ahead`` fuer das naechste Zeitfenster (Toleranz 1)."""
    token = oath_totp(
        device.bin_key,
        step=device.step,
        t0=device.t0 - steps_ahead * device.step,
        digits=device.digits,
    )
    return f"{token:0{device.digits}d}"


@pytest.fixture
def geraete(staff_user):
    """Bestandsgeraet und das per Faktorwechsel spaeter eingerichtete Geraet."""
    alt = TOTPDevice.objects.create(user=staff_user, name="alt", confirmed=True, key="a" * 40)
    neu = TOTPDevice.objects.create(user=staff_user, name="neu", confirmed=True, key="b" * 40)
    assert alt.pk < neu.pk
    assert _code(alt) != _code(neu)
    generate_backup_codes(staff_user)
    return alt, neu


def _password_only(client, user):
    client.force_login(user)


def _verified(client, user):
    client.force_login(user)
    session = client.session
    session["mfa_verified"] = True
    session.save()


@pytest.mark.django_db
class TestLoginVerifyChecksTheNewDevice:
    def test_code_of_the_new_device_verifies(self, client, staff_user, geraete):
        _alt, neu = geraete
        _password_only(client, staff_user)
        response = client.post(VERIFY_URL, {"token": _code(neu)})
        assert response.status_code == 302
        assert client.session.get("mfa_verified") is True
        assert client.session[DEVICE_ID_SESSION_KEY] == neu.persistent_id

    def test_code_of_the_replaced_device_is_rejected(self, client, staff_user, geraete):
        alt, _neu = geraete
        _password_only(client, staff_user)
        response = client.post(VERIFY_URL, {"token": _code(alt)})
        assert response.status_code == 200
        assert client.session.get("mfa_verified") is not True

    def test_backup_code_login_binds_the_new_device(self, client, staff_user, geraete):
        _alt, neu = geraete
        code = generate_backup_codes(staff_user)[0]
        _password_only(client, staff_user)
        response = client.post(VERIFY_URL, {"token": code, "mode": "backup"})
        assert response.status_code == 302
        assert client.session[DEVICE_ID_SESSION_KEY] == neu.persistent_id

    def test_replaced_device_is_not_throttled_by_the_new_code(self, client, staff_user, geraete):
        alt, neu = geraete
        _password_only(client, staff_user)
        client.post(VERIFY_URL, {"token": _code(neu)})
        alt.refresh_from_db()
        assert alt.throttling_failure_count == 0


@pytest.mark.django_db
class TestSudoChecksTheNewDevice:
    def test_service_accepts_the_new_device(self, staff_user, geraete):
        _alt, neu = geraete
        assert verify_totp_or_backup(staff_user, _code(neu)) is True

    def test_service_rejects_the_replaced_device(self, staff_user, geraete):
        alt, _neu = geraete
        assert verify_totp_or_backup(staff_user, _code(alt)) is False

    def test_sudo_page_grants_with_the_new_code(self, client, staff_user, geraete, settings):
        settings.SUDO_MODE_ENABLED = True
        _alt, neu = geraete
        _verified(client, staff_user)
        response = client.post(SUDO_URL, {"password": PASSWORD, "otp_token": _code(neu), "next": "/"})
        assert response.status_code == 302
        assert isinstance(client.session.get("sudo_until"), int)
        assert client.session["sudo_until"] > time.time()

    def test_sudo_page_refuses_the_replaced_code(self, client, staff_user, geraete, settings):
        settings.SUDO_MODE_ENABLED = True
        alt, _neu = geraete
        _verified(client, staff_user)
        response = client.post(SUDO_URL, {"password": PASSWORD, "otp_token": _code(alt), "next": "/"})
        assert response.status_code == 403
        assert "sudo_until" not in client.session


@pytest.mark.django_db
class TestRegenerateChecksTheNewDevice:
    def test_code_of_the_new_device_regenerates(self, client, staff_user, geraete):
        _alt, neu = geraete
        _verified(client, staff_user)
        response = client.post(REGENERATE_URL, {"token": _code(neu)})
        assert response.status_code == 302
        assert response.url == "/mfa/backup-codes/"

    def test_code_of_the_replaced_device_is_refused(self, client, staff_user, geraete):
        alt, _neu = geraete
        _verified(client, staff_user)
        response = client.post(REGENERATE_URL, {"token": _code(alt)})
        assert response.status_code == 302
        assert response.url == "/mfa/settings/"


@pytest.mark.django_db
class TestFactorChangeFlowEndToEnd:
    """Der ganze Weg im Test-Client: Wechsel mit Sudo, Quittung, neue Anmeldung."""

    def test_after_the_change_the_new_device_signs_in(self, client, staff_user, settings):
        settings.SUDO_MODE_ENABLED = True
        alt = TOTPDevice.objects.create(user=staff_user, name="alt", confirmed=True, key="a" * 40)
        generate_backup_codes(staff_user)
        _verified(client, staff_user)
        session = client.session
        session["sudo_until"] = int(time.time()) + 600
        session.save()

        client.post("/mfa/setup/", {"token": ""})
        neu = TOTPDevice.objects.get(user=staff_user, confirmed=False)
        assert client.post("/mfa/setup/", {"token": _code(neu)}).url == "/mfa/backup-codes/"
        assert client.post("/mfa/backup-codes/", {"confirm_saved": "1"}).status_code == 302
        neu.refresh_from_db()
        assert neu.confirmed is True

        client.logout()
        _password_only(client, staff_user)
        # Der Setup-Code hat das aktuelle Zeitfenster verbraucht; das naechste liegt in der Toleranz.
        response = client.post(VERIFY_URL, {"token": _code(neu, steps_ahead=1)})
        assert response.status_code == 302
        assert client.session.get("mfa_verified") is True

        client.logout()
        _password_only(client, staff_user)
        response = client.post(VERIFY_URL, {"token": _code(alt)})
        assert response.status_code == 200
        assert client.session.get("mfa_verified") is not True
