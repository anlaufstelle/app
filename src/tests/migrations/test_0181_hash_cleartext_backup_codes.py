"""Direkte Testabdeckung der RunPython-Datenmigration 0181 (Refs #2327).

``0181_hash_cleartext_backup_codes`` hasht den Restbestand an Backup-Codes, die
noch aus der Zeit vor der Hash-Speicherung (Refs #790) im Klartext in
``otp_static_statictoken`` liegen — historisches Erzeugungsformat
``^[0-9a-f]{4}-[0-9a-f]{4}$``. Erst danach darf ``verify_backup_code`` den
Klartext-Vergleich ersatzlos verlieren, ohne dass ein Konto mit alten Codes
seinen Recovery-Kanal verliert.

Zusagen, die hier haengen:

1. Altformat-Zeilen des Backup-Geraets tragen danach ``_hash_code(<klartext>)``;
   bereits gehashte Zeilen bleiben byte-gleich, die Zeilenzahl je Geraet auch.
2. Nur das Backup-Geraet (Name ``backup``) wird angefasst — ein anderes
   statisches Geraet vergleicht seine Tokens ueber django-otp im Klartext und
   waere nach einem Hash unbrauchbar.
3. Ein zweiter Lauf aendert nichts (Idempotenz), die Zaehlzeile im
   migrate-Output nennt die Zahl der gehashten Zeilen.
4. Ein alter Klartext-Code verifiziert danach ueber den Hash-Pfad.
5. Die eingefrorene Hash-Funktion der Migration ist identisch mit
   ``_hash_code`` des Service-Moduls.

Muster: ``test_0145_backfill_report_key`` / ``test_0120_…`` (Idempotenz).
"""

from __future__ import annotations

import contextlib
import importlib
import io
import re

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django_test_migrations.contrib.unittest_case import MigratorTestCase

from core.services.security.mfa import _hash_code

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
        self.gehasht_a_pk = StaticToken.objects.create(device=backup_a, token=_hash_code(_NEU_CODE)).pk

        # Zweites Konto: ein bereits gehashter Backup-Code — bleibt unangetastet.
        backup_b = StaticDevice.objects.create(user=konto_b, name="backup", confirmed=True)
        self.backup_b_pk = backup_b.pk
        self.gehasht_b_pk = StaticToken.objects.create(device=backup_b, token=_hash_code("Bbbbbbbbbbbbbbbbbbbbb2")).pk
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
        assert self._token(self.alt_1_pk) == _hash_code(_ALT_CODE_1)
        assert self._token(self.alt_2_pk) == _hash_code(_ALT_CODE_2)

    def test_gehashte_zeilen_bleiben_unveraendert(self):
        assert self._token(self.gehasht_a_pk) == _hash_code(_NEU_CODE)
        assert self._token(self.gehasht_b_pk) == _hash_code("Bbbbbbbbbbbbbbbbbbbbb2")

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

    def test_alter_klartext_code_verifiziert_ueber_den_hash_pfad(self):
        """Der Konto-Nutzer tippt seinen alten Code ein — nach der Migration
        liegt nur noch dessen Hash in der Tabelle, ein Treffer kann also nur
        ueber den Hash-Kandidaten zustande kommen. Danach ist er verbraucht."""
        from core.services.security import remaining_backup_codes, verify_backup_code

        assert not self._tokens().objects.filter(token=_ALT_CODE_1, device_id=self.backup_a_pk).exists()
        konto = get_user_model().objects.get(pk=self.konto_a_pk)

        assert verify_backup_code(konto, _ALT_CODE_1) is True
        assert remaining_backup_codes(konto) == 2
        assert verify_backup_code(konto, _ALT_CODE_1) is False


@pytest.mark.parametrize(
    "code",
    [_ALT_CODE_1, _ALT_CODE_2, "0000-0000", "ffff-ffff", _NEU_CODE, "", "Ä-ß"],
)
def test_eingefrorene_hashfunktion_entspricht_dem_service(code):
    """Die Migration friert die Hash-Bildung ein, statt den Service zu importieren.

    Aendert sich ``_hash_code`` spaeter, faellt das hier auf — dann braucht der
    Restbestand eine eigene Folgemigration, und 0181 bleibt der historische
    Stand, der zum Zeitpunkt ihres Laufs galt.
    """
    assert _migration()._hash_code(code) == _hash_code(code)
