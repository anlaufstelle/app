"""Fail-closed + behaviour guarantees for ``settings/devlive.py`` (Refs #671).

Same Subprocess-Strategie wie ``test_prod_settings.py``: das Settings-Modul
wird in einer isolierten Python-Subprozess-Umgebung importiert, damit die
Module-Level Guards greifen (``ImproperlyConfigured`` bei fehlenden ENVs).
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"

# Minimaler Satz Pflicht-ENVs, der fuer einen erfolgreichen Import von
# devlive.py reicht (uebernimmt die Pflichten von prod.py).
_VALID_ENV = {
    "DJANGO_SECRET_KEY": "x" * 50,
    "ALLOWED_HOSTS": "dev.anlaufstelle.app",
    "ENCRYPTION_KEY": "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZmE=",
    "POSTGRES_DB": "x",
    "POSTGRES_USER": "u",
    "POSTGRES_PASSWORD": "p",
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5432",
}


def _run_devlive_import(env_overrides=None, expr="import anlaufstelle.settings.devlive  # noqa"):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(SRC_DIR),
        "DJANGO_SETTINGS_MODULE": "anlaufstelle.settings.devlive",
        # dotenv-Smuggling unterbinden (siehe test_prod_settings.py).
        "ENCRYPTION_KEYS": "",
    }
    env.update(_VALID_ENV)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", expr],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


class TestDevLiveLoad:
    def test_imports_with_minimal_valid_env(self):
        result = _run_devlive_import()
        assert result.returncode == 0, f"Import failed: {result.stderr}"

    def test_email_backend_is_console(self):
        result = _run_devlive_import(
            expr=("from anlaufstelle.settings.devlive import EMAIL_BACKEND;print(EMAIL_BACKEND)")
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "django.core.mail.backends.console.EmailBackend"

    def test_debug_is_false(self):
        result = _run_devlive_import(expr="from anlaufstelle.settings.devlive import DEBUG;print(DEBUG)")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "False"

    def test_hsts_inherited_from_prod(self):
        result = _run_devlive_import(
            expr=(
                "from anlaufstelle.settings.devlive import "
                "SECURE_HSTS_SECONDS, SECURE_HSTS_PRELOAD, SESSION_COOKIE_SECURE;"
                "print(SECURE_HSTS_SECONDS, SECURE_HSTS_PRELOAD, SESSION_COOKIE_SECURE)"
            )
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "31536000 True True"


class TestDevLiveFailClosed:
    """Pflicht-ENV-Guards aus prod.py muessen auch in devlive greifen."""

    def test_missing_secret_key_raises(self):
        result = _run_devlive_import({"DJANGO_SECRET_KEY": ""})
        assert result.returncode != 0
        assert "DJANGO_SECRET_KEY" in result.stderr

    def test_missing_allowed_hosts_raises(self):
        result = _run_devlive_import({"ALLOWED_HOSTS": ""})
        assert result.returncode != 0
        assert "ALLOWED_HOSTS" in result.stderr

    def test_missing_encryption_key_raises(self):
        result = _run_devlive_import({"ENCRYPTION_KEY": ""})
        assert result.returncode != 0
        assert "ENCRYPTION_KEY" in result.stderr

    def test_sudo_mode_disabled_raises(self):
        result = _run_devlive_import({"SUDO_MODE_ENABLED": "false"})
        assert result.returncode != 0
        assert "SUDO_MODE_ENABLED" in result.stderr
