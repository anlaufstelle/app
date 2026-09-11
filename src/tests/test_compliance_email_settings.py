"""Produktions-Settings fuer explizites TLS und implizites TLS/SMTPS."""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"


VALID_ENV = {
    "DJANGO_SECRET_KEY": "x" * 50,
    "ALLOWED_HOSTS": "example.org",
    "ENCRYPTION_KEY": "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZmE=",
    "DJANGO_AUDIT_HASH_KEY": "y" * 50,
}


def _run_prod_import(env_overrides: dict[str, str], code: str = "import anlaufstelle.settings.prod"):
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
        "POSTGRES_ADMIN_USER": "",
        "POSTGRES_ADMIN_PASSWORD": "",
        "POSTGRES_BOOTSTRAP_PASSWORD": "",
        "REDIS_URL": "",
        "FILE_UPLOAD_TEMP_DIR": "",
        "EXTERNAL_ORIGIN": "",
    }
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=30,
    )


def test_prod_reads_email_use_ssl() -> None:
    result = _run_prod_import(
        {**VALID_ENV, "EMAIL_USE_TLS": "false", "EMAIL_USE_SSL": "true"},
        code=(
            "import json; from anlaufstelle.settings import prod; "
            "print(json.dumps({'tls': prod.EMAIL_USE_TLS, 'ssl': prod.EMAIL_USE_SSL}))"
        ),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"tls": False, "ssl": True}


def test_prod_rejects_tls_and_ssl_together() -> None:
    result = _run_prod_import({**VALID_ENV, "EMAIL_USE_TLS": "true", "EMAIL_USE_SSL": "true"})
    assert result.returncode != 0
    assert "EMAIL_USE_TLS" in result.stderr
    assert "EMAIL_USE_SSL" in result.stderr
