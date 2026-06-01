"""Fail-closed guarantees for production settings (Refs #558).

These tests execute the production settings module in an isolated Python
subprocess with controlled env vars. A successful exit-code means the module
imported cleanly; a non-zero exit-code means it raised ImproperlyConfigured,
which is the expected fail-closed behaviour when a required variable is
missing.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"


def _run_prod_import(env_overrides, args=("check", "--deploy")):
    """Import prod settings in a subprocess with a clean environment.

    All sensitive vars are pre-initialised to an empty string so that the
    project's ``load_dotenv()`` call cannot smuggle values from the local
    ``.env`` file into the test (dotenv does not override vars that are
    already set, even if their value is the empty string).
    """
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(SRC_DIR),
        "DJANGO_SETTINGS_MODULE": "anlaufstelle.settings.prod",
        "DATABASE_URL": "postgres://u:p@localhost:5432/x",
        "POSTGRES_DB": "x",
        "POSTGRES_USER": "u",
        "POSTGRES_PASSWORD": "p",
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "5432",
        # Pre-empt dotenv pass-through of local secrets.
        "DJANGO_SECRET_KEY": "",
        "ALLOWED_HOSTS": "",
        "ENCRYPTION_KEY": "",
        "ENCRYPTION_KEYS": "",
    }
    env.update(env_overrides)
    # Just importing the settings module is enough to trigger the guards.
    result = subprocess.run(
        [sys.executable, "-c", "import anlaufstelle.settings.prod  # noqa"],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    return result


class TestProdFailClosed:
    """Prod settings must refuse to load with missing or unsafe configuration."""

    def test_missing_secret_key_raises(self):
        result = _run_prod_import(
            {
                "ALLOWED_HOSTS": "example.org",
                "ENCRYPTION_KEY": "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZmE=",
            }
        )
        assert result.returncode != 0
        assert "DJANGO_SECRET_KEY" in result.stderr

    def test_missing_allowed_hosts_raises(self):
        result = _run_prod_import(
            {
                "DJANGO_SECRET_KEY": "x" * 50,
                "ENCRYPTION_KEY": "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZmE=",
            }
        )
        assert result.returncode != 0
        assert "ALLOWED_HOSTS" in result.stderr

    def test_missing_encryption_keys_raises(self):
        result = _run_prod_import(
            {
                "DJANGO_SECRET_KEY": "x" * 50,
                "ALLOWED_HOSTS": "example.org",
            }
        )
        assert result.returncode != 0
        assert "ENCRYPTION_KEY" in result.stderr

    def test_encryption_keys_plural_accepted(self):
        """ENCRYPTION_KEYS alone (without singular ENCRYPTION_KEY) must load."""
        result = _run_prod_import(
            {
                "DJANGO_SECRET_KEY": "x" * 50,
                "ALLOWED_HOSTS": "example.org",
                "ENCRYPTION_KEYS": "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZmE=",
            }
        )
        assert result.returncode == 0, f"Import failed unexpectedly: {result.stderr}"
