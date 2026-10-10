"""The historical cleartext migration stays frozen; legacy codes are now rejected."""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import io
import re

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django_test_migrations.contrib.unittest_case import MigratorTestCase

_MIGRATION_NAME = "0181_hash_cleartext_backup_codes"

#: Zwei Klartext-Codes im historischen Format (``secrets.token_hex(2)``-Paare).
_ALT_CODE_1 = "abcd-1234"
_ALT_CODE_2 = "0f0f-a1b2"
#: Ein bereits nach Refs #790 ausgegebener Code — seine Zeile ist schon gehasht.
_NEU_CODE = "Hhhhhhhhhhhhhhhhhhhhh1"
_ZAEHLZEILE_RE = re.compile(r"\[0181_hash_cleartext_backup_codes\] (\d+) Klartext-Backup-Code")


def _migration():
    return importlib.import_module(f"core.migrations.{_MIGRATION_NAME}")


class TestMigrate0181HashLegacyBackupCodes(MigratorTestCase):
    migrate_from = [("core", "0180_rls_shifthandovernote"), ("otp_static", "0003_add_timestamps")]
    migrate_to = ("core", _MIGRATION_NAME)

    def setUp(self):
        # Die Zaehlzeile schreibt die Migration per ``print`` in den
        # migrate-Output — hier mitgeschnitten, um sie zu pruefen.
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            super().setUp()
        self.migrate_output = buffer.getvalue()

    def prepare(self):
        apps = self.old_state.apps
        User = apps.get_model("core", "User")
        StaticDevice = apps.get_model("otp_static", "StaticDevice")
        StaticToken = apps.get_model("otp_static", "StaticToken")

        konto_a = User.objects.create(username="mig-0181-a", password="!")
        konto_b = User.objects.create(username="mig-0181-b", password="!")
        self.konto_a_pk = konto_a.pk

        backup_a = StaticDevice.objects.create(user=konto_a, name="backup", confirmed=True)
        self.backup_a_pk = backup_a.pk
        self.alt_1_pk = StaticToken.objects.create(device=backup_a, token=_ALT_CODE_1).pk
        self.alt_2_pk = StaticToken.objects.create(device=backup_a, token=_ALT_CODE_2).pk
        self.hashed_a_pk = StaticToken.objects.create(device=backup_a, token=_migration()._hash_code(_NEU_CODE)).pk

        # Zweites Konto: ein bereits gehashter Backup-Code — bleibt unangetastet.
        backup_b = StaticDevice.objects.create(user=konto_b, name="backup", confirmed=True)
        self.backup_b_pk = backup_b.pk
        self.hashed_b_pk = StaticToken.objects.create(
            device=backup_b, token=_migration()._hash_code("Bbbbbbbbbbbbbbbbbbbbb2")
        ).pk
        # Fremdes statisches Geraet mit einem Token in Altform: django-otp
        # vergleicht es im Klartext, ein Hash machte es unbrauchbar.
        fremd_b = StaticDevice.objects.create(user=konto_b, name="other", confirmed=True)
        self.fremd_b_pk = fremd_b.pk
        self.fremd_token_pk = StaticToken.objects.create(device=fremd_b, token=_ALT_CODE_1).pk

    def _tokens(self):
        return self.new_state.apps.get_model("otp_static", "StaticToken")

    def _token(self, pk):
        return self._tokens().objects.get(pk=pk).token

    def _stand(self):
        return dict(self._tokens().objects.values_list("pk", "token"))

    def test_altformat_zeilen_tragen_den_hash(self):
        assert self._token(self.alt_1_pk) == _migration()._hash_code(_ALT_CODE_1)
        assert self._token(self.alt_2_pk) == _migration()._hash_code(_ALT_CODE_2)

    def test_gehashte_zeilen_bleiben_unveraendert(self):
        assert self._token(self.hashed_a_pk) == _migration()._hash_code(_NEU_CODE)
        assert self._token(self.hashed_b_pk) == _migration()._hash_code("Bbbbbbbbbbbbbbbbbbbbb2")

    def test_fremdes_statisches_geraet_bleibt_im_klartext(self):
        assert self._token(self.fremd_token_pk) == _ALT_CODE_1

    def test_zeilenzahl_je_geraet_bleibt_gleich(self):
        tokens = self._tokens().objects
        assert tokens.filter(device_id=self.backup_a_pk).count() == 3
        assert tokens.filter(device_id=self.backup_b_pk).count() == 1
        assert tokens.filter(device_id=self.fremd_b_pk).count() == 1

    def test_kein_klartext_im_backup_geraet_mehr(self):
        restbestand = self._tokens().objects.filter(
            device__name="backup",
            token__regex=r"^[0-9a-f]{4}-[0-9a-f]{4}$",
        )
        assert not restbestand.exists(), list(restbestand.values_list("token", flat=True))

    def test_zaehlzeile_nennt_die_gehashten_zeilen(self):
        treffer = _ZAEHLZEILE_RE.findall(self.migrate_output)
        assert treffer == ["2"], self.migrate_output

    def test_zweiter_lauf_aendert_nichts(self):
        vorher = self._stand()
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), connection.schema_editor() as schema_editor:
            _migration().hash_cleartext_backup_codes(self.new_state.apps, schema_editor)
        assert self._stand() == vorher
        assert _ZAEHLZEILE_RE.findall(buffer.getvalue()) == ["0"], buffer.getvalue()

    def test_migrated_legacy_code_is_rejected_by_current_service(self):
        """Historical hashes remain stored, but weak codes no longer authenticate."""
        from core.services.security import remaining_backup_codes, verify_backup_code

        assert not self._tokens().objects.filter(token=_ALT_CODE_1, device_id=self.backup_a_pk).exists()
        user = get_user_model().objects.get(pk=self.konto_a_pk)
        assert remaining_backup_codes(user) == 3
        assert verify_backup_code(user, _ALT_CODE_1) is False
        assert remaining_backup_codes(user) == 3


@pytest.mark.parametrize(
    "code",
    [_ALT_CODE_1, _ALT_CODE_2, "0000-0000", "ffff-ffff", _NEU_CODE, "", "Ä-ß"],
)
def test_frozen_hash_function_keeps_historical_sha256_contract(code):
    """The historical migration keeps SHA-256 despite the new keyed service."""
    assert _migration()._hash_code(code) == hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]
