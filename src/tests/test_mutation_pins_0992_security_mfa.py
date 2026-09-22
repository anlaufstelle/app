"""Pin-Tests der Welle N (release-0.99.2) — Modul ``security/mfa`` + ``views/mfa``.

Gehoeren zu den Fixes fuer
https://github.com/anlaufstelle/app/issues/2287 und
https://github.com/anlaufstelle/app/issues/2294. Die Ablehnungsseite
beider Befunde liegt in den Guard-Dateien
(``test_mfa_setup_reauth_guard.py``, ``test_mfa_backup_code_format_guard.py``);
hier stehen die Gegenrichtungen, die ein Mutant sonst kostenlos mitnehmen
koennte:

* der **regulaere Erstsetup-Pfad** darf durch das neue ``dispatch``-Gate nicht
  enger werden (ein Mutant, der das Gate auf jede Sitzung ausweitet, wuerde
  sonst nur in den Guards auffallen, nicht in der Gegenrichtung),
* die **Gerätebindung** des Setup-Abschlusses greift genau auf das
  unbestaetigte, autorisierte Geraet — nicht auf ein bereits bestaetigtes,
* die **Formatpruefung** des Klartext-Fallbacks ist exakt (Laenge, Position des
  Bindestrichs, Hex-Alphabet, Kleinschreibung) und nicht „irgendwie
  code-foermig",
* der **Throttle-Schritt** bleibt genau einer je Fehlversuch, obwohl die
  Kandidatenliste jetzt variable Laenge hat.
"""

from __future__ import annotations

import pytest
from django_otp.oath import totp as oath_totp
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken

from core.models import AuditLog
from core.models import EncryptedTOTPDevice as TOTPDevice
from core.services.security import (
    BACKUP_CODES_COUNT,
    generate_backup_codes,
    remaining_backup_codes,
    verify_backup_code,
)
from core.services.security.mfa import _hash_code


def _valid_token(device: TOTPDevice) -> str:
    token = oath_totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits)
    return f"{token:0{device.digits}d}"


@pytest.mark.django_db
class TestFirstSetupPathUnchanged:
    """Refs #2287: Das Gate darf nur den erneuten Einstieg treffen."""

    def test_full_first_setup_completes_and_consumes_device_marker(self, client, staff_user):
        assert client.login(username="teststaff", password="testpass123")

        page = client.get("/mfa/setup/")
        assert page.status_code == 200
        device = TOTPDevice.objects.get(user=staff_user, confirmed=False)

        posted = client.post("/mfa/setup/", {"token": _valid_token(device)})
        assert posted.status_code == 302
        assert posted.url == "/mfa/backup-codes/"
        # Der Abschluss ist an genau dieses Geraet gebunden.
        assert client.session.get("mfa_setup_device_pk") == device.pk
        assert client.session.get("mfa_setup_pending") is True

        done = client.post("/mfa/backup-codes/", {"confirm_saved": "on"})
        assert done.status_code == 302
        assert done.url == "/mfa/settings/"
        device.refresh_from_db()
        assert device.confirmed is True
        assert client.session.get("mfa_verified") is True
        # Marker und Codes sind nach dem Abschluss aus der Sitzung geraeumt.
        assert client.session.get("mfa_setup_device_pk") is None
        assert client.session.get("mfa_setup_pending") is None
        assert client.session.get("mfa_backup_codes") is None
        assert AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.MFA_ENABLED).count() == 1
        assert remaining_backup_codes(staff_user) == BACKUP_CODES_COUNT

    def test_setup_entry_stays_open_without_any_confirmed_factor(self, client, staff_user):
        """Ohne Bestandsfaktor bleibt der Erstsetup-Einstieg unverifiziert erreichbar."""
        assert client.login(username="teststaff", password="testpass123")
        assert client.session.get("mfa_verified") is not True

        response = client.get("/mfa/setup/")

        assert response.status_code == 200
        assert TOTPDevice.objects.filter(user=staff_user, confirmed=False).count() == 1

    def test_unconfirmed_device_alone_does_not_trigger_the_gate(self, client, staff_user):
        """Ein unbestaetigtes Geraet ist kein Bestandsfaktor."""
        TOTPDevice.objects.create(user=staff_user, name="offen", confirmed=False)
        assert client.login(username="teststaff", password="testpass123")

        response = client.get("/mfa/setup/")

        assert response.status_code == 200


@pytest.mark.django_db
class TestSetupCompletionDeviceBinding:
    """Refs #2287: ``confirmed=False`` im Bindungs-Filter ist tragend."""

    def test_marker_on_already_confirmed_device_does_not_complete_setup(self, client, staff_user):
        import json

        from core.services.file_vault import encrypt_field

        bereits = TOTPDevice.objects.create(user=staff_user, name="bestand", confirmed=True)
        assert client.login(username="teststaff", password="testpass123")
        session = client.session
        session["mfa_verified"] = True
        session["mfa_backup_codes"] = encrypt_field(json.dumps(["Dddddddddddddddddddddd"]))
        session["mfa_setup_pending"] = True
        session["mfa_setup_device_pk"] = bereits.pk
        session.save()

        response = client.post("/mfa/backup-codes/", {"confirm_saved": "on"})

        assert response.status_code == 302
        assert response.url == "/mfa/setup/"
        assert not AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.MFA_ENABLED).exists()

    def test_marker_of_another_account_is_not_accepted(self, client, staff_user, lead_user):
        import json

        from core.services.file_vault import encrypt_field

        fremd = TOTPDevice.objects.create(user=lead_user, name="fremd", confirmed=False)
        assert client.login(username="teststaff", password="testpass123")
        session = client.session
        session["mfa_backup_codes"] = encrypt_field(json.dumps(["Eeeeeeeeeeeeeeeeeeeeee"]))
        session["mfa_setup_pending"] = True
        session["mfa_setup_device_pk"] = fremd.pk
        session.save()

        response = client.post("/mfa/backup-codes/", {"confirm_saved": "on"})

        assert response.status_code == 302
        assert response.url == "/mfa/setup/"
        fremd.refresh_from_db()
        assert fremd.confirmed is False
        assert client.session.get("mfa_verified") is not True


@pytest.mark.django_db
class TestLegacyFormatCheckIsExact:
    """Refs #2294: Die Formatpruefung ist exakt, nicht „ungefaehr"."""

    @staticmethod
    def _device_with_cleartext(user, token: str) -> StaticDevice:
        device = StaticDevice.objects.create(user=user, name="backup", confirmed=True)
        StaticToken.objects.create(device=device, token=token)
        return device

    def test_exact_historic_format_still_verifies(self, staff_user):
        self._device_with_cleartext(staff_user, "abcd-1234")
        assert verify_backup_code(staff_user, "abcd-1234") is True

    @pytest.mark.parametrize(
        "eingabe",
        [
            "ABCD-1234",  # Grossschreibung — ``secrets.token_hex`` liefert nur Kleinbuchstaben
            "abcd1234",  # ohne Bindestrich (die View normalisiert das vorher)
            "abc-d1234",  # Bindestrich an falscher Position
            "abcd-12345",  # eine Stelle zu lang
            "abcd-123",  # eine Stelle zu kurz
            "abcz-1234",  # ausserhalb des Hex-Alphabets
            " abcd-1234",  # fuehrendes Leerzeichen
            "abcd-1234 ",  # abschliessendes Leerzeichen
        ],
    )
    def test_near_miss_shapes_are_not_compared_directly(self, staff_user, eingabe):
        device = self._device_with_cleartext(staff_user, "abcd-1234")
        assert verify_backup_code(staff_user, eingabe) is False
        assert device.token_set.count() == 1

    def test_digest_shape_is_never_compared_directly(self, staff_user):
        """Der gespeicherte Wert selbst ist kein Kandidat fuer den Direktvergleich."""
        device = StaticDevice.objects.create(user=staff_user, name="backup", confirmed=True)
        digest = _hash_code("Ffffffffffffffffffffff")
        StaticToken.objects.create(device=device, token=digest)

        assert verify_backup_code(staff_user, digest) is False
        # Die Drosselung aus dem Fehlversuch zuruecksetzen, sonst kaeme der
        # naechste Aufruf gar nicht bis zum Lookup (``verify_is_allowed``).
        device.refresh_from_db()
        device.throttle_reset()
        assert verify_backup_code(staff_user, "Ffffffffffffffffffffff") is True

    def test_single_throttle_increment_per_miss(self, staff_user):
        """Refs #790/#2294: Die variable Kandidatenliste bleibt EIN Fehlversuch."""
        generate_backup_codes(staff_user)
        device = StaticDevice.objects.get(user=staff_user, name="backup")

        # Klartext-foermige Eingabe: zwei Kandidaten in EINER Query.
        assert verify_backup_code(staff_user, "abcd-1234") is False
        device.refresh_from_db()
        assert device.throttling_failure_count == 1

        device.throttle_reset()
        # Nicht klartext-foermige Eingabe: ein Kandidat — gleicher Preis.
        assert verify_backup_code(staff_user, "Gggggggggggggggggggggg") is False
        device.refresh_from_db()
        assert device.throttling_failure_count == 1
