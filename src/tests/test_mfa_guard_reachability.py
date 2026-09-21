"""Erreichbarkeit und Zusagen der MFA-Schranke (Refs #2237).

Zwei Fragen, die vor jeder Vereinfachung an dieser Schranke beantwortet sein
müssen — und die dieser Guard dauerhaft offenhält:

1. **Wo greift welcher Guard?** ``_RequireVerifiedSessionAndTOTPMixin.initial()``
   prüft ``session["mfa_verified"]``, bevor ``super().initial()`` den
   Bibliotheks-Vorcheck (``_check_user_verified`` seit django-otp-webauthn
   0.10.3) und danach ``check_can_register()`` aufruft. Eine zweite
   ``mfa_verified``-Prüfung in ``check_can_register()`` kann deshalb nie
   zuschlagen: Die Methode wird ausschließlich aus ``initial()`` heraus
   erreicht, und dort ist das Flag bereits erzwungen. Der Spy unten belegt das
   am echten Endpoint statt am Quelltext.
2. **Bleibt die MFA-Pflicht unberührt?** Ein Konto mit Zweitfaktor-Pflicht darf
   ohne verifizierte Sitzung keine geschützte Seite sehen — weder mit TOTP noch
   mit Passkey — und ``next=`` darf nur auf erlaubte interne Ziele führen, nie
   nach außen und nie im Kreis auf die Verify-Seite.
"""

from __future__ import annotations

import json

import pytest
from django_otp.oath import totp

from core.models import EncryptedTOTPDevice as TOTPDevice
from core.services.security import SUDO_SESSION_KEY
from core.views import mfa_webauthn

GESCHUETZTE_SEITE = "/clients/"


def _make_totp(user):
    return TOTPDevice.objects.create(user=user, name="totp", confirmed=True, key="a" * 40)


def _valid_totp(device):
    token = totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits)
    return f"{token:0{device.digits}d}"


def _make_passkey(user, *, credential_id=b"cred-reach"):
    from django_otp_webauthn.models import WebAuthnCredential

    return WebAuthnCredential.objects.create(
        user=user,
        name="Testschlüssel",
        confirmed=True,
        credential_id=credential_id,
        public_key=b"pubkey-bytes",
        aaguid="00000000-0000-0000-0000-000000000000",
        sign_count=0,
    )


def _verify_session(client):
    """Markiert die Session als 2FA-verifiziert (App-Flag, ohne django-otp-Gerät)."""
    session = client.session
    session["mfa_verified"] = True
    session.save()


@pytest.fixture
def registrierungs_spy(monkeypatch):
    """Protokolliert den Wert von ``mfa_verified`` bei jedem ``check_can_register``.

    Liefert die Liste der beobachteten Flag-Werte — ein Eintrag je tatsächlich
    erreichtem Aufruf, in Aufrufreihenfolge.
    """
    beobachtet: list[object] = []
    original = mfa_webauthn._RequireVerifiedSessionAndTOTPMixin.check_can_register

    def spy(self):
        beobachtet.append(self.request.session.get("mfa_verified"))
        return original(self)

    monkeypatch.setattr(mfa_webauthn._RequireVerifiedSessionAndTOTPMixin, "check_can_register", spy)
    return beobachtet


@pytest.mark.django_db
class TestRegistrierungsGuardErreichbarkeit:
    """Refs #2237: welcher der beiden App-Guards ist überhaupt erreichbar?"""

    def test_app_guard_in_initial_greift_vor_der_bibliothek(self, client, staff_user, registrierungs_spy):
        """Unverifizierte Sitzung mit bestätigtem TOTP: 403 aus ``initial()``.

        ``check_can_register`` wird dabei gar nicht erst betreten — die dortige
        ``mfa_verified``-Prüfung könnte also nie zuschlagen.
        """
        _make_totp(staff_user)
        client.login(username="teststaff", password="testpass123")

        response = client.post("/webauthn/registration/begin/")

        assert response.status_code == 403
        assert json.loads(response.content)["code"] == "mfa_verification_required"
        assert registrierungs_spy == []

    def test_complete_endpoint_erreicht_check_can_register_ebenfalls_nicht(
        self, client, staff_user, registrierungs_spy
    ):
        _make_totp(staff_user)
        client.login(username="teststaff", password="testpass123")

        response = client.post(
            "/webauthn/registration/complete/",
            data="{}",
            content_type="application/json",
        )

        assert response.status_code == 403
        assert registrierungs_spy == []

    def test_ohne_jedes_geraet_bleibt_der_app_guard_die_erste_schranke(self, client, staff_user, registrierungs_spy):
        """Ohne bestätigtes Gerät schweigt der Bibliotheks-Vorcheck.

        ``_check_user_verified`` greift nur bei bereits eingeschriebenen Konten
        (``user_is_mfa_enrolled``). Der App-Guard in ``initial()`` ist hier also
        die einzige Schranke vor der Registrierung — das ist der eigenständige
        Beitrag, den er neben der Bibliothek noch leistet.
        """
        client.login(username="teststaff", password="testpass123")

        response = client.post("/webauthn/registration/begin/")

        assert response.status_code == 403
        assert json.loads(response.content)["code"] == "mfa_verification_required"
        assert registrierungs_spy == []

    def test_check_can_register_sieht_ausschliesslich_verifizierte_sitzungen(
        self, client, staff_user, registrierungs_spy
    ):
        """Die beiden Wege, die ``check_can_register`` überhaupt erreichen.

        Ohne TOTP: 400 aus der fachlichen Vorbedingung. Mit TOTP: Options-Ausgabe.
        In beiden Fällen ist ``mfa_verified`` beim Eintritt bereits gesetzt.
        """
        client.login(username="teststaff", password="testpass123")
        _verify_session(client)

        ohne_totp = client.post("/webauthn/registration/begin/")
        assert ohne_totp.status_code == 400
        assert json.loads(ohne_totp.content)["code"] == "totp_required"

        device = _make_totp(staff_user)
        client.post("/mfa/verify/", {"token": _valid_totp(device)})
        mit_totp = client.post("/webauthn/registration/begin/")
        assert mit_totp.status_code == 200

        assert len(registrierungs_spy) == 2
        assert all(flag is True for flag in registrierungs_spy)

    def test_app_guard_laeuft_im_mro_vor_der_bibliotheks_zeremonie(self):
        """Ordnungs-Guard: kippt die MRO, kippt die Aussage der Tests oben."""
        from django_otp_webauthn.views import RegistrationCeremonyMixin

        for view in (
            mfa_webauthn.WebAuthnRegistrationBeginView,
            mfa_webauthn.WebAuthnRegistrationCompleteView,
        ):
            mro = view.__mro__
            assert mro.index(mfa_webauthn._RequireVerifiedSessionAndTOTPMixin) < mro.index(RegistrationCeremonyMixin)


@pytest.mark.django_db
class TestMfaPflichtOhneZweitenFaktor:
    """Refs #2237: Die Pflicht selbst darf sich durch die Bereinigung nicht ändern."""

    def test_totp_konto_ohne_verifikation_sieht_keine_geschuetzte_seite(self, client, staff_user):
        _make_totp(staff_user)
        client.login(username="teststaff", password="testpass123")

        response = client.get(GESCHUETZTE_SEITE)

        assert response.status_code == 302
        assert response.url == f"/mfa/verify/?next={GESCHUETZTE_SEITE.replace('/', '%2F')}"

    def test_passkey_konto_ohne_verifikation_sieht_keine_geschuetzte_seite(self, client, staff_user):
        _make_passkey(staff_user)
        client.login(username="teststaff", password="testpass123")

        response = client.get(GESCHUETZTE_SEITE)

        assert response.status_code == 302
        assert response.url.startswith("/mfa/verify/")

    def test_ohne_bestaetigtes_geraet_fuehrt_die_pflicht_ins_setup(self, client, staff_user):
        staff_user.mfa_required = True
        staff_user.save(update_fields=["mfa_required"])
        client.login(username="teststaff", password="testpass123")

        response = client.get(GESCHUETZTE_SEITE)

        assert response.status_code == 302
        assert response.url == "/mfa/setup/"

    def test_wurzelziel_wird_ohne_next_umgeleitet(self, client, staff_user):
        """Das Wurzelziel bleibt ohne ``next`` — es ist der Rollen-Default."""
        _make_totp(staff_user)
        client.login(username="teststaff", password="testpass123")

        response = client.get("/")

        assert response.status_code == 302
        assert response.url == "/mfa/verify/"


@pytest.mark.django_db
class TestVerifyNextZiele:
    """Refs #2237: ``next=`` führt nur auf erlaubte interne Ziele."""

    @pytest.mark.parametrize(
        "ziel",
        ["https://[", "//[", "http://[::1", "https://evil.example/\x00\\pfad"],
    )
    def test_malformierte_get_ziele_fallen_auf_rollen_default_zurueck(self, client, staff_user, ziel):
        _make_totp(staff_user)
        client.login(username="teststaff", password="testpass123")
        _verify_session(client)

        response = client.get("/mfa/verify/", {"next": ziel})

        assert response.status_code == 302
        assert response.url == "/"

    @pytest.mark.parametrize(
        "ziel",
        ["https://[", "//[", "http://[::1", "https://evil.example/\x00\\pfad"],
    )
    def test_malformierte_post_ziele_fallen_auf_rollen_default_zurueck(self, client, staff_user, ziel):
        _make_totp(staff_user)
        client.login(username="teststaff", password="testpass123")
        _verify_session(client)

        response = client.post("/mfa/verify/", {"next": ziel})

        assert response.status_code == 302
        assert response.url == "/"

    def test_internes_ziel_bleibt_erhalten(self, client, staff_user):
        device = _make_totp(staff_user)
        client.login(username="teststaff", password="testpass123")

        response = client.post(f"/mfa/verify/?next={GESCHUETZTE_SEITE}", {"token": _valid_totp(device)})

        assert response.url == GESCHUETZTE_SEITE

    @pytest.mark.parametrize(
        "ziel",
        ["https://evil.example/", "//evil.example/", "/\\evil.example", "javascript:alert(1)"],
    )
    def test_externe_ziele_werden_verworfen(self, client, staff_user, ziel):
        device = _make_totp(staff_user)
        client.login(username="teststaff", password="testpass123")

        response = client.post("/mfa/verify/", {"token": _valid_totp(device), "next": ziel})

        assert response.url == "/"

    def test_next_auf_die_verify_seite_erzeugt_keinen_zusatz_hop(self, client, staff_user):
        """Kein Kreisverkehr: ``next=/mfa/verify/`` landet nicht wieder dort.

        Die Verify-Seite leitet eine bereits verifizierte Sitzung sofort weiter
        — ein ``next`` auf sie selbst kostete also einen zusätzlichen Redirect
        und ließe sich zu einer Kette stapeln.
        """
        device = _make_totp(staff_user)
        client.login(username="teststaff", password="testpass123")

        response = client.post("/mfa/verify/?next=/mfa/verify/", {"token": _valid_totp(device)})

        assert response.status_code == 302
        assert response.url == "/"

    def test_next_auf_die_verify_seite_mit_verschachteltem_ziel(self, client, staff_user):
        device = _make_totp(staff_user)
        client.login(username="teststaff", password="testpass123")

        response = client.post(
            "/mfa/verify/",
            {"token": _valid_totp(device), "next": f"/mfa/verify/?next={GESCHUETZTE_SEITE}"},
        )

        assert response.url == "/"

    def test_bereits_verifizierte_sitzung_springt_nicht_auf_sich_selbst(self, client, staff_user):
        _make_totp(staff_user)
        client.login(username="teststaff", password="testpass123")
        _verify_session(client)

        response = client.get("/mfa/verify/?next=/mfa/verify/")

        assert response.status_code == 302
        assert response.url == "/"

    def test_super_admin_behaelt_seinen_rollen_default(self, client, super_admin_user):
        device = _make_totp(super_admin_user)
        client.force_login(super_admin_user)

        response = client.post("/mfa/verify/?next=/mfa/verify/", {"token": _valid_totp(device)})

        assert response.url == "/system/"


@pytest.mark.django_db
class TestSudoPfadeUnveraendert:
    """Refs #2237: Re-Auth bleibt, wie sie war."""

    def test_mfa_disable_verlangt_sudo(self, client, staff_user, settings):
        settings.SUDO_MODE_ENABLED = True
        _make_totp(staff_user)
        client.login(username="teststaff", password="testpass123")
        _verify_session(client)

        response = client.post("/mfa/disable/")

        assert response.status_code == 302
        assert response.url.startswith("/sudo/?next=")

    def test_passkey_loeschen_verlangt_sudo(self, client, staff_user, settings):
        settings.SUDO_MODE_ENABLED = True
        passkey = _make_passkey(staff_user)
        _make_totp(staff_user)
        client.login(username="teststaff", password="testpass123")
        _verify_session(client)

        response = client.post(f"/mfa/passkey/{passkey.pk}/delete/")

        assert response.status_code == 302
        assert response.url.startswith("/sudo/?next=")

    def test_sudo_sitzung_laesst_das_deaktivieren_zu(self, client, staff_user, settings):
        settings.SUDO_MODE_ENABLED = True
        _make_totp(staff_user)
        client.login(username="teststaff", password="testpass123")
        session = client.session
        session["mfa_verified"] = True
        session[SUDO_SESSION_KEY] = 9999999999
        session.save()

        response = client.post("/mfa/disable/")

        assert response.status_code == 302
        assert staff_user.has_confirmed_totp_device is False
