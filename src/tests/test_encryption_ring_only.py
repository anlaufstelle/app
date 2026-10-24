"""Field encryption is configured through the key ring ``ENCRYPTION_KEYS`` only.

The single-key variable ``ENCRYPTION_KEY`` is gone. A non-empty value must stop
the start in every settings module with a clear rename hint instead of being
ignored silently, and the key ring (first entry encrypts, all entries decrypt)
is the only source for the Fernet keys and everything derived from them.

Settings guards only fire at import time, so they run in a subprocess with a
controlled environment (same strategy as ``test_prod_settings.py``).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.checks import run_checks
from django.test import override_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"

KEY = "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZmE="
OTHER_KEY = "cXV4cXV4cXV4cXV4cXV4cXV4cXV4cXV4cXV4cXV4cXU="
# Real Fernet keys (32 url-safe base64 bytes) for the tests that encrypt for real;
# KEY above only has to be a stable string for settings and pepper derivation.
FERNET_A = "Y29yZ2Vjb3JnZWNvcmdlY29yZ2Vjb3JnZWNvcmdlY28="
FERNET_B = OTHER_KEY

ALL_MODULES = ["base", "dev", "test", "e2e", "prod", "devlive", "demo"]
LOCAL_MODULES = ["dev", "test", "e2e"]
HARDENED_MODULES = ["prod", "devlive", "demo"]

_BASE_ENV = {
    "DJANGO_SECRET_KEY": "x" * 50,
    "DJANGO_AUDIT_HASH_KEY": "y" * 50,
    "ALLOWED_HOSTS": "demo.anlaufstelle.app,localhost,127.0.0.1",
    "POSTGRES_DB": "x",
    "POSTGRES_USER": "u",
    "POSTGRES_PASSWORD": "p" * 40,
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5432",
    # Never let a developer's local .env leak a key into these assertions.
    "DJANGO_LOAD_DOTENV": "false",
    "ENCRYPTION_KEY": "",
    "ENCRYPTION_KEYS": "",
    "REQUIRE_ENCRYPTION": "",
    "LOCAL_ENCRYPTION_KEY_FILE": "",
}


def _run_settings(module: str, env_overrides: dict | None = None, expr: str | None = None):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(SRC_DIR),
        "DJANGO_SETTINGS_MODULE": f"anlaufstelle.settings.{module}",
        **_BASE_ENV,
        **(env_overrides or {}),
    }
    code = expr or f"import anlaufstelle.settings.{module}  # noqa"
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


class TestSingleKeyIsRejected:
    @pytest.mark.parametrize("module", ALL_MODULES)
    def test_single_key_alone_stops_the_start(self, module):
        result = _run_settings(module, {"ENCRYPTION_KEY": KEY})

        assert result.returncode != 0
        assert "ImproperlyConfigured" in result.stderr
        assert "ENCRYPTION_KEYS" in result.stderr
        assert KEY not in result.stderr

    @pytest.mark.parametrize("module", ALL_MODULES)
    def test_single_key_next_to_a_ring_is_not_silently_ignored(self, module):
        result = _run_settings(module, {"ENCRYPTION_KEY": OTHER_KEY, "ENCRYPTION_KEYS": KEY})

        assert result.returncode != 0
        assert "ImproperlyConfigured" in result.stderr
        assert OTHER_KEY not in result.stderr
        assert KEY not in result.stderr

    @pytest.mark.parametrize("module", ALL_MODULES)
    def test_rejection_names_the_rename(self, module):
        result = _run_settings(module, {"ENCRYPTION_KEY": KEY})

        assert "ENCRYPTION_KEY " in result.stderr or "ENCRYPTION_KEY wurde" in result.stderr
        assert "ENCRYPTION_KEYS" in result.stderr

    @pytest.mark.parametrize("module", LOCAL_MODULES)
    def test_blank_single_key_counts_as_unset(self, module):
        result = _run_settings(module, {"ENCRYPTION_KEY": "   ", "ENCRYPTION_KEYS": KEY})

        assert result.returncode == 0, result.stderr


class TestRingOnlyStarts:
    @pytest.mark.parametrize("module", ALL_MODULES)
    def test_ring_alone_boots(self, module):
        result = _run_settings(module, {"ENCRYPTION_KEYS": KEY})

        assert result.returncode == 0, result.stderr

    @pytest.mark.parametrize("module", ALL_MODULES)
    def test_ring_with_two_entries_keeps_order(self, module):
        result = _run_settings(
            module,
            {"ENCRYPTION_KEYS": f"{KEY},{OTHER_KEY}"},
            expr=(
                f"from anlaufstelle.settings.{module} import ENCRYPTION_KEYS as r;"
                "print(r)"  # the ring is a plain string; the parser lives in the service
            ),
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == f"{KEY},{OTHER_KEY}"

    @pytest.mark.parametrize("module", ALL_MODULES)
    def test_settings_expose_no_single_key_attribute(self, module):
        result = _run_settings(
            module,
            {"ENCRYPTION_KEYS": KEY},
            expr=f"import anlaufstelle.settings.{module} as s; print(hasattr(s, 'ENCRYPTION_KEY'))",
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "False"


class TestProdStillFailsClosedWithoutKey:
    @pytest.mark.parametrize("module", HARDENED_MODULES)
    def test_missing_ring_stops_the_start(self, module):
        result = _run_settings(module)

        assert result.returncode != 0
        assert "ENCRYPTION_KEYS" in result.stderr
        assert "ENCRYPTION_KEY oder" not in result.stderr

    @pytest.mark.parametrize("module", HARDENED_MODULES)
    def test_separator_only_ring_stops_the_start(self, module):
        result = _run_settings(module, {"ENCRYPTION_KEYS": " , "})

        assert result.returncode != 0
        assert "The key ring contains no key" in result.stderr


class TestDevWithoutKeyIsUnchanged:
    @pytest.mark.parametrize("module", LOCAL_MODULES)
    def test_no_key_warns_loudly_and_still_starts(self, module):
        result = _run_settings(module)

        assert result.returncode == 0, result.stderr
        assert "PLAINTEXT MODE" in result.stderr
        assert "ENCRYPTION_KEYS" in result.stderr

    @pytest.mark.parametrize("module", LOCAL_MODULES)
    def test_no_key_with_require_encryption_aborts(self, module):
        result = _run_settings(module, {"REQUIRE_ENCRYPTION": "1"})

        assert result.returncode != 0
        assert "REQUIRE_ENCRYPTION" in result.stderr
        assert "ENCRYPTION_KEYS" in result.stderr

    @pytest.mark.parametrize("module", LOCAL_MODULES)
    def test_ring_only_is_not_classified_as_plaintext_mode(self, module):
        result = _run_settings(module, {"ENCRYPTION_KEYS": KEY})

        assert result.returncode == 0, result.stderr
        assert "PLAINTEXT MODE" not in result.stderr

    @pytest.mark.parametrize("module", LOCAL_MODULES)
    def test_ring_only_satisfies_require_encryption(self, module):
        result = _run_settings(module, {"ENCRYPTION_KEYS": KEY, "REQUIRE_ENCRYPTION": "1"})

        assert result.returncode == 0, result.stderr
        assert "PLAINTEXT MODE" not in result.stderr

    @pytest.mark.parametrize("module", LOCAL_MODULES)
    def test_single_key_is_rejected_even_with_require_encryption_satisfied(self, module):
        result = _run_settings(module, {"ENCRYPTION_KEY": KEY, "REQUIRE_ENCRYPTION": "1"})

        assert result.returncode != 0
        assert "ImproperlyConfigured" in result.stderr


class TestLocalKeyFileFillsTheRing:
    @pytest.mark.parametrize("module", LOCAL_MODULES)
    def test_file_content_becomes_the_single_ring_entry(self, module, tmp_path):
        key_file = tmp_path / "local.key"
        key_file.write_text(f"{KEY}\n", encoding="utf-8")

        result = _run_settings(
            module,
            {"LOCAL_ENCRYPTION_KEY_FILE": str(key_file)},
            expr=f"from anlaufstelle.settings.{module} import ENCRYPTION_KEYS as r; print(r)",
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == KEY
        assert "PLAINTEXT MODE" not in result.stderr

    def test_environment_ring_wins_over_the_file(self, tmp_path):
        key_file = tmp_path / "local.key"
        key_file.write_text(OTHER_KEY, encoding="utf-8")

        result = _run_settings(
            "dev",
            {"LOCAL_ENCRYPTION_KEY_FILE": str(key_file), "ENCRYPTION_KEYS": KEY},
            expr="from anlaufstelle.settings.dev import ENCRYPTION_KEYS as r; print(r)",
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == KEY

    def test_missing_file_falls_back_to_the_plaintext_warning(self, tmp_path):
        result = _run_settings("dev", {"LOCAL_ENCRYPTION_KEY_FILE": str(tmp_path / "absent.key")})

        assert result.returncode == 0, result.stderr
        assert "PLAINTEXT MODE" in result.stderr

    def test_missing_file_still_honours_require_encryption(self, tmp_path):
        result = _run_settings(
            "dev",
            {"LOCAL_ENCRYPTION_KEY_FILE": str(tmp_path / "absent.key"), "REQUIRE_ENCRYPTION": "1"},
        )

        assert result.returncode != 0
        assert "REQUIRE_ENCRYPTION" in result.stderr


class TestRuntimeReadsOnlyTheRing:
    def test_single_key_setting_is_not_a_key_source(self):
        from core.services.file_vault.encryption import EncryptionKeyMissing, get_fernet

        with override_settings(ENCRYPTION_KEY=KEY, ENCRYPTION_KEYS=""):
            with pytest.raises(EncryptionKeyMissing) as excinfo:
                get_fernet()

        assert "ENCRYPTION_KEYS" in str(excinfo.value)
        assert "ENCRYPTION_KEY /" not in str(excinfo.value)

    def test_ring_first_entry_encrypts_and_every_entry_decrypts(self):
        from cryptography.fernet import Fernet

        from core.services.file_vault.encryption import decrypt_field, encrypt_field

        legacy_token = {"__encrypted__": True, "value": Fernet(FERNET_B.encode()).encrypt(b"legacy value").decode()}

        with override_settings(ENCRYPTION_KEYS=f"{FERNET_A},{FERNET_B}"):
            token = encrypt_field("fresh value")
            assert decrypt_field(token) == "fresh value"
            assert decrypt_field(legacy_token) == "legacy value"

        with override_settings(ENCRYPTION_KEYS=FERNET_A):
            assert decrypt_field(token) == "fresh value"

    def test_fernet_cache_follows_the_ring_setting(self):
        from core.services.file_vault.encryption import get_fernet

        with override_settings(ENCRYPTION_KEYS=FERNET_A):
            first = get_fernet()
        with override_settings(ENCRYPTION_KEYS=FERNET_B):
            second = get_fernet()

        assert first is not second

    def test_missing_key_message_names_only_the_ring(self):
        from core.services.file_vault.encryption import EncryptionKeyMissing, encrypt_field

        with override_settings(ENCRYPTION_KEYS=""):
            with pytest.raises(Exception) as excinfo:
                encrypt_field("secret")

        assert isinstance(excinfo.value, (EncryptionKeyMissing, Exception))
        assert "ENCRYPTION_KEYS" in str(excinfo.value)
        assert "ENCRYPTION_KEY " not in str(excinfo.value)


class TestDerivedValuesStayByteIdentical:
    """The idempotency pepper derives from the first key; moving a key from the
    single variable into a one-entry ring must not change it (otherwise stored
    fingerprints stop matching and retried requests are replayed twice)."""

    EXPECTED_PEPPER_HEX = "7fdf1f59fff9286fc11e52e9f54e93f8af5bc20553cce9f8cad9ac8160aed1cd"
    EXPECTED_PEPPER_ID = "d88b94b2"
    EXPECTED_FINGERPRINT = "h1:d88b94b2:qtjz8ooN0yOuoUwkCFpMiad1lTE69qxndRG0P0FQ8M4"

    def test_one_entry_ring_reproduces_the_pre_removal_pepper(self):
        from core.services.events import idempotency

        with override_settings(ENCRYPTION_KEYS=KEY):
            pepper = idempotency._fingerprint_pepper()

        assert pepper.hex() == self.EXPECTED_PEPPER_HEX
        assert idempotency._pepper_id(pepper) == self.EXPECTED_PEPPER_ID
        assert idempotency._fingerprint_for_pepper(pepper, "canonical-payload") == self.EXPECTED_FINGERPRINT

    def test_pepper_follows_the_first_ring_entry_only(self):
        from core.services.events import idempotency

        with override_settings(ENCRYPTION_KEYS=f"{KEY},{OTHER_KEY}"):
            pepper = idempotency._fingerprint_pepper()

        assert pepper.hex() == self.EXPECTED_PEPPER_HEX

    def test_missing_ring_names_only_the_ring(self):
        from core.services.events import idempotency
        from core.services.file_vault.encryption import EncryptionKeyMissing

        with override_settings(ENCRYPTION_KEYS=""):
            with pytest.raises(EncryptionKeyMissing) as excinfo:
                idempotency._fingerprint_pepper()

        assert "ENCRYPTION_KEYS" in str(excinfo.value)
        assert "ENCRYPTION_KEY /" not in str(excinfo.value)


class TestNoDeprecationCheckAnyMore:
    def test_single_key_deprecation_warning_is_gone(self):
        with override_settings(DEBUG=False, ENCRYPTION_KEY=KEY, ENCRYPTION_KEYS=""):
            ids = {message.id for message in run_checks()}

        assert "core.W005" not in ids
