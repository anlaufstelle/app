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
    # 32+ Zeichen, kein change-me-Prefix — der N6-Guard (POSTGRES_PASSWORD)
    # aus prod.py greift auch beim demo-Import (Refs #1591).
    "POSTGRES_PASSWORD": "p" * 40,
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
            "from anlaufstelle.settings.demo import DEMO_LOGINS;print([entry['username'] for entry in DEMO_LOGINS])"
        )
        assert result.returncode == 0, result.stderr
        usernames = result.stdout.strip()
        assert "superadmin" not in usernames, f"superadmin darf nicht in DEMO_LOGINS stehen: {usernames}"

    def test_non_privileged_demo_logins_remain(self):
        """Die uebrigen Demo-Logins bleiben unveraendert erhalten."""
        result = _run_demo_import(
            "from anlaufstelle.settings.demo import DEMO_LOGINS;print([entry['username'] for entry in DEMO_LOGINS])"
        )
        assert result.returncode == 0, result.stderr
        for username in ("admin", "emma", "miriam", "markus", "lena", "felix"):
            assert username in result.stdout, f"{username} fehlt in DEMO_LOGINS: {result.stdout}"


class TestDemoUmami:
    """Refs #1604: Demo trackt via Umami inkl. Recorder/Heatmap, aber
    Consent-Opt-in, eigene Website-ID und STRIKTE globale CSP (Lockerung nur
    Post-Login per Middleware)."""

    def test_website_id_is_demo_specific(self):
        result = _run_demo_import("from anlaufstelle.settings.demo import UMAMI_WEBSITE_ID;print(UMAMI_WEBSITE_ID)")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "00000000-0000-4000-8000-000000000002"

    def test_recorder_src_is_example_recorder_js(self):
        result = _run_demo_import("from anlaufstelle.settings.demo import UMAMI_RECORDER_SRC;print(UMAMI_RECORDER_SRC)")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "https://umami.example.org/recorder.js"

    def test_consent_is_required(self):
        result = _run_demo_import(
            "from anlaufstelle.settings.demo import UMAMI_REQUIRE_CONSENT;print(UMAMI_REQUIRE_CONSENT)"
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "True"

    def test_legal_urls_point_to_own_demo_pages(self):
        """Refs #1641: Die Rechtstexte sind eigene Seiten der Demo (interne
        Pfade ``/impressum/`` + ``/datenschutz/``), nicht mehr die (404
        liefernden) Webseiten-Links."""
        result = _run_demo_import(
            "from anlaufstelle.settings.demo import DEMO_LEGAL_IMPRINT_URL, DEMO_LEGAL_PRIVACY_URL;"
            "print(DEMO_LEGAL_IMPRINT_URL, DEMO_LEGAL_PRIVACY_URL)"
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "/impressum/ /datenschutz/"

    def test_global_csp_stays_strict_no_umami_host(self):
        """Auf der Demo darf der Umami-Host NICHT global in der CSP stehen —
        die Lockerung ist Post-Login-scoped (DemoUmamiCSPRelaxMiddleware).
        devlive gibt ihn global frei; demo setzt die CSP wieder strikt."""
        result = _run_demo_import(
            "from anlaufstelle.settings.demo import CONTENT_SECURITY_POLICY as c;"
            "d=c['DIRECTIVES'];"
            "print('https://umami.example.org' in d['script-src'],"
            "'https://umami.example.org' in d['connect-src'])"
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "False False"

    def test_relax_middleware_registered(self):
        result = _run_demo_import(
            "from anlaufstelle.settings.demo import MIDDLEWARE;"
            "print('core.middleware.demo_umami_csp_relax.DemoUmamiCSPRelaxMiddleware' in MIDDLEWARE)"
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "True"
