"""Produktions-Settings fuer explizites TLS und implizites TLS/SMTPS."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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


_DUMP_MAILER = (
    "import json; from anlaufstelle.settings import prod; "
    "print(json.dumps({'backend': prod.MAILERS['default']['BACKEND'], "
    "**prod.MAILERS['default']['OPTIONS']}))"
)


def test_prod_reads_email_use_ssl() -> None:
    result = _run_prod_import(
        {**VALID_ENV, "EMAIL_USE_TLS": "false", "EMAIL_USE_SSL": "true"},
        code=_DUMP_MAILER,
    )
    assert result.returncode == 0, result.stderr
    mailer = json.loads(result.stdout)
    assert (mailer["use_tls"], mailer["use_ssl"]) == (False, True)


@pytest.mark.parametrize("name, default", [("EMAIL_USE_TLS", True), ("EMAIL_USE_SSL", False)])
@pytest.mark.parametrize("value", ["", "  "])
def test_prod_treats_blank_email_transport_values_as_unset(name: str, default: bool, value: str) -> None:
    result = _run_prod_import(
        {**VALID_ENV, name: value},
        code=_DUMP_MAILER,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["use_tls" if name == "EMAIL_USE_TLS" else "use_ssl"] is default


@pytest.mark.parametrize("name", ["EMAIL_USE_TLS", "EMAIL_USE_SSL"])
@pytest.mark.parametrize("value", ["ture", "on"])
def test_prod_rejects_unknown_email_transport_values(name: str, value: str) -> None:
    result = _run_prod_import({**VALID_ENV, name: value})

    assert result.returncode != 0
    assert name in result.stderr
    assert "true/1/yes" in result.stderr
    assert "false/0/no" in result.stderr


@pytest.mark.parametrize("value, expected", [("true", True), ("False", False), ("1", True), ("0", False)])
def test_prod_accepts_strict_email_transport_values(value: str, expected: bool) -> None:
    result = _run_prod_import(
        {**VALID_ENV, "EMAIL_USE_TLS": value, "EMAIL_USE_SSL": "false"},
        code=_DUMP_MAILER,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["use_tls"] is expected


def test_prod_rejects_tls_and_ssl_together() -> None:
    result = _run_prod_import({**VALID_ENV, "EMAIL_USE_TLS": "true", "EMAIL_USE_SSL": "true"})
    assert result.returncode != 0
    assert "EMAIL_USE_TLS" in result.stderr
    assert "EMAIL_USE_SSL" in result.stderr


def test_prod_mailer_keeps_the_established_env_variable_names() -> None:
    """Refs #1662: der Django-6.1-Umstieg (EMAIL_*-Settings -> MAILERS) laesst die
    Betreiber-ENV unveraendert — eine bestehende ``.env`` bleibt gueltig."""
    result = _run_prod_import(
        {
            **VALID_ENV,
            "EMAIL_HOST": "mail.example.org",
            "EMAIL_PORT": "2525",
            "EMAIL_HOST_USER": "postbote",
            "EMAIL_HOST_PASSWORD": "geheim",
            "EMAIL_USE_TLS": "true",
            "EMAIL_USE_SSL": "false",
        },
        code=_DUMP_MAILER,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "backend": "django.core.mail.backends.smtp.EmailBackend",
        "host": "mail.example.org",
        "port": 2525,
        "username": "postbote",
        "password": "geheim",
        "use_tls": True,
        "use_ssl": False,
    }


def test_prod_mailer_defaults_match_the_pre_migration_behaviour() -> None:
    """Ohne gesetzte EMAIL_*-Variablen gelten dieselben Defaults wie vor dem
    Umstieg: localhost:587, leere Credentials, STARTTLS an, SMTPS aus."""
    result = _run_prod_import(VALID_ENV, code=_DUMP_MAILER)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "backend": "django.core.mail.backends.smtp.EmailBackend",
        "host": "localhost",
        "port": 587,
        "username": "",
        "password": "",
        "use_tls": True,
        "use_ssl": False,
    }


def test_prod_defines_no_deprecated_email_settings() -> None:
    """``EMAIL_*``-Settings und ``MAILERS`` schliessen sich in Django 6.1 aus
    (ImproperlyConfigured beim Settings-Import)."""
    result = _run_prod_import(
        VALID_ENV,
        code=(
            "from django.conf import DEPRECATED_EMAIL_SETTINGS; "
            "from anlaufstelle.settings import prod; "
            "print(sorted(n for n in DEPRECATED_EMAIL_SETTINGS if hasattr(prod, n)))"
        ),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
