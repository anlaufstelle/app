"""Tests für 2FA-Backup-Codes (Refs #588)."""

import re
import threading
from unittest import mock

import pytest
from django.db import close_old_connections, connection
from django_otp.oath import totp as oath_totp
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

from core.models import AuditLog, User
from core.services.security import (
    BACKUP_CODES_COUNT,
    generate_backup_codes,
    remaining_backup_codes,
    verify_backup_code,
)

# Refs #790: token_urlsafe(16) -> 22 Zeichen aus Base64-URL-safe Alphabet.
CODE_FORMAT = re.compile(r"^[A-Za-z0-9_-]{22}$")
# Extrahiert die Codes gezielt aus den <li>-Elementen der Backup-Codes-Liste.
CODE_IN_HTML = re.compile(r"select-all\">([A-Za-z0-9_-]{22})</li>")


@pytest.mark.django_db
class TestBackupCodesService:
    def test_generate_returns_ten_codes_in_expected_format(self, staff_user):
        codes = generate_backup_codes(staff_user)
        assert len(codes) == BACKUP_CODES_COUNT
        assert all(CODE_FORMAT.match(c) for c in codes), (
            f"Erwartet 22-Zeichen URL-safe Base64 (128 Bit Entropie), gemessen: {codes}"
        )
        assert len(set(codes)) == BACKUP_CODES_COUNT, "Codes müssen alle unterschiedlich sein"

    def test_generated_codes_have_at_least_128_bits_of_entropy(self, staff_user):
        """Refs #790 (C-22): 22 Zeichen Base64URL = 132 Bit, also >= 128 Bit."""
        import math

        codes = generate_backup_codes(staff_user)
        for c in codes:
            entropy_bits = len(c) * math.log2(64)  # Base64-Alphabet
            assert entropy_bits >= 128, (
                f"Backup-Code '{c}' hat nur {entropy_bits:.0f} Bit Entropie, mind. 128 Bit erwartet."
            )

    def test_codes_stored_as_keyed_digests(self, staff_user):
        """Storage uses 16 base64url characters and contains no cleartext codes."""
        from core.services.security.mfa import _hash_code

        codes = generate_backup_codes(staff_user)
        device = StaticDevice.objects.get(user=staff_user, name="backup")
        stored_tokens = set(device.token_set.values_list("token", flat=True))
        assert stored_tokens == {_hash_code(code) for code in codes}
        assert all(len(value) == 16 for value in stored_tokens)
        assert stored_tokens.isdisjoint(codes)

    def test_legacy_codes_are_rejected(self, staff_user):
        """The security requirement removes support for weak legacy codes."""
        from django_otp.plugins.otp_static.models import StaticToken

        from core.services.security.mfa import _hash_code

        device = StaticDevice.objects.create(user=staff_user, name="backup", confirmed=True)
        StaticToken.objects.create(device=device, token=_hash_code("abcd-1234"))
        assert verify_backup_code(staff_user, "abcd-1234") is False
        assert device.token_set.count() == 1

    def test_generate_replaces_existing_codes(self, staff_user):
        first = generate_backup_codes(staff_user)
        second = generate_backup_codes(staff_user)
        assert set(first).isdisjoint(set(second))
        # Genau ein StaticDevice mit frischem Satz.
        devices = StaticDevice.objects.filter(user=staff_user, name="backup")
        assert devices.count() == 1
        assert devices.first().token_set.count() == BACKUP_CODES_COUNT

    def test_remaining_backup_codes(self, staff_user):
        assert remaining_backup_codes(staff_user) == 0
        generate_backup_codes(staff_user)
        assert remaining_backup_codes(staff_user) == BACKUP_CODES_COUNT

    def test_verify_backup_code_is_single_use(self, staff_user):
        codes = generate_backup_codes(staff_user)
        one = codes[0]
        assert verify_backup_code(staff_user, one) is True
        assert verify_backup_code(staff_user, one) is False
        assert remaining_backup_codes(staff_user) == BACKUP_CODES_COUNT - 1

    def test_verify_unknown_code_rejected(self, staff_user):
        generate_backup_codes(staff_user)
        assert verify_backup_code(staff_user, "0000-0000") is False
        assert verify_backup_code(staff_user, "") is False
        assert verify_backup_code(staff_user, None) is False

    def test_unknown_code_increments_throttle_exactly_once(self, staff_user):
        """Refs #790: Ein Fehlversuch kostet genau einen Throttle-Schritt."""
        generate_backup_codes(staff_user)
        device = StaticDevice.objects.get(user=staff_user, name="backup")

        assert verify_backup_code(staff_user, "0000-0000") is False

        device.refresh_from_db()
        assert device.throttling_failure_count == 1

    def test_verify_without_device_returns_false(self, staff_user):
        assert verify_backup_code(staff_user, "abcd-ef01") is False


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
class TestBackupCodeConsumptionRace:
    """Refs #2110: Ein Backup-Code darf auch parallel nur einmal gelingen.

    Die Barrier erzwingt den Vorzustand deterministisch. Nach dem Fix laeuft
    der Guard als Naturrennen; seine Schutzwirkung ist zusaetzlich durch die
    Mutantensonde ohne ausgewerteten DELETE-Rowcount belegt.
    """

    def test_concurrent_verification_has_exactly_one_winner(self, staff_user):
        code = generate_backup_codes(staff_user)[0]
        ready = threading.Barrier(2, timeout=20)
        both_matched = threading.Barrier(2, timeout=20)
        results = [None, None]
        errors = []
        real_delete = StaticToken.delete

        def synchronized_delete(token, *args, **kwargs):
            # Erzwingt beim alten Check-then-delete, dass beide Threads denselben
            # Token gelesen haben, bevor einer ihn loescht.
            both_matched.wait(timeout=20)
            return real_delete(token, *args, **kwargs)

        def worker(slot):
            try:
                connection.close()
                ready.wait(timeout=20)
                user = User.objects.get(pk=staff_user.pk)
                results[slot] = verify_backup_code(user, code)
            except Exception as exc:  # pragma: no cover - Sammelassertion
                errors.append(exc)
            finally:
                close_old_connections()

        with mock.patch.object(StaticToken, "delete", synchronized_delete):
            threads = [threading.Thread(target=worker, args=(slot,)) for slot in (0, 1)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

        assert not any(thread.is_alive() for thread in threads), "Deadlock beim parallelen Backup-Code-Consume"
        assert not errors, f"Worker-Fehler: {errors!r}"
        assert results.count(True) == 1, results
        assert remaining_backup_codes(staff_user) == BACKUP_CODES_COUNT - 1


@pytest.mark.django_db
class TestBackupCodesSessionEncryption:
    """L2 (Refs #1375): Frisch generierte Backup-Codes dürfen NICHT im Klartext
    im (DB-)Session-Store liegen — sie werden für den Anzeige-Zwischenschritt at
    rest verschlüsselt abgelegt (Fernet, wie TOTP-Keys ADR-031) und beim Lesen
    entschlüsselt. Der Anzeige-/Quittungs-Flow bleibt unverändert."""

    def _setup_and_show(self, client, user):
        from core.models import EncryptedTOTPDevice

        client.login(username="teststaff", password="testpass123")
        client.get("/mfa/setup/")
        device = EncryptedTOTPDevice.objects.get(user=user, confirmed=False)
        token = oath_totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits)
        client.post("/mfa/setup/", {"token": f"{token:0{device.digits}d}"})
        shown = client.get("/mfa/backup-codes/")
        rendered = CODE_IN_HTML.findall(shown.content.decode())
        return rendered

    def test_session_stores_encrypted_not_cleartext_codes(self, client, staff_user):
        import json

        from core.services.file_vault import decrypt_field

        rendered = self._setup_and_show(client, staff_user)
        assert len(rendered) == BACKUP_CODES_COUNT

        stored = client.session["mfa_backup_codes"]
        # Kein Klartext-List-Objekt, sondern der verschlüsselte Marker-Dict.
        assert isinstance(stored, dict)
        assert stored.get("__encrypted__") is True

        # Kein einziger Klartext-Code steht in der serialisierten Session.
        serialized = json.dumps(stored)
        for code in rendered:
            assert code not in serialized, "Klartext-Backup-Code in der Session gefunden (L2)"

        # Entschlüsselt sind es exakt die angezeigten Codes.
        assert json.loads(decrypt_field(stored)) == rendered


@pytest.mark.django_db
class TestMFAVerifyWithBackupCode:
    """Der Login-Prompt akzeptiert Backup-Codes, wenn der Nutzer `mode=backup` sendet."""

    def _setup_confirmed_totp(self, user):
        device = TOTPDevice.objects.create(user=user, name="default", confirmed=True)
        return device

    def test_backup_code_login_marks_session_verified(self, client, staff_user):
        self._setup_confirmed_totp(staff_user)
        codes = generate_backup_codes(staff_user)
        client.login(username="teststaff", password="testpass123")

        response = client.post(
            "/mfa/verify/",
            {"mode": "backup", "token": codes[0]},
        )
        assert response.status_code == 302
        assert response.url == "/"
        # Session als verifiziert markiert; Code verbraucht.
        assert client.session.get("mfa_verified") is True
        assert remaining_backup_codes(staff_user) == BACKUP_CODES_COUNT - 1
        assert AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.BACKUP_CODES_USED).exists()

    @pytest.mark.parametrize("token", ["abcd1234", "ABCD1234", "abcd-1234"])
    def test_legacy_input_never_verifies(self, client, staff_user, token):
        """Legacy input has no normalization or fallback verification path."""
        from django_otp.plugins.otp_static.models import StaticToken

        from core.services.security.mfa import _hash_code

        self._setup_confirmed_totp(staff_user)
        device = StaticDevice.objects.create(user=staff_user, name="backup", confirmed=True)
        StaticToken.objects.create(device=device, token=_hash_code("abcd-1234"))
        client.login(username="teststaff", password="testpass123")
        response = client.post("/mfa/verify/", {"mode": "backup", "token": token})
        assert response.status_code == 200
        assert client.session.get("mfa_verified") is not True
        assert device.token_set.count() == 1

    def test_totp_path_still_works_when_not_in_backup_mode(self, client, staff_user):
        device = self._setup_confirmed_totp(staff_user)
        generate_backup_codes(staff_user)
        token = oath_totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits)

        client.login(username="teststaff", password="testpass123")
        response = client.post(
            "/mfa/verify/",
            {"token": f"{token:0{device.digits}d}"},
        )
        assert response.status_code == 302
        # Backup-Codes unverändert.
        assert remaining_backup_codes(staff_user) == BACKUP_CODES_COUNT

    def test_invalid_backup_code_logs_mfa_failed(self, client, staff_user):
        self._setup_confirmed_totp(staff_user)
        generate_backup_codes(staff_user)
        client.login(username="teststaff", password="testpass123")

        response = client.post(
            "/mfa/verify/",
            {"mode": "backup", "token": "1234-5678"},
        )
        assert response.status_code == 200  # Form mit Fehler
        failed = AuditLog.objects.filter(
            user=staff_user,
            action=AuditLog.Action.MFA_FAILED,
            detail__mode="backup",
        ).first()
        assert failed is not None


@pytest.mark.django_db
class TestMFABackupCodesView:
    def test_codes_remain_visible_until_acknowledged(self, client, staff_user):
        """Refs #1118: Die Codes bleiben über Reloads hinweg sichtbar, bis der
        Nutzer ihre Sicherung bestätigt. Ein versehentlicher Reload soll die
        einmal generierten Codes nicht verschlucken."""
        client.login(username="teststaff", password="testpass123")
        session = client.session
        session["mfa_backup_codes"] = ["aaaa-bbbb", "cccc-dddd"]
        session.save()

        first = client.get("/mfa/backup-codes/")
        assert first.status_code == 200
        assert b"aaaa-bbbb" in first.content

        # Reload zeigt die Codes weiterhin (peek statt pop) — erst die Quittung räumt auf.
        second = client.get("/mfa/backup-codes/")
        assert second.status_code == 200
        assert b"aaaa-bbbb" in second.content

    def test_view_redirects_when_no_codes_in_session(self, client, staff_user):
        client.login(username="teststaff", password="testpass123")
        response = client.get("/mfa/backup-codes/")
        assert response.status_code == 302
        assert response.url == "/mfa/settings/"

    def test_ack_without_confirmation_does_not_activate_2fa(self, client, staff_user):
        """Refs #1118: POST ohne gesetzte Bestätigungs-Checkbox schließt das
        Setup NICHT ab — das Device bleibt unbestätigt, kein MFA_ENABLED."""
        device = TOTPDevice.objects.create(user=staff_user, name="default", confirmed=False)
        client.login(username="teststaff", password="testpass123")
        session = client.session
        session["mfa_backup_codes"] = ["aaaa-bbbb", "cccc-dddd"]
        session["mfa_setup_pending"] = True
        session.save()

        # Kein 'confirm_saved' im POST — serverseitig muss das abgewiesen werden.
        response = client.post("/mfa/backup-codes/")
        assert response.status_code == 200  # Re-Render mit Validierungshinweis
        device.refresh_from_db()
        assert device.confirmed is False
        assert not AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.MFA_ENABLED).exists()
        # Setup bleibt eindeutig unvollständig.
        assert client.session.get("mfa_setup_pending") is True
        assert staff_user.has_confirmed_totp_device is False

    def test_ack_with_confirmation_completes_setup(self, client, staff_user):
        """Refs #1118: POST mit gesetzter Checkbox aktiviert 2FA — Device wird
        bestätigt, Session verifiziert, MFA_ENABLED auditiert, Codes geräumt."""
        device = TOTPDevice.objects.create(user=staff_user, name="default", confirmed=False)
        client.login(username="teststaff", password="testpass123")
        session = client.session
        session["mfa_backup_codes"] = ["aaaa-bbbb", "cccc-dddd"]
        session["mfa_setup_pending"] = True
        session.save()

        # Refs #2287: Der Abschluss haengt am konkret autorisierten Geraet —
        # ``MFASetupView.post`` legt dessen PK unter ``mfa_setup_device_pk`` ab.
        session["mfa_setup_device_pk"] = device.pk
        session.save()

        response = client.post("/mfa/backup-codes/", {"confirm_saved": "on"})
        assert response.status_code == 302
        assert response.url == "/mfa/settings/"
        device.refresh_from_db()
        assert device.confirmed is True
        assert client.session.get("mfa_verified") is True
        assert client.session.get("mfa_setup_pending") is None
        # Codes nach Abschluss aus der Session entfernt (keine Wiederanzeige).
        assert client.session.get("mfa_backup_codes") is None
        assert AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.MFA_ENABLED).exists()

    def test_ack_without_codes_in_session_redirects(self, client, staff_user):
        """Ein POST ohne Codes in der Session (z. B. nach Abschluss/Reload) führt
        ohne Seiteneffekt zurück in die Einstellungen."""
        client.login(username="teststaff", password="testpass123")
        response = client.post("/mfa/backup-codes/", {"confirm_saved": "on"})
        assert response.status_code == 302
        assert response.url == "/mfa/settings/"


@pytest.mark.django_db
class TestMFARegenerate:
    """Neuerzeugen aus einer MFA-verifizierten Sitzung (Refs #2328: nur diese
    erreicht die Route; eine nicht verifizierte leitet das ``dispatch``-Gate
    nach ``/mfa/verify/``, siehe ``test_mfa_backup_views_dispatch_gate.py``)."""

    @staticmethod
    def _login_verified(client):
        client.login(username="teststaff", password="testpass123")
        session = client.session
        session["mfa_verified"] = True
        session.save()

    def test_regenerate_rejected_on_invalid_totp(self, client, staff_user):
        TOTPDevice.objects.create(user=staff_user, name="default", confirmed=True)
        first = generate_backup_codes(staff_user)
        self._login_verified(client)

        response = client.post(
            "/mfa/backup-codes/regenerate/",
            {"token": "000000"},
        )
        # Ungültiger Token → Redirect zurück in die Settings ohne Neugenerierung.
        assert response.status_code == 302
        assert response.url == "/mfa/settings/"
        # Storage contains keyed digests; ``first`` contains the cleartext codes.
        from core.services.security import _hash_code

        still = StaticDevice.objects.get(user=staff_user, name="backup").token_set.values_list("token", flat=True)
        assert set(still) == {_hash_code(c) for c in first}

    def test_regenerate_rotates_codes_on_valid_totp(self, client, staff_user):
        device = TOTPDevice.objects.create(user=staff_user, name="default", confirmed=True)
        first = generate_backup_codes(staff_user)
        self._login_verified(client)

        token = oath_totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits)
        response = client.post(
            "/mfa/backup-codes/regenerate/",
            {"token": f"{token:0{device.digits}d}"},
        )
        assert response.status_code == 302
        assert response.url == "/mfa/backup-codes/"
        # L2 (Refs #1375): Codes liegen verschlüsselt in der Session — entschlüsseln.
        import json

        from core.services.file_vault import decrypt_field

        stored = client.session.get("mfa_backup_codes")
        assert stored is not None
        assert isinstance(stored, dict) and stored.get("__encrypted__") is True
        new_codes = json.loads(decrypt_field(stored))
        assert set(first).isdisjoint(set(new_codes))
        assert AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.BACKUP_CODES_REGENERATED).exists()
