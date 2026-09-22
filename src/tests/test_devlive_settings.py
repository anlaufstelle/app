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
    # Refs #1024 A4.2: devlive erbt von prod.py, das AUDIT_HASH_KEY fail-closed
    # verlangt — daher hier als Pflicht-ENV für den Import nötig.
    "DJANGO_AUDIT_HASH_KEY": "y" * 50,
    "POSTGRES_DB": "x",
    "POSTGRES_USER": "u",
    # 32+ Zeichen, kein change-me-Prefix — der N6-Guard (POSTGRES_PASSWORD)
    # aus prod.py greift auch beim devlive-Import (Refs #1591).
    "POSTGRES_PASSWORD": "p" * 40,
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5432",
}


def _devlive_env(env_overrides=None):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(SRC_DIR),
        "DJANGO_SETTINGS_MODULE": "anlaufstelle.settings.devlive",
        # dotenv-Smuggling unterbinden (siehe test_prod_settings.py).
        "ENCRYPTION_KEYS": "",
        # .env-Laden komplett abschalten: die UMAMI_*-Werte kommen seit #1701
        # aus der Deploy-Env — eine lokale .env mit echten Werten wuerde sonst
        # die Default-leer-Asserts dieser Tests unterlaufen.
        "DJANGO_LOAD_DOTENV": "false",
    }
    env.update(_VALID_ENV)
    if env_overrides:
        env.update(env_overrides)
    return env


def _run_devlive_import(env_overrides=None, expr="import anlaufstelle.settings.devlive  # noqa"):
    return subprocess.run(
        [sys.executable, "-c", expr],
        env=_devlive_env(env_overrides),
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


def _run_devlive_check_deploy(env_overrides=None):
    """``manage.py check --deploy`` unter devlive — derselbe Aufruf wie im
    Image-Entrypoint (``docker-entrypoint.sh``) vor dem Gunicorn-Start."""
    return subprocess.run(
        [sys.executable, str(SRC_DIR / "manage.py"), "check", "--deploy"],
        env=_devlive_env(env_overrides),
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
            expr=("from anlaufstelle.settings.devlive import MAILERS;print(MAILERS['default']['BACKEND'])")
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "django.core.mail.backends.console.EmailBackend"

    def test_console_mailer_carries_no_smtp_options(self):
        """Der geerbte prod-SMTP-Mailer wird vollstaendig ersetzt (Refs #1662)."""
        result = _run_devlive_import(
            expr=("from anlaufstelle.settings.devlive import MAILERS;print(sorted(MAILERS['default']))")
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "['BACKEND']"

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


class TestDevLiveUmami:
    """Refs #1602/#1701: Die Umami-Werte kommen aus der Deploy-Env — der
    Release-Quelltext traegt keine instanzspezifischen Website-IDs/Hosts mehr.
    Ohne Env bleibt devlive tracking-frei mit strikter base-CSP; mit Env wird
    der Host des Tracker-Skripts in script-src/connect-src freigegeben — ohne
    das geteilte base-CSP-Dict zu mutieren (Deep-Copy-Isolation)."""

    _UMAMI_ENV = {
        "UMAMI_WEBSITE_ID": "11111111-2222-3333-4444-555555555555",
        "UMAMI_SCRIPT_SRC": "https://umami.example.org/main.js",
    }

    def test_unset_env_leaves_tracking_disabled(self):
        result = _run_devlive_import(
            expr=(
                "from anlaufstelle.settings.devlive import UMAMI_WEBSITE_ID, UMAMI_SCRIPT_SRC;"
                "print(UMAMI_WEBSITE_ID, repr(UMAMI_SCRIPT_SRC))"
            )
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "None ''"

    def test_empty_env_values_leave_tracking_disabled(self):
        """Leere Env-Strings (z.B. auskommentierte .env-Zeile) zaehlen als
        nicht gesetzt — kein halb-konfigurierter Tracker."""
        result = _run_devlive_import(
            {"UMAMI_WEBSITE_ID": "", "UMAMI_SCRIPT_SRC": ""},
            expr=(
                "from anlaufstelle.settings.devlive import UMAMI_WEBSITE_ID, UMAMI_SCRIPT_SRC;"
                "print(UMAMI_WEBSITE_ID, repr(UMAMI_SCRIPT_SRC))"
            ),
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "None ''"

    def test_half_configured_env_disables_tracking(self):
        """Nur ID ohne Skript-URL (oder umgekehrt) zaehlt als AUS — sonst
        wuerde base.html ein ``<script src="">`` einbinden (die Seite selbst
        als Skript) bzw. ein Skript ohne Website-ID laden."""
        result = _run_devlive_import(
            {"UMAMI_WEBSITE_ID": "11111111-2222-3333-4444-555555555555"},
            expr=(
                "from anlaufstelle.settings.devlive import UMAMI_WEBSITE_ID, UMAMI_SCRIPT_SRC;"
                "print(UMAMI_WEBSITE_ID, repr(UMAMI_SCRIPT_SRC))"
            ),
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "None ''"

    def test_unset_env_keeps_csp_identical_to_base(self):
        """Ohne konfigurierten Tracker gibt es KEINE CSP-Lockerung — devlive
        ist dann von der strikten base-CSP nicht unterscheidbar."""
        result = _run_devlive_import(
            expr=(
                "from anlaufstelle.settings.devlive import CONTENT_SECURITY_POLICY as c;"
                "from anlaufstelle.settings.base import CONTENT_SECURITY_POLICY as b;"
                "print(c == b)"
            )
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "True"

    def test_env_values_are_passed_through(self):
        result = _run_devlive_import(
            self._UMAMI_ENV,
            expr=(
                "from anlaufstelle.settings.devlive import UMAMI_WEBSITE_ID, UMAMI_SCRIPT_SRC;"
                "print(UMAMI_WEBSITE_ID, UMAMI_SCRIPT_SRC)"
            ),
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ("11111111-2222-3333-4444-555555555555 https://umami.example.org/main.js")

    def test_csp_allows_configured_host_in_script_and_connect_src(self):
        """Der CSP-Token ist der HOST des Tracker-Skripts (Origin, ohne Pfad)
        — script-src (Tracker-Skript) UND connect-src (Beacon-POSTs)."""
        result = _run_devlive_import(
            self._UMAMI_ENV,
            expr=(
                "from anlaufstelle.settings.devlive import CONTENT_SECURITY_POLICY as c;"
                "d=c['DIRECTIVES'];"
                "print('https://umami.example.org' in d['script-src'],"
                "'https://umami.example.org' in d['connect-src'])"
            ),
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "True True"

    def test_deepcopy_leaves_base_csp_strict(self):
        """Der Import von devlive darf das GETEILTE base-CSP-Dict nicht
        mutieren — sonst wuerde die globale strikte script-src 'self'
        (Schutzwall fuer window.offlineStore, ADR-022) fuer alle anderen
        Settings-Module aufgeweicht."""
        result = _run_devlive_import(
            self._UMAMI_ENV,
            expr=(
                "import anlaufstelle.settings.devlive;"  # importiert + mutiert die Kopie
                "from anlaufstelle.settings.base import CONTENT_SECURITY_POLICY as b;"
                "print('https://umami.example.org' in b['DIRECTIVES']['script-src'],"
                "'https://umami.example.org' in b['DIRECTIVES']['connect-src'])"
            ),
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "False False"


class TestDevLiveMailerDeployCheck:
    """Refs #2326: Django 6.1 wertet ein Entwicklungs-Backend im
    ``MAILERS["default"]`` unter ``check --deploy`` als Fehler ``mail.E001``.
    Der Image-Entrypoint faehrt genau diesen Check, sodass dev.anlaufstelle.app
    (kein SMTP, ``.env`` ohne ``EMAIL_*``) mit dem festen Konsolen-Backend
    nicht mehr startete. devlive faellt deshalb nur ohne ``EMAIL_HOST`` auf die
    Konsole zurueck und schaltet ausschliesslich dafuer ``mail.E001`` stumm;
    mit ``EMAIL_HOST`` bleibt der SMTP-Mailer aus prod erhalten (SMTP-Nachweis
    auf dev, Refs #2089) und der Check bleibt scharf.
    """

    _SMTP_ENV = {"EMAIL_HOST": "smtp.example.org", "EMAIL_PORT": "2525"}

    # MEDIA_ROOT ausserhalb von BASE_DIR, sonst bricht ``core.E002`` den
    # Deploy-Check unabhaengig vom Mailer ab (Refs #1453).
    @staticmethod
    def _deploy_env(tmp_path, extra=None):
        return {"MEDIA_ROOT": str(tmp_path / "media"), **(extra or {})}

    def test_ohne_email_host_konsole_und_nur_mail_e001_stumm(self):
        result = _run_devlive_import(
            expr=(
                "from anlaufstelle.settings.devlive import MAILERS, SILENCED_SYSTEM_CHECKS;"
                "print(MAILERS['default']['BACKEND'], sorted(SILENCED_SYSTEM_CHECKS))"
            )
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "django.core.mail.backends.console.EmailBackend ['mail.E001']"

    def test_leerer_email_host_zaehlt_als_ungesetzt(self):
        """Compose/dotenv reichen unbelegte Variablen als Leerstring durch."""
        result = _run_devlive_import(
            {"EMAIL_HOST": ""},
            expr="from anlaufstelle.settings.devlive import MAILERS;print(MAILERS['default']['BACKEND'])",
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "django.core.mail.backends.console.EmailBackend"

    def test_mit_email_host_smtp_wie_prod_ohne_stummschaltung(self):
        result = _run_devlive_import(
            self._SMTP_ENV,
            expr=(
                "import anlaufstelle.settings.devlive as d;"
                "m=d.MAILERS['default'];"
                "print(m['BACKEND'], m['OPTIONS']['host'], m['OPTIONS']['port'],"
                "'mail.E001' in getattr(d, 'SILENCED_SYSTEM_CHECKS', []))"
            ),
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "django.core.mail.backends.smtp.EmailBackend smtp.example.org 2525 False"

    def test_check_deploy_ohne_email_host_ist_gruen(self, tmp_path):
        result = _run_devlive_check_deploy(self._deploy_env(tmp_path))
        ausgabe = result.stdout + result.stderr
        assert result.returncode == 0, ausgabe
        assert "mail.E001" not in ausgabe
        # Der Check laeuft weiterhin — er ist stummgeschaltet, nicht verschwunden.
        assert "(1 silenced)" in ausgabe, ausgabe

    def test_check_deploy_mit_email_host_ist_gruen_und_schaltet_nichts_stumm(self, tmp_path):
        result = _run_devlive_check_deploy(self._deploy_env(tmp_path, self._SMTP_ENV))
        ausgabe = result.stdout + result.stderr
        assert result.returncode == 0, ausgabe
        assert "mail.E001" not in ausgabe
        assert "(0 silenced)" in ausgabe, ausgabe
