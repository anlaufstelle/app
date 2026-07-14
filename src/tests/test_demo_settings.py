"""Fail-closed + Inhalts-Garantien fuer ``settings/demo.py`` (Refs #1375, L11).

Gleiche Subprozess-Strategie wie ``test_devlive_settings.py``: das Settings-
Modul wird in einer isolierten Python-Subprozess-Umgebung importiert, damit die
Module-Level Guards aus ``prod.py`` greifen und wir die tatsaechlich publizierten
Demo-Werte pruefen (nicht per ``override_settings`` gefakte).
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"

# Minimaler Satz Pflicht-ENVs, mit dem demo.py (erbt devlive -> prod) importiert.
_VALID_ENV = {
    "DJANGO_SECRET_KEY": "x" * 50,
    "ALLOWED_HOSTS": "demo.anlaufstelle.app",
    "ENCRYPTION_KEY": "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZmE=",
    "DJANGO_AUDIT_HASH_KEY": "y" * 50,
    "POSTGRES_DB": "x",
    "POSTGRES_USER": "u",
    "POSTGRES_PASSWORD": "p",
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5432",
}


def _run_demo_import(expr):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(SRC_DIR),
        "DJANGO_SETTINGS_MODULE": "anlaufstelle.settings.demo",
        "ENCRYPTION_KEYS": "",
    }
    env.update(_VALID_ENV)
    return subprocess.run(
        [sys.executable, "-c", expr],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


class TestDemoLogins:
    def test_superadmin_not_publicly_advertised(self):
        """L11 (Refs #1375): Das superadmin-Konto darf NICHT im oeffentlichen
        Login-Zugangsdaten-Panel (``DEMO_LOGINS``) mit publiziertem Passwort
        beworben werden. Das Konto darf existieren, nur nicht oeffentlich."""
        result = _run_demo_import(
            "from anlaufstelle.settings.demo import DEMO_LOGINS;"
            "print([entry['username'] for entry in DEMO_LOGINS])"
        )
        assert result.returncode == 0, result.stderr
        usernames = result.stdout.strip()
        assert "superadmin" not in usernames, f"superadmin darf nicht in DEMO_LOGINS stehen: {usernames}"

    def test_non_privileged_demo_logins_remain(self):
        """Die uebrigen Demo-Logins bleiben unveraendert erhalten."""
        result = _run_demo_import(
            "from anlaufstelle.settings.demo import DEMO_LOGINS;"
            "print([entry['username'] for entry in DEMO_LOGINS])"
        )
        assert result.returncode == 0, result.stderr
        for username in ("admin", "emma", "miriam", "markus", "lena", "felix"):
            assert username in result.stdout, f"{username} fehlt in DEMO_LOGINS: {result.stdout}"
