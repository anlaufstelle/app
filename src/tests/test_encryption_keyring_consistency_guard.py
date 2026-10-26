"""Keep encryption-key ring parsing consistent across startup and runtime."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.test import override_settings

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
KEY = "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="


def _function_body(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    body = function.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return ast.dump(ast.Module(body=body, type_ignores=[]), include_attributes=False)


def _prod_import(*, key_ring: str = " , ") -> subprocess.CompletedProcess[str]:
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


def test_prod_startgate_ring_splitter_matches_shared_helper():
    prod_body = _function_body(
        SRC_DIR / "anlaufstelle" / "settings" / "prod.py",
        "_split_encryption_key_ring",
    )
    shared_body = _function_body(
        SRC_DIR / "core" / "services" / "file_vault" / "keyring.py",
        "split_key_ring",
    )

    assert prod_body == shared_body


def test_runtime_treats_separator_only_ring_as_unset():
    from core.services.file_vault.encryption import _get_keys

    with override_settings(ENCRYPTION_KEYS=" , "):
        assert _get_keys() == []


def test_runtime_ignores_a_leftover_single_key_setting():
    from core.services.file_vault.encryption import _get_keys

    with override_settings(ENCRYPTION_KEYS=" , ", ENCRYPTION_KEY=KEY):
        assert _get_keys() == []


def test_prod_startgate_rejects_separator_only_ring():
    result = _prod_import()

    assert result.returncode != 0
    assert "The key ring contains no key" in result.stderr


def test_prod_startgate_keeps_missing_key_message_when_nothing_is_set():
    result = _prod_import(key_ring="")

    assert result.returncode != 0
    assert "ENCRYPTION_KEYS must be set in production." in result.stderr


def test_prod_startgate_accepts_a_ring_with_separators_around_the_key():
    result = _prod_import(key_ring=f" , {KEY} ,")

    assert result.returncode == 0, result.stderr
