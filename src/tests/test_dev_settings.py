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

import pytest

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


@pytest.mark.architecture
class TestEnvExampleEntfuehrtDieLokalenLinks:
    """Refs #1967: ``.env.example`` darf ``EXTERNAL_ORIGIN`` nicht aktiv setzen.

    ``make setup`` kopiert die Datei 1:1 nach ``.env``, ``base.py`` laedt sie per
    ``load_dotenv`` in ``os.environ``. Ein dort gesetzter Produktionswert kann von
    dev/e2e nicht mehr korrigiert werden: beide sind bewusst so geschrieben, dass
    die Umgebung gewinnt (``dev.py``: ``EXTERNAL_ORIGIN or "http://localhost:8000"``;
    ``e2e.py``: ``if not os.environ.get(...)``). Der Setup-Link aus der
    Konsolen-Mail zeigte dann auf die oeffentliche Produktionsseite, und der
    lokale Einladungs-Flow liesse sich nicht abschliessen.
    """

    _ENV_EXAMPLE = REPO_ROOT / ".env.example"

    def _aktive_zuweisungen(self):
        werte = {}
        for zeile in self._ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
            zeile = zeile.strip()
            if not zeile or zeile.startswith("#") or "=" not in zeile:
                continue
            key, _, value = zeile.partition("=")
            werte[key.strip()] = value.strip()
        return werte

    def test_external_origin_ist_nicht_aktiv_gesetzt(self):
        assert "EXTERNAL_ORIGIN" not in self._aktive_zuweisungen(), (
            ".env.example darf EXTERNAL_ORIGIN nur auskommentiert dokumentieren — `make setup` "
            "kopiert die Datei nach .env, und der Wert schluege dort die lokalen Vorbelegungen "
            "von dev.py/e2e.py."
        )

    def _origin_mit_env_example(self, settings_modul, port_erwartung):
        werte = self._aktive_zuweisungen()
        # DJANGO_SETTINGS_MODULE steht in der Vorlage auf prod — hier zaehlt das
        # lokal genutzte Modul, alles Uebrige kommt unveraendert aus der Datei.
        werte.pop("DJANGO_SETTINGS_MODULE", None)
        result = _run_dev_import(
            {
                **werte,
                "DJANGO_SETTINGS_MODULE": settings_modul,
                # Die Vorlagenwerte stehen bereits in der Subprozess-Env — eine
                # lokale .env des Entwicklungsrechners darf hier nichts ergaenzen.
                "DJANGO_LOAD_DOTENV": "false",
            },
            expr=f"import {settings_modul} as s;print(s.EXTERNAL_ORIGIN)",
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == port_erwartung

    def test_dev_bleibt_beim_lokalen_runserver(self):
        self._origin_mit_env_example("anlaufstelle.settings.dev", "http://localhost:8000")

    def test_e2e_bleibt_beim_e2e_server(self):
        self._origin_mit_env_example("anlaufstelle.settings.e2e", "http://localhost:8844")
