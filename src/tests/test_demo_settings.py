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


def _run_demo_import(expr, env_overrides=None):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(SRC_DIR),
        "DJANGO_SETTINGS_MODULE": "anlaufstelle.settings.demo",
        "ENCRYPTION_KEYS": "",
        # .env-Laden abschalten: die UMAMI_*-Werte kommen seit #1701 aus der
        # Deploy-Env — eine lokale .env mit echten Werten wuerde sonst die
        # Default-leer-Asserts dieser Tests unterlaufen.
        "DJANGO_LOAD_DOTENV": "false",
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


_UMAMI_ENV = {
    "UMAMI_WEBSITE_ID": "22222222-3333-4444-5555-666666666666",
    "UMAMI_SCRIPT_SRC": "https://umami.example.org/main.js",
    "UMAMI_RECORDER_SRC": "https://umami.example.org/recorder.js",
}


class TestDemoUmami:
    """Refs #1604/#1701: Demo trackt via Umami inkl. Recorder/Heatmap —
    Website-ID/Script-URLs kommen aus der Deploy-Env (keine instanzspezifischen
    Werte im Release-Quelltext), Consent-Opt-in bleibt hart verdrahtet und die
    globale CSP STRIKT (Lockerung nur Post-Login per Middleware)."""

    def test_unset_env_disables_tracking(self):
        result = _run_demo_import(
            "from anlaufstelle.settings.demo import UMAMI_WEBSITE_ID, UMAMI_SCRIPT_SRC, UMAMI_RECORDER_SRC;"
            "print(UMAMI_WEBSITE_ID, repr(UMAMI_SCRIPT_SRC), repr(UMAMI_RECORDER_SRC))"
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "None '' ''"

    def test_website_id_passes_through_from_env(self):
        result = _run_demo_import(
            "from anlaufstelle.settings.demo import UMAMI_WEBSITE_ID;print(UMAMI_WEBSITE_ID)",
            env_overrides=_UMAMI_ENV,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "22222222-3333-4444-5555-666666666666"

    def test_recorder_src_passes_through_from_env(self):
        result = _run_demo_import(
            "from anlaufstelle.settings.demo import UMAMI_RECORDER_SRC;print(UMAMI_RECORDER_SRC)",
            env_overrides=_UMAMI_ENV,
        )
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

    def test_global_csp_stays_strict_even_with_env_values(self):
        """Auf der Demo darf der Umami-Host NICHT global in der CSP stehen —
        auch dann nicht, wenn die UMAMI_*-Env-Werte gesetzt sind. devlive gibt
        den Host global frei; demo setzt die CSP wieder strikt, die Lockerung
        ist Post-Login-scoped (DemoUmamiCSPRelaxMiddleware)."""
        result = _run_demo_import(
            "from anlaufstelle.settings.demo import CONTENT_SECURITY_POLICY as c;"
            "d=c['DIRECTIVES'];"
            "print('https://umami.example.org' in d['script-src'],"
            "'https://umami.example.org' in d['connect-src'])",
            env_overrides=_UMAMI_ENV,
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


class TestExtraTemplateDir:
    """Refs #1701: ``EXTRA_TEMPLATE_DIR`` haengt ein Deploy-Overlay VOR die
    Projekt-Templates — so liefert die Demo ihre echten Rechtstexte per
    gemountetem Verzeichnis, ohne dass die Inhalte im Release-Quelltext liegen."""

    def test_unset_env_keeps_project_templates_only(self):
        result = _run_demo_import(
            "from anlaufstelle.settings.demo import TEMPLATES;"
            "dirs=TEMPLATES[0]['DIRS'];"
            "print(len(dirs), str(dirs[0]).endswith('/templates'))"
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "1 True"

    def test_env_dir_is_prepended_and_wins(self):
        result = _run_demo_import(
            "from anlaufstelle.settings.demo import TEMPLATES;dirs=TEMPLATES[0]['DIRS'];print(len(dirs), str(dirs[0]))",
            env_overrides={"EXTRA_TEMPLATE_DIR": "/srv/overlay-templates"},
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "2 /srv/overlay-templates"
