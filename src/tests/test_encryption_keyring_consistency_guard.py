"""Keep encryption-key ring parsing in one place for startup and runtime."""

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


def _imported_names(path: Path, module: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module == module
        for alias in node.names
    }


def _local_comma_splitters(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        function.name
        for function in ast.walk(tree)
        if isinstance(function, ast.FunctionDef)
        for call in ast.walk(function)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "split"
        and [arg.value for arg in call.args if isinstance(arg, ast.Constant)] == [","]
    ]


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


def test_runtime_uses_the_settings_time_key_ring_helper():
    from anlaufstelle.settings._keyring import split_key_ring as settings_time_helper
    from core.services.file_vault.keyring import split_key_ring as runtime_helper

    assert runtime_helper is settings_time_helper


def test_prod_startgate_imports_the_shared_helper_instead_of_a_copy():
    prod_settings = SRC_DIR / "anlaufstelle" / "settings" / "prod.py"

    assert "split_key_ring" in _imported_names(prod_settings, "_keyring")
    assert _local_comma_splitters(prod_settings) == []


def test_shared_helper_loads_before_django_setup_without_app_modules():
    probe = (
        "import sys\n"
        "from anlaufstelle.settings._keyring import split_key_ring\n"
        "assert split_key_ring(' , a ,, b ') == ['a', 'b']\n"
        "loaded = sorted(m for m in sys.modules if m.split('.')[0] in {'core', 'django'})\n"
        "print(loaded)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(SRC_DIR)},
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


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
