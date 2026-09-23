"""Keep encryption-key ring parsing consistent across startup and runtime."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.test import override_settings

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
KEY = "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZmE="


def _prod_import(*, single_key: str, key_ring: str = " , ") -> subprocess.CompletedProcess[str]:
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
        "ENCRYPTION_KEY": single_key,
        "ENCRYPTION_KEYS": key_ring,
        "DJANGO_AUDIT_HASH_KEY": "y" * 50,
        "POSTGRES_ADMIN_USER": "",
        "POSTGRES_ADMIN_PASSWORD": "",
        "POSTGRES_BOOTSTRAP_PASSWORD": "",
        "REDIS_URL": "",
        "FILE_UPLOAD_TEMP_DIR": "",
        "EXTERNAL_ORIGIN": "",
    }
    return subprocess.run(
        [sys.executable, "-c", "import anlaufstelle.settings.prod"],
        env=env,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=20,
    )


def test_separator_only_ring_is_empty():
    from core.services.file_vault.keyring import split_key_ring

    assert split_key_ring(" , ") == []


@pytest.mark.parametrize(
    ("single_key", "expected"),
    [(KEY, [KEY]), ("", [])],
    ids=["single-key-fallback", "no-key"],
)
def test_runtime_treats_separator_only_ring_as_unset(single_key, expected):
    from core.services.file_vault.encryption import _get_keys

    with override_settings(ENCRYPTION_KEYS=" , ", ENCRYPTION_KEY=single_key):
        assert _get_keys() == expected


@pytest.mark.parametrize(
    ("single_key", "expected_ids"),
    [(KEY, {"core.W005"}), ("", set())],
    ids=["single-key-warning", "no-key-no-deprecation-warning"],
)
def test_system_check_treats_separator_only_ring_as_unset(single_key, expected_ids):
    from core.checks import check_encryption_single_key_deprecated

    with override_settings(DEBUG=False, ENCRYPTION_KEYS=" , ", ENCRYPTION_KEY=single_key):
        messages = check_encryption_single_key_deprecated(None)

    assert {message.id for message in messages} == expected_ids


def test_system_check_uses_shared_ring_splitter():
    from core import checks
    from core.services.file_vault.keyring import split_key_ring

    assert checks.split_key_ring is split_key_ring


def test_prod_startgate_rejects_separator_only_ring_without_single_key():
    result = _prod_import(single_key="")

    assert result.returncode != 0
    assert "Schlüsselring enthält keinen Schlüssel" in result.stderr


def test_prod_startgate_keeps_missing_key_message_when_nothing_is_set():
    result = _prod_import(single_key="", key_ring="")

    assert result.returncode != 0
    assert "ENCRYPTION_KEY oder ENCRYPTION_KEYS muss in Produktion gesetzt sein." in result.stderr


def test_prod_startgate_accepts_separator_only_ring_with_single_key():
    result = _prod_import(single_key=KEY)

    assert result.returncode == 0, result.stderr
