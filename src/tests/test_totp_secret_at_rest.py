"""TOTP-Secret at rest verschlüsselt (Refs #1362 / ADR-031).

Deckt die harten Akzeptanzkriterien ab:

1. Idempotente, reversible Datenmigration (Format-Erkennung Fernet vs. 40-Hex).
2. 2FA-Verify über den ECHTEN Login-Pfad — vor UND nach der Migration.
3. Roher DB-Wert (raw SQL) enthält kein Klartext-Hex-Secret.
4. Key-Rotation über MultiFernet bleibt möglich.
5. Setup-/Provisioning-Flow (config_url/QR) funktioniert weiter.
6. Kein Klartext-Leak über den Custom-Admin (Device ist dort nicht registriert).

Die Admin-Rollen sind MFA-pflichtig — ein Migrationsfehler würde alle Admins
aussperren. Daher die expliziten Roundtrip-Tests: Klartext→migriert→verify ok;
frisches Device→verify ok; Migration 2×→verify ok.
"""

import base64
import importlib
import re
from io import StringIO

import pytest
from cryptography.fernet import Fernet
from django.apps import apps as global_apps
from django.core.management import call_command
from django.db import connection
from django.test import override_settings
from django_otp.oath import totp as oath_totp
from django_otp.plugins.otp_totp.models import TOTPDevice

from core.models import EncryptedTOTPDevice
from core.services.security.totp import (
    decrypt_totp_key,
    encrypt_totp_key,
    is_encrypted_totp_key,
)

# Migrationsmodul hat einen ziffernstartigen Namen — nur via importlib ladbar.
_migration = importlib.import_module("core.migrations.0101_totp_secret_at_rest")

# Klartext-Default-Key: random_hex(20) → 40 Kleinbuchstaben-Hex.
_HEX40 = re.compile(r"\A[0-9a-f]{40}\Z")


def _raw_key(device_id: int) -> str:
    """Liest die ``key``-Spalte roh aus der DB (umgeht jede Modell-Entschlüsselung)."""
    with connection.cursor() as cur:
        cur.execute("SELECT key FROM otp_totp_totpdevice WHERE id = %s", [device_id])
        return cur.fetchone()[0]


def _valid_token(device) -> str:
    """Berechnet einen gültigen TOTP-Code aus dem (entschlüsselten) Secret."""
    token = oath_totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits)
    return f"{token:0{device.digits}d}"


class TestTotpKeyHelpers:
    """Format-Erkennung + Krypto-Roundtrip (kein DB-Zugriff)."""

    def test_plain_hex_is_not_encrypted(self):
        assert is_encrypted_totp_key("a" * 40) is False

    def test_fernet_token_is_encrypted(self):
        assert is_encrypted_totp_key(encrypt_totp_key("ab" * 20)) is True

    def test_empty_value_is_not_encrypted(self):
        assert is_encrypted_totp_key("") is False

    def test_encrypt_decrypt_roundtrip(self):
        plain = "abcdef0123456789abcdef0123456789abcdef01"
        assert decrypt_totp_key(encrypt_totp_key(plain)) == plain

    def test_decrypt_passes_plaintext_through(self):
        """Unmigrierter Klartext-Key wird unverändert zurückgegeben (Vor-Migration-Pfad)."""
        plain = "ab" * 20
        assert decrypt_totp_key(plain) == plain

    def test_token_never_matches_plain_hex(self):
        tok = encrypt_totp_key("00" * 20)
        assert not _HEX40.match(tok)
        assert tok.startswith("gA")  # Fernet-Versionsbyte 0x80


class TestMultiFernetRotation:
    """Requirement 4: Key-Rotation über MultiFernet bleibt möglich."""

    def test_old_token_readable_after_key_added_then_rewrapped(self):
        old_key = Fernet.generate_key().decode()
        new_key = Fernet.generate_key().decode()
        plain = "12ab" * 10  # 40 Hex

        with override_settings(ENCRYPTION_KEY="", ENCRYPTION_KEYS=old_key):
            token_old = encrypt_totp_key(plain)

        # Neuer Primärschlüssel voran, alter noch in der Liste → Alt-Token lesbar.
        with override_settings(ENCRYPTION_KEY="", ENCRYPTION_KEYS=f"{new_key},{old_key}"):
            assert decrypt_totp_key(token_old) == plain
            token_new = encrypt_totp_key(decrypt_totp_key(token_old))  # Re-Wrap

        # Alter Schlüssel entfernt: neues Token weiter lesbar, altes nicht mehr.
        with override_settings(ENCRYPTION_KEY="", ENCRYPTION_KEYS=new_key):
            assert decrypt_totp_key(token_new) == plain
            with pytest.raises(Exception):
                decrypt_totp_key(token_old)


@pytest.mark.django_db
class TestEncryptedTotpDeviceModel:
    """Modell verschlüsselt beim Schreiben, entschlüsselt transparent beim Lesen."""

    def test_create_stores_ciphertext_not_plaintext(self, staff_user):
        device = EncryptedTOTPDevice.objects.create(user=staff_user, name="d", confirmed=True)
        raw = _raw_key(device.id)
        # Requirement 3: roher DB-Wert ist KEIN Klartext-Hex-Secret.
        assert not _HEX40.match(raw)
        assert is_encrypted_totp_key(raw)
        # Entschlüsselt zurück zu einem 40-Hex-Secret; verify funktioniert.
        assert _HEX40.match(decrypt_totp_key(raw))
        assert device.verify_token(_valid_token(device)) is True

    def test_config_url_uses_decrypted_secret(self, staff_user):
        """Requirement 5: Provisioning-URI (QR) baut auf dem entschlüsselten Secret auf."""
        device = EncryptedTOTPDevice.objects.create(user=staff_user, name="d", confirmed=False)
        url = device.config_url
        assert url.startswith("otpauth://totp/")
        match = re.search(r"secret=([A-Z2-7]+)", url)
        assert match, url
        secret_b32 = match.group(1)
        padding = "=" * (-len(secret_b32) % 8)
        assert base64.b32decode(secret_b32 + padding) == device.bin_key

    def test_save_does_not_double_encrypt(self, staff_user):
        device = EncryptedTOTPDevice.objects.create(user=staff_user, name="d", confirmed=True)
        raw1 = _raw_key(device.id)
        device.save()  # erneut speichern
        raw2 = _raw_key(device.id)
        # Kein Doppel-Wrap: einmal entschlüsseln liefert Hex (kein weiteres Token).
        assert _HEX40.match(decrypt_totp_key(raw2))
        assert decrypt_totp_key(raw2) == decrypt_totp_key(raw1)


@pytest.mark.django_db
class TestVerifyLoginPath:
    """Requirement 2: 2FA-Verify über den echten Login-Pfad (View, nicht nur Model)."""

    def test_fresh_encrypted_device_verifies(self, client, staff_user):
        device = EncryptedTOTPDevice.objects.create(user=staff_user, name="d", confirmed=True)
        client.login(username="teststaff", password="testpass123")
        resp = client.post("/mfa/verify/", {"token": _valid_token(device)})
        assert resp.status_code == 302
        assert resp.url == "/"
        assert client.session.get("mfa_verified") is True

    def test_legacy_plaintext_device_still_verifies(self, client, staff_user):
        """Vor-Migrations-Zustand: Klartext-Device verifiziert über den Proxy weiter."""
        device = TOTPDevice.objects.create(user=staff_user, name="legacy", confirmed=True)
        assert _HEX40.match(_raw_key(device.id))  # wirklich Klartext at rest
        token = _valid_token(device)
        client.login(username="teststaff", password="testpass123")
        resp = client.post("/mfa/verify/", {"token": token})
        assert resp.status_code == 302
        assert client.session.get("mfa_verified") is True

    def test_setup_get_persists_encrypted_secret(self, client, staff_user):
        """Requirement 5: Der Setup-GET legt ein Device an — mit verschlüsseltem Secret."""
        client.login(username="teststaff", password="testpass123")
        resp = client.get("/mfa/setup/")
        assert resp.status_code == 200
        device = EncryptedTOTPDevice.objects.get(user=staff_user, confirmed=False)
        raw = _raw_key(device.id)
        assert not _HEX40.match(raw)
        assert is_encrypted_totp_key(raw)


@pytest.mark.django_db
class TestDataMigration:
    """Requirement 1: idempotente, reversible In-Place-Verschlüsselung."""

    def _legacy_device(self, user):
        # Basis-Modell schreibt Klartext (kein Proxy-save) → simuliert Altbestand.
        return TOTPDevice.objects.create(user=user, name="legacy", confirmed=True)

    def test_forward_encrypts_in_place(self, staff_user):
        device = self._legacy_device(staff_user)
        plain_before = _raw_key(device.id)
        assert _HEX40.match(plain_before)

        _migration.encrypt_existing_totp_keys(global_apps, None)

        enc = _raw_key(device.id)
        assert is_encrypted_totp_key(enc)
        assert decrypt_totp_key(enc) == plain_before  # Secret unverändert

    def test_forward_is_idempotent(self, staff_user):
        device = self._legacy_device(staff_user)
        plain_before = _raw_key(device.id)

        _migration.encrypt_existing_totp_keys(global_apps, None)
        enc1 = _raw_key(device.id)
        _migration.encrypt_existing_totp_keys(global_apps, None)  # 2. Lauf
        enc2 = _raw_key(device.id)

        assert enc2 == enc1  # kein erneutes Wrappen
        assert decrypt_totp_key(enc2) == plain_before

    def test_reverse_restores_plaintext_and_is_idempotent(self, staff_user):
        device = self._legacy_device(staff_user)
        plain_before = _raw_key(device.id)

        _migration.encrypt_existing_totp_keys(global_apps, None)
        assert is_encrypted_totp_key(_raw_key(device.id))

        _migration.decrypt_existing_totp_keys(global_apps, None)
        assert _raw_key(device.id) == plain_before
        _migration.decrypt_existing_totp_keys(global_apps, None)  # nochmals
        assert _raw_key(device.id) == plain_before

    def test_verify_survives_migration_roundtrip(self, client, staff_user):
        """Klartext → migriert (2×) → echter Login-Verify bleibt gültig."""
        device = self._legacy_device(staff_user)
        token = _valid_token(device)  # aus Klartext-Secret VOR Migration

        _migration.encrypt_existing_totp_keys(global_apps, None)
        _migration.encrypt_existing_totp_keys(global_apps, None)

        client.login(username="teststaff", password="testpass123")
        resp = client.post("/mfa/verify/", {"token": token})
        assert resp.status_code == 302
        assert client.session.get("mfa_verified") is True


@pytest.mark.django_db
class TestReencryptCommandRewrapsTotp:
    """Requirement 4: ``reencrypt_fields`` wickelt TOTP-Secrets aktiv um (Rotation)."""

    def test_rewrap_preserves_secret_and_verify(self, staff_user):
        device = EncryptedTOTPDevice.objects.create(user=staff_user, name="d", confirmed=True)
        raw_before = _raw_key(device.id)
        plain = decrypt_totp_key(raw_before)

        call_command("reencrypt_fields")

        raw_after = _raw_key(device.id)
        # Fernet-Token trägt IV+Timestamp → Neu-Wrap ergibt ein anderes Token …
        assert raw_after != raw_before
        # … das aber dasselbe Secret trägt, und der Verify-Pfad bleibt gültig.
        assert decrypt_totp_key(raw_after) == plain
        device.refresh_from_db()
        assert device.verify_token(_valid_token(device)) is True


@pytest.mark.django_db
class TestVerifySaveDoesNotClobberKey:
    """Root-cause der Rewrap-Race (Review-Befund #1362): der django-otp-Verify-
    Voll-``save()`` darf die ``key``-Spalte niemals (zurück)schreiben.

    ``verify_token`` schreibt auf Erfolg (``throttle_reset(commit=False)`` +
    ``save()``) wie auf Fehlschlag (``throttle_increment(commit=True)``) ein
    Voll-``save()`` inkl. ``key``. Träfe dabei ein aus der DB geladenes,
    veraltetes Alt-Key-Token nach einem parallelen ``reencrypt_fields``-Rewrap in
    die Spalte, bliebe das Gerät still unter dem Alt-Key → MFA-Lockout nach
    Entfernen des Alt-Schlüssels. Der Proxy schließt das an der Quelle.
    """

    def test_stale_full_save_after_rewrap_does_not_clobber_key(self, staff_user):
        """Der exakte Race-Fall: eine Instanz mit VERALTETEM Alt-Key-Token im
        Speicher schreibt ihr Voll-``save()`` NACH dem Rewrap — ``key`` bleibt
        das frisch umgewickelte Token, nur die Throttle-Felder werden persistiert."""
        old_key = Fernet.generate_key().decode()
        new_key = Fernet.generate_key().decode()
        with override_settings(ENCRYPTION_KEY="", ENCRYPTION_KEYS=old_key):
            device = EncryptedTOTPDevice.objects.create(user=staff_user, name="d", confirmed=True)
            device_id = device.id
            raw_old = _raw_key(device_id)
            plain = decrypt_totp_key(raw_old)
            # Stale Instanz, wie sie ein paralleler Verify-Request vor dem Rewrap geladen hätte:
            stale = EncryptedTOTPDevice.objects.get(pk=device_id)
            assert stale.key == raw_old  # trägt das Alt-Key-Token im Speicher

        with override_settings(ENCRYPTION_KEY="", ENCRYPTION_KEYS=f"{new_key},{old_key}"):
            call_command("reencrypt_fields")  # Rewrap → Gerät jetzt unter new_key
            raw_new = _raw_key(device_id)
            assert raw_new != raw_old
            assert decrypt_totp_key(raw_new) == plain

            # Der „parallele Verify" schreibt SPÄTER sein Voll-``save()`` (Throttle-Increment).
            stale.throttling_failure_count = (stale.throttling_failure_count or 0) + 1
            stale.save()
            assert _raw_key(device_id) == raw_new  # key UNANGETASTET (kein Alt-Key-Token zurück)
            # … der Throttle-Zähler wurde aber sehr wohl geschrieben (Voll-save wirkt sonst normal):
            assert EncryptedTOTPDevice.objects.get(pk=device_id).throttling_failure_count == 1

        # Harter Beweis: Alt-Schlüssel entfernt → Gerät bleibt entschlüsselbar, kein Lockout.
        with override_settings(ENCRYPTION_KEY="", ENCRYPTION_KEYS=new_key):
            assert decrypt_totp_key(_raw_key(device_id)) == plain

    def test_failed_verify_does_not_rewrite_key(self, staff_user):
        """Fehlschlag → ``throttle_increment(commit=True)`` → Voll-``save()``: key bleibt."""
        device = EncryptedTOTPDevice.objects.create(user=staff_user, name="d", confirmed=True)
        raw_before = _raw_key(device.id)
        wrong = f"{(int(_valid_token(device)) + 1) % 1_000_000:06d}"
        assert device.verify_token(wrong) is False
        assert _raw_key(device.id) == raw_before  # key unverändert
        device.refresh_from_db()
        assert device.throttling_failure_count >= 1  # Throttle wurde persistiert

    def test_successful_verify_does_not_rewrite_key(self, staff_user):
        """Erfolg → ``throttle_reset(commit=False)`` + Voll-``save()``: key bleibt."""
        device = EncryptedTOTPDevice.objects.create(user=staff_user, name="d", confirmed=True)
        raw_before = _raw_key(device.id)
        assert device.verify_token(_valid_token(device)) is True
        assert _raw_key(device.id) == raw_before

    def test_full_save_still_encrypts_legacy_plaintext_key(self, staff_user):
        """Regression: ein Voll-``save()`` auf ein Klartext-Legacy-Device
        verschlüsselt das Secret weiterhin (der Klartext-Zweig schreibt key)."""
        legacy = TOTPDevice.objects.create(user=staff_user, name="legacy", confirmed=True)
        assert _HEX40.match(_raw_key(legacy.id))
        proxy = EncryptedTOTPDevice.objects.get(pk=legacy.id)
        assert _HEX40.match(proxy.key)  # Klartext in memory
        proxy.save()  # Voll-save()
        raw = _raw_key(legacy.id)
        assert is_encrypted_totp_key(raw)
        assert _HEX40.match(decrypt_totp_key(raw))  # Secret erhalten


@pytest.mark.django_db
class TestReencryptTotpRewrapRace:
    """Command-seitige Defense-in-Depth des Rewraps (Review-Befund #1362).

    Der eigentliche Verursacher (django-otp-Verify-Voll-``save()``) ist bereits
    im Proxy geschlossen (siehe ``TestVerifySaveDoesNotClobberKey``). Der Command
    härtet den Rewrap zusätzlich gegen **jeden anderen** parallelen Schreiber der
    ``key``-Spalte ab: je Gerät Row-Lock + wertgebundener Compare-and-Swap; ändert
    sich die Zeile zwischen gelocktem Lesen und Schreiben, greift Retry bzw. eine
    laute Meldung. Der fremde Schreiber wird hier per rohem SQL simuliert (umgeht
    jeden Modell-``save``), um die CAS-/Retry-Semantik zu belegen.
    """

    def _make_old_key_device(self, user, old_key):
        """Legt ein Gerät an, dessen Secret unter ``old_key`` gewickelt ist."""
        with override_settings(ENCRYPTION_KEY="", ENCRYPTION_KEYS=old_key):
            device = EncryptedTOTPDevice.objects.create(user=user, name="d", confirmed=True)
            raw = _raw_key(device.id)
            return device.id, raw, decrypt_totp_key(raw)

    def test_interleaved_foreign_writeback_is_detected_and_healed(self, staff_user, monkeypatch):
        """Interleave: ein zwischen Read und Write eingeschobener fremder
        key-Writeback (Alt-Key-Token) wird per CAS erkannt (0 Zeilen) und per
        Retry geheilt — danach liegt KEIN Alt-Key-Token mehr in der DB."""
        old_key = Fernet.generate_key().decode()
        new_key = Fernet.generate_key().decode()
        device_id, raw_before, plain = self._make_old_key_device(staff_user, old_key)

        # Ein frisches, ANDERES Alt-Key-Token desselben Secrets — das, was ein
        # zwischen Lesen und CAS laufender fremder key-Schreiber hinterließe.
        stale_old_token = Fernet(old_key.encode()).encrypt(plain.encode()).decode()
        assert stale_old_token != raw_before  # Fernet-IV/Timestamp → andere Bytes

        import core.services.security.totp as totp_mod

        real_encrypt = totp_mod.encrypt_totp_key
        state = {"fired": False}

        def clobbering_encrypt(value):
            result = real_encrypt(value)
            if not state["fired"]:
                state["fired"] = True
                # Simuliert einen parallelen fremden key-Schreiber: schiebt ein
                # Alt-Key-Token zwischen unser gelocktes Lesen und den CAS-``update``.
                with connection.cursor() as cur:
                    cur.execute(
                        "UPDATE otp_totp_totpdevice SET key = %s WHERE id = %s",
                        [stale_old_token, device_id],
                    )
            return result

        monkeypatch.setattr(totp_mod, "encrypt_totp_key", clobbering_encrypt)

        with override_settings(ENCRYPTION_KEY="", ENCRYPTION_KEYS=f"{new_key},{old_key}"):
            call_command("reencrypt_fields")
            raw_after = _raw_key(device_id)
            assert state["fired"] is True  # Interleave wurde wirklich ausgelöst
            # Kein Alt-Key-Token geblieben (weder das ursprüngliche noch das eingeschobene).
            assert raw_after not in (raw_before, stale_old_token)
            assert decrypt_totp_key(raw_after) == plain  # Secret unverändert

        # Harter Beweis: Alt-Schlüssel entfernt → neues Token lesbar, beide Alt-Tokens tot.
        with override_settings(ENCRYPTION_KEY="", ENCRYPTION_KEYS=new_key):
            assert decrypt_totp_key(raw_after) == plain
            with pytest.raises(Exception):
                decrypt_totp_key(raw_before)
            with pytest.raises(Exception):
                decrypt_totp_key(stale_old_token)

    def test_persistent_contention_is_reported_not_silently_dropped(self, staff_user, monkeypatch):
        """Bleibt die Zeile bei JEDEM Versuch fremdbeschrieben, zählt der Command
        das Gerät NICHT als umgewickelt, sondern meldet die pk laut auf stderr —
        damit der Operator den Alt-Schlüssel NICHT voreilig entfernt."""
        old_key = Fernet.generate_key().decode()
        new_key = Fernet.generate_key().decode()
        device_id, raw_before, plain = self._make_old_key_device(staff_user, old_key)

        import core.services.security.totp as totp_mod

        real_encrypt = totp_mod.encrypt_totp_key

        def always_clobber(value):
            result = real_encrypt(value)
            # Jeder Versuch wird sabotiert: neues Alt-Key-Token (andere Bytes) →
            # der wertgebundene CAS-Filter trifft nie zu.
            fresh_old = Fernet(old_key.encode()).encrypt(plain.encode()).decode()
            with connection.cursor() as cur:
                cur.execute(
                    "UPDATE otp_totp_totpdevice SET key = %s WHERE id = %s",
                    [fresh_old, device_id],
                )
            return result

        monkeypatch.setattr(totp_mod, "encrypt_totp_key", always_clobber)

        out, err = StringIO(), StringIO()
        with override_settings(ENCRYPTION_KEY="", ENCRYPTION_KEYS=f"{new_key},{old_key}"):
            call_command("reencrypt_fields", stdout=out, stderr=err)

        # Nicht stillschweigend als „umgewickelt" gezählt …
        assert "0 TOTP devices" in out.getvalue()
        # … sondern laut gemeldet, mit Geräte-pk, damit der Operator den Alt-Key hält.
        warning = err.getvalue()
        assert "TOTP-Rewrap" in warning
        assert str(device_id) in warning

    def test_rewrap_moves_device_off_old_key(self, staff_user):
        """Basisfall ohne Race: der Command wickelt das Secret nachweislich vom
        alten auf den neuen Primärschlüssel um (nichts bleibt unter dem Alt-Key)."""
        old_key = Fernet.generate_key().decode()
        new_key = Fernet.generate_key().decode()
        device_id, raw_before, plain = self._make_old_key_device(staff_user, old_key)

        with override_settings(ENCRYPTION_KEY="", ENCRYPTION_KEYS=f"{new_key},{old_key}"):
            call_command("reencrypt_fields")
            raw_after = _raw_key(device_id)
        assert raw_after != raw_before

        with override_settings(ENCRYPTION_KEY="", ENCRYPTION_KEYS=new_key):
            assert decrypt_totp_key(raw_after) == plain
            with pytest.raises(Exception):
                decrypt_totp_key(raw_before)


@pytest.mark.django_db
class TestTotpKeyColumnWidthGuard:
    """Upgrade-Hazard-Guard (ADR-031 / security-notes, Review-Befund #1362).

    Ein künftiges ``django-otp``-Release (Dependabot bumpt automatisch) könnte
    eine ``otp_totp``-Migration mit ``AlterField(key, max_length=80)`` liefern —
    Django erzeugte daraus ``ALTER … TYPE varchar(80)``, das an den bereits
    gespeicherten ~140-Zeichen-Fernet-Tokens hart scheitert und ``migrate``
    blockiert. Dieser Guard fängt eine unbeabsichtigte Re-Verengung der Spalte
    früh (im Test-DB-Schema nach ``migrate``), bevor sie produktiv aufschlägt.
    """

    def test_key_column_is_wide_enough_for_fernet_token(self):
        required = len(encrypt_totp_key("a" * 40))  # Fernet-Token über 40-Hex-Secret (~140)
        with connection.cursor() as cur:
            cur.execute(
                "SELECT character_maximum_length FROM information_schema.columns "
                "WHERE table_name = 'otp_totp_totpdevice' AND column_name = 'key'"
            )
            width = cur.fetchone()[0]
        assert width is not None and width >= required, (
            f"otp_totp_totpdevice.key ist nur varchar({width}) — ein Fernet-TOTP-Token "
            f"({required} Zeichen) passt nicht mehr hinein. Verdacht: ein django-otp-Upgrade "
            "hat die Spalte per AlterField(max_length=80) re-verengt. Siehe ADR-031 / "
            "docs/security-notes.md (Gegenmaßnahme: core-Nachfolge-Migration, die wieder "
            "auf 255 weitet)."
        )


class TestNoAdminLeak:
    """Requirement 6: Das Device taucht nicht im Custom-Admin auf → kein Leak dort."""

    def test_totp_device_not_registered_in_custom_admin(self):
        from core.admin_site import anlaufstelle_admin_site

        registered = set(anlaufstelle_admin_site._registry)
        assert EncryptedTOTPDevice not in registered
        assert TOTPDevice not in registered
