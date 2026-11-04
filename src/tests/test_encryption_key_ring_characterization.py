"""Characterize how ``ENCRYPTION_KEYS`` is parsed at startup and at runtime.

Both the production start gate and the runtime field encryption read the same
comma-separated key ring. These tests pin the observable behaviour for every
input form (empty, separators, whitespace, empty entries, duplicates, invalid
keys, order) so that both paths keep starting, refusing and encrypting the
same way.
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from django.test import override_settings

from core.services.file_vault.encryption import EncryptionError, decrypt_field, encrypt_field
from core.services.file_vault.keyring import split_key_ring

SRC_DIR = Path(__file__).resolve().parents[1]
KEY_A = base64.urlsafe_b64encode(b"a" * 32).decode()
KEY_B = base64.urlsafe_b64encode(b"b" * 32).decode()

GENERATE_HINT = (
    'Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
)
MISSING_RING = (
    "django.core.exceptions.ImproperlyConfigured: ENCRYPTION_KEYS must be set in production. " + GENERATE_HINT
)
EMPTY_RING = (
    "django.core.exceptions.ImproperlyConfigured: The key ring contains no key (ENCRYPTION_KEYS). " + GENERATE_HINT
)


def _invalid_entry(position: int) -> str:
    return f"django.core.exceptions.ImproperlyConfigured: ENCRYPTION_KEYS entry {position} is not a valid Fernet key."


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, []),
        ("", []),
        ("   ", []),
        (",", []),
        (" , ,, ", []),
        ("\t\n", []),
        (KEY_A, [KEY_A]),
        (f"  {KEY_A}\n", [KEY_A]),
        (f" , {KEY_A} ,", [KEY_A]),
        (f"{KEY_A},,{KEY_B}", [KEY_A, KEY_B]),
        (f"{KEY_B}, {KEY_A}", [KEY_B, KEY_A]),
        (f"{KEY_A},{KEY_A}", [KEY_A, KEY_A]),
        ("not-a-key", ["not-a-key"]),
        (f"{KEY_A}, not-a-key ", [KEY_A, "not-a-key"]),
    ],
)
def test_split_key_ring_strips_drops_empty_entries_and_keeps_order(value, expected):
    assert split_key_ring(value) == expected


class TestRuntimeKeyRing:
    def test_first_stripped_entry_encrypts(self):
        with override_settings(ENCRYPTION_KEYS=f" , {KEY_B} ,, {KEY_A} ,"):
            token = encrypt_field("secret")["value"]

        assert Fernet(KEY_B).decrypt(token.encode()) == b"secret"

    def test_later_entries_decrypt(self):
        token = Fernet(KEY_A).encrypt(b"secret").decode()

        with override_settings(ENCRYPTION_KEYS=f"{KEY_B},{KEY_A}"):
            assert decrypt_field({"__encrypted__": True, "value": token}) == "secret"

    def test_duplicate_entries_still_encrypt_and_decrypt(self):
        with override_settings(ENCRYPTION_KEYS=f"{KEY_A},{KEY_A}"):
            assert decrypt_field(encrypt_field("secret")) == "secret"

    @pytest.mark.parametrize("value", ["", "   ", " , ,, "])
    def test_ring_without_entries_cannot_encrypt(self, value):
        with override_settings(ENCRYPTION_KEYS=value), pytest.raises(EncryptionError, match="no encryption key"):
            encrypt_field("secret")

    def test_invalid_entry_cannot_encrypt(self):
        with override_settings(ENCRYPTION_KEYS="not-a-key"), pytest.raises(EncryptionError, match="Encryption failed"):
            encrypt_field("secret")


def _prod_start(key_ring: str, *argv: str) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(SRC_DIR),
        "DJANGO_SETTINGS_MODULE": "anlaufstelle.settings.prod",
        "DATABASE_URL": "postgres://u:p@localhost:5432/x",
        "POSTGRES_DB": "x",
        "POSTGRES_USER": "u",
        "POSTGRES_PASSWORD": "p" * 40,
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "5432",
        "DJANGO_SECRET_KEY": "x" * 50,
        "ALLOWED_HOSTS": "example.org",
        "ENCRYPTION_KEY": "",
        "ENCRYPTION_KEYS": key_ring,
        "DJANGO_LOAD_DOTENV": "false",
        "DJANGO_AUDIT_HASH_KEY": "y" * 50,
        "POSTGRES_ADMIN_USER": "",
        "POSTGRES_ADMIN_PASSWORD": "",
        "POSTGRES_BOOTSTRAP_PASSWORD": "",
        "REDIS_URL": "",
        "FILE_UPLOAD_TEMP_DIR": "",
        "EXTERNAL_ORIGIN": "",
    }
    return subprocess.run(
        [sys.executable, "-c", "import anlaufstelle.settings.prod", *argv],
        env=env,
        capture_output=True,
        text=True,
        cwd=SRC_DIR,
        timeout=30,
    )


@pytest.mark.parametrize(
    ("key_ring", "last_error_line"),
    [
        ("", MISSING_RING),
        ("   ", EMPTY_RING),
        (",", EMPTY_RING),
        (" , ,, ", EMPTY_RING),
        ("not-a-key", _invalid_entry(1)),
        (f"{KEY_A},,not-a-key", _invalid_entry(2)),
        (f" not-a-key , {KEY_A}", _invalid_entry(1)),
    ],
    ids=["empty", "whitespace", "separator", "separators", "invalid", "invalid-second", "invalid-first"],
)
def test_prod_start_refuses(key_ring, last_error_line):
    result = _prod_start(key_ring)

    assert result.returncode == 1
    assert result.stderr.strip().splitlines()[-1] == last_error_line
    assert KEY_A not in result.stderr


@pytest.mark.parametrize(
    "key_ring",
    [KEY_A, f"  {KEY_A}\n", f" , {KEY_A} ,", f"{KEY_A},,{KEY_B}", f"{KEY_B}, {KEY_A}"],
    ids=["single", "padded", "separators-around", "empty-entry", "two-keys"],
)
def test_prod_start_accepts(key_ring):
    result = _prod_start(key_ring)

    assert result.returncode == 0, result.stderr
    assert "ENCRYPTION_KEYS" not in result.stderr


def test_prod_start_accepts_duplicates_with_a_warning_without_key_value():
    result = _prod_start(f"{KEY_A},{KEY_A}")

    assert result.returncode == 0, result.stderr
    assert "ENCRYPTION_KEYS entry 2 duplicates an earlier entry." in result.stderr
    assert KEY_A not in result.stderr


@pytest.mark.parametrize("key_ring", ["", " , "], ids=["empty", "separator"])
def test_prod_collectstatic_build_starts_without_key(key_ring):
    result = _prod_start(key_ring, "collectstatic")

    assert result.returncode == 0, result.stderr
