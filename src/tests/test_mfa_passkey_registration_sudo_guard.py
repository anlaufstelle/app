"""Guard: einen Passkey hinzufuegen verlangt Sudo, wenn schon ein Faktor besteht (Refs #2328).

Ein Passkey ist ein weiterer, dauerhafter zweiter Faktor. Die Registrierung
(``/webauthn/registration/begin/`` und ``/complete/``) verlangte bisher nur
eine MFA-verifizierte Sitzung und ein bestaetigtes TOTP-Geraet — waehrend das
Loeschen eines Passkeys, das Abschalten der MFA und der TOTP-Faktorwechsel
Sudo verlangen. Ein entwendetes, bereits verifiziertes Sitzungs-Cookie
reichte also, um dem Konto einen eigenen Passkey unterzuschieben.

Vertrag: besteht ein bestaetigter Faktor (TOTP oder Passkey), lehnen Begin und
Complete ohne Sudo mit 403 ``sudo_required`` ab — als JSON, denn die
Zeremonie laeuft per ``fetch``. Die Reihenfolge der Schranken bleibt: erst
der MFA-Nachweis (403 ``mfa_verification_required``), dann Sudo, dann die
fachliche Vorbedingung TOTP (400). Ohne bestaetigten Faktor gibt es keine
Sudo-Schranke. Die Einstellungsseite fuehrt ohne Sudo ueber die Sudo-Seite
zurueck auf sich selbst, statt eine Zeremonie anzubieten, die abgelehnt wird.

``settings/test.py`` schaltet Sudo ab — die Klassen hier schalten es per
Fixture ``sudo_an`` wieder ein, sonst waere der Guard trivial gruen.
"""

from __future__ import annotations

import json
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from django_otp import DEVICE_ID_SESSION_KEY

from core.models import AuditLog
from core.models import EncryptedTOTPDevice as TOTPDevice

BEGIN_URL = "/webauthn/registration/begin/"
COMPLETE_URL = "/webauthn/registration/complete/"
SETTINGS_URL = "/mfa/settings/"


@pytest.fixture
def sudo_an(settings):
    settings.SUDO_MODE_ENABLED = True


def _confirmed_passkey(user, credential_id=b"cred-2328-passkey"):
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


@pytest.fixture(params=["totp", "totp+passkey"])
def bestandsfaktor(request, staff_user):
    device = TOTPDevice.objects.create(user=staff_user, name="bestand", confirmed=True)
    if request.param == "totp+passkey":
        _confirmed_passkey(staff_user)
    return device


def _login(client, user, *, verified=True, sudo=False):
    """Angemeldet; ``verified`` wie nach ``/mfa/verify/`` (App-Flag + django-otp-Geraet)."""
    client.force_login(user)
    session = client.session
    if verified:
        session["mfa_verified"] = True
        device = TOTPDevice.objects.filter(user=user, confirmed=True).first()
        if device is not None:
            session[DEVICE_ID_SESSION_KEY] = device.persistent_id
    if sudo:
        session["sudo_until"] = int(time.time()) + 600
    session.save()


def _code(response) -> str:
    return json.loads(response.content).get("code")


def _passkey_count(user) -> int:
    from django_otp_webauthn.models import WebAuthnCredential

    return WebAuthnCredential.objects.filter(user=user).count()


@pytest.mark.django_db
@pytest.mark.usefixtures("sudo_an")
class TestRegistrationWithoutSudoIsRefused:
    def test_begin_refused(self, client, staff_user, bestandsfaktor):
        _login(client, staff_user)
        response = client.post(BEGIN_URL)
        assert response.status_code == 403
        assert _code(response) == "sudo_required"
        assert "challenge" not in json.loads(response.content)

    def test_complete_refused_before_any_ceremony(self, client, staff_user, bestandsfaktor):
        _login(client, staff_user)
        before = _passkey_count(staff_user)
        response = client.post(COMPLETE_URL, data="{}", content_type="application/json")
        assert response.status_code == 403
        assert _code(response) == "sudo_required"
        assert _passkey_count(staff_user) == before
        assert not AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.WEBAUTHN_REGISTERED).exists()

    def test_expired_window_is_refused(self, client, staff_user, bestandsfaktor):
        _login(client, staff_user)
        session = client.session
        session["sudo_until"] = int(time.time()) - 1
        session.save()
        response = client.post(BEGIN_URL)
        assert response.status_code == 403
        assert _code(response) == "sudo_required"

    def test_refusal_names_the_way_out(self, client, staff_user, bestandsfaktor):
        _login(client, staff_user)
        detail = json.loads(client.post(BEGIN_URL).content)["detail"]
        assert "Passwort" in detail
        assert "neu" in detail


@pytest.mark.django_db
@pytest.mark.usefixtures("sudo_an")
class TestRegistrationWithSudoProceeds:
    def test_begin_returns_options(self, client, staff_user, bestandsfaktor):
        _login(client, staff_user, sudo=True)
        response = client.post(BEGIN_URL)
        assert response.status_code == 200
        assert "challenge" in json.loads(response.content)

    def test_complete_passes_the_sudo_barrier(self, client, staff_user, bestandsfaktor):
        _login(client, staff_user, sudo=True)
        response = client.post(COMPLETE_URL, data="{}", content_type="application/json")
        # Ohne begonnene Zeremonie lehnt die Bibliothek ab — aber nicht die Sudo-Schranke.
        assert _code(response) != "sudo_required"

    def test_sudo_switched_off_needs_no_window(self, client, staff_user, bestandsfaktor, settings):
        settings.SUDO_MODE_ENABLED = False
        _login(client, staff_user)
        response = client.post(BEGIN_URL)
        assert response.status_code == 200


@pytest.mark.django_db
@pytest.mark.usefixtures("sudo_an")
class TestBarrierOrder:
    def test_unverified_session_sees_the_mfa_barrier_first(self, client, staff_user, bestandsfaktor):
        _login(client, staff_user, verified=False)
        response = client.post(BEGIN_URL)
        assert response.status_code == 403
        assert _code(response) == "mfa_verification_required"

    def test_without_any_factor_there_is_no_sudo_barrier(self, client, staff_user):
        _login(client, staff_user)
        response = client.post(BEGIN_URL)
        assert response.status_code == 400
        assert _code(response) == "totp_required"

    def test_passkey_only_account_needs_sudo_before_the_totp_rule(self, client, staff_user):
        _confirmed_passkey(staff_user)
        _login(client, staff_user)
        response = client.post(BEGIN_URL)
        assert response.status_code == 403
        assert _code(response) == "sudo_required"


@pytest.mark.django_db
@pytest.mark.usefixtures("sudo_an")
class TestSettingsPageLeadsThroughSudo:
    def test_without_sudo_the_page_links_to_the_sudo_step(self, client, staff_user, bestandsfaktor):
        _login(client, staff_user)
        response = client.get(SETTINGS_URL)
        assert response.status_code == 200
        link = response.context["passkey_sudo_url"]
        target = urlsplit(link)
        assert target.path == "/sudo/"
        assert parse_qs(target.query).get("next") == [SETTINGS_URL]
        content = response.content.decode()
        assert 'data-testid="passkey-sudo-link"' in content
        assert f'href="{link}"' in content
        assert 'data-testid="passkey-register-button"' not in content
        assert "otp_webauthn_register.js" not in content

    def test_with_sudo_the_page_offers_the_ceremony(self, client, staff_user, bestandsfaktor):
        _login(client, staff_user, sudo=True)
        content = client.get(SETTINGS_URL).content.decode()
        assert 'data-testid="passkey-register-button"' in content
        assert "otp_webauthn_register.js" in content
        assert 'data-testid="passkey-sudo-link"' not in content

    def test_sudo_switched_off_offers_the_ceremony(self, client, staff_user, bestandsfaktor, settings):
        settings.SUDO_MODE_ENABLED = False
        _login(client, staff_user)
        content = client.get(SETTINGS_URL).content.decode()
        assert 'data-testid="passkey-register-button"' in content
        assert 'data-testid="passkey-sudo-link"' not in content
