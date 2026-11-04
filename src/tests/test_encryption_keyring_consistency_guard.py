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


#: Comma splits that are not key-ring parsing, per module and enclosing function.
_NON_RING_COMMA_SPLITS = {
    "anlaufstelle/settings/_env.py": {"env_csv"},  # generic CSV settings (hosts, origins)
    "anlaufstelle/settings/_mfa_roles.py": {"_parse_role_list"},  # MFA role names
    "core/services/file_vault/policy.py": {"enforce_allowed_file_types"},  # file extensions
}
_SHARED_HELPER = "anlaufstelle/settings/_keyring.py"


class _CommaSplitSites(ast.NodeVisitor):
    def __init__(self) -> None:
        self.functions: list[str] = []
        self.sites: set[str] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "split"
            and [arg.value for arg in node.args if isinstance(arg, ast.Constant)] == [","]
        ):
            self.sites.add(self.functions[-1] if self.functions else "<module>")
        self.generic_visit(node)


def _comma_split_sites(path: Path) -> set[str]:
    visitor = _CommaSplitSites()
    visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
    return visitor.sites


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


@pytest.mark.parametrize("module", ["prod.py", "dev.py", "_encryption.py"])
def test_settings_modules_import_the_shared_helper(module):
    assert "split_key_ring" in _imported_names(SRC_DIR / "anlaufstelle" / "settings" / module, "_keyring")


def test_only_the_shared_helper_splits_the_key_ring():
    modules = sorted(
        [
            *(SRC_DIR / "anlaufstelle" / "settings").glob("*.py"),
            *(SRC_DIR / "core" / "services" / "file_vault").glob("*.py"),
        ]
    )
    sites = {path.relative_to(SRC_DIR).as_posix(): _comma_split_sites(path) for path in modules}

    assert sites.pop(_SHARED_HELPER) == {"split_key_ring"}
    unexpected = {module: found - _NON_RING_COMMA_SPLITS.get(module, set()) for module, found in sites.items()}
    assert {module: found for module, found in unexpected.items() if found} == {}


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
