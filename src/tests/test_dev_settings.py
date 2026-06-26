"""Klartext-Warnung/Guard für ``settings/dev.py`` (#1276, T15).

``dev.py`` darf lokal weiterhin OHNE ``ENCRYPTION_KEY`` starten (Felder dann im
Klartext) — aber das Risiko muss unübersehbar sein, damit eine Staging-Box, die
versehentlich auf dev-Settings läuft, keine Art-9-Daten still im Klartext
ablegt. Geprüft wird:

1. Default (kein Key, kein Flag): Import lädt, aber eine **laute** Warnung geht
   nach stderr.
2. Opt-in-Guard ``REQUIRE_ENCRYPTION``: ohne Key verweigert ``dev.py`` den Start.
3. Mit gesetztem ``ENCRYPTION_KEY``: kein Klartext-Banner.

Subprozess-Strategie wie ``test_prod_settings.py`` (Module-Level-Guards greifen
nur beim Import in einer kontrollierten Env).
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"

_FERNET_KEY = "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZmE="

_BANNER_MARKER = "KLARTEXT"


def _run_dev_import(env_overrides=None, expr="import anlaufstelle.settings.dev  # noqa"):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(SRC_DIR),
        "DJANGO_SETTINGS_MODULE": "anlaufstelle.settings.dev",
        "POSTGRES_DB": "x",
        "POSTGRES_USER": "u",
        "POSTGRES_PASSWORD": "p",
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "5432",
        # dotenv-Smuggling der lokalen Secrets unterbinden (siehe
        # test_prod_settings.py): vorbelegte Leerstrings werden von
        # load_dotenv(override=False) nicht überschrieben.
        "ENCRYPTION_KEY": "",
        "ENCRYPTION_KEYS": "",
        "REQUIRE_ENCRYPTION": "",
    }
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", expr],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


class TestDevPlaintextGuard:
    def test_missing_key_loads_but_warns_loudly(self):
        result = _run_dev_import()
        assert result.returncode == 0, f"dev.py darf ohne Key starten: {result.stderr}"
        # Laut = unübersehbar auf stderr (nicht nur eine versteckte Log-Zeile).
        assert _BANNER_MARKER in result.stderr
        assert "ENCRYPTION_KEY" in result.stderr

    def test_missing_key_with_require_encryption_raises(self):
        result = _run_dev_import({"REQUIRE_ENCRYPTION": "1"})
        assert result.returncode != 0
        assert "REQUIRE_ENCRYPTION" in result.stderr or "ENCRYPTION_KEY" in result.stderr

    def test_key_present_no_banner(self):
        result = _run_dev_import({"ENCRYPTION_KEY": _FERNET_KEY})
        assert result.returncode == 0, result.stderr
        assert _BANNER_MARKER not in result.stderr

    def test_key_present_with_require_encryption_loads(self):
        result = _run_dev_import({"ENCRYPTION_KEY": _FERNET_KEY, "REQUIRE_ENCRYPTION": "1"})
        assert result.returncode == 0, result.stderr
