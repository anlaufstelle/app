"""Guard: ``DEMO_MODE`` ist ausschliesslich in ``settings/demo.py`` wahr (Refs #2285).

Der passwortlose Demo-Einstieg (``core/views/demo_scenarios.py``) ist NUR
sicher, solange ``DEMO_MODE`` in Produktions-/Live-Dev-/E2E-Einstellungen
falsy bleibt — dafuer gab es bisher keinen Guard-Test, nur den Befund
„genau eine Zuweisung, keine Env-Ableitung" bei der Code-Durchsicht.

Gleiche Subprozess-Strategie wie ``test_devlive_settings.py``/
``test_demo_settings.py``: jedes Settings-Modul wird isoliert importiert,
damit die modul-eigenen Fail-Closed-Guards (``ImproperlyConfigured``)
greifen und der tatsaechlich importierte Wert geprueft wird — nicht ein
per ``override_settings`` vorgetaeuschter.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"

# Minimaler Satz Pflicht-ENVs fuer prod.py bzw. seine Ableitung devlive.py
# (dieselben Werte wie test_devlive_settings.py/test_demo_settings.py — jede
# Settings-Testdatei pflegt ihre eigene Kopie, etabliertes Muster in diesem
# Repo statt einer geteilten Fixture-Datei).
_PROD_ENV = {
    "DJANGO_SECRET_KEY": "x" * 50,
    "ALLOWED_HOSTS": "example.org",
    "ENCRYPTION_KEY": "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZmE=",
    "DJANGO_AUDIT_HASH_KEY": "y" * 50,
    "POSTGRES_DB": "x",
    "POSTGRES_USER": "u",
    "POSTGRES_PASSWORD": "p" * 40,
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5432",
}


def _run_import(settings_module, env_overrides=None, expr=None):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(SRC_DIR),
        "DJANGO_SETTINGS_MODULE": settings_module,
        "ENCRYPTION_KEYS": "",
        "DJANGO_LOAD_DOTENV": "false",
    }
    if env_overrides:
        env.update(env_overrides)
    script = expr or (f"import {settings_module} as m;print(getattr(m, 'DEMO_MODE', False))")
    return subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


class TestDemoModeNotLeakedOutsideDemoSettings:
    def test_prod_settings_demo_mode_is_falsy(self):
        result = _run_import("anlaufstelle.settings.prod", _PROD_ENV)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "False"

    def test_devlive_settings_demo_mode_is_falsy(self):
        env = {**_PROD_ENV, "ALLOWED_HOSTS": "dev.anlaufstelle.app"}
        result = _run_import("anlaufstelle.settings.devlive", env)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "False"

    def test_e2e_settings_demo_mode_is_falsy(self):
        # e2e.py erbt von dev.py, das fuer SECRET_KEY/POSTGRES_PASSWORD eigene
        # Dev-Fallbacks setzt — kein Pflicht-ENV noetig (anders als prod/devlive).
        result = _run_import("anlaufstelle.settings.e2e")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "False"

    def test_dev_settings_demo_mode_is_falsy(self):
        result = _run_import("anlaufstelle.settings.dev")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "False"

    def test_test_settings_demo_mode_is_falsy(self):
        result = _run_import("anlaufstelle.settings.test")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "False"

    def test_demo_settings_is_the_only_true_value(self):
        """Gegenprobe: der Guard misst tatsaechlich etwas — ``demo.py`` selbst
        liefert ``True`` unter denselben Pflicht-ENVs wie devlive.py (es erbt
        davon)."""
        env = {**_PROD_ENV, "ALLOWED_HOSTS": "demo.anlaufstelle.app,localhost,127.0.0.1"}
        result = _run_import("anlaufstelle.settings.demo", env)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "True"
