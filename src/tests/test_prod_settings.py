"""Fail-closed guarantees for production settings (Refs #558).

These tests execute the production settings module in an isolated Python
subprocess with controlled env vars. A successful exit-code means the module
imported cleanly; a non-zero exit-code means it raised ImproperlyConfigured,
which is the expected fail-closed behaviour when a required variable is
missing.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"


def _run_prod_import(env_overrides, code="import anlaufstelle.settings.prod  # noqa"):
    """Import prod settings in a subprocess with a clean environment.

    All sensitive vars are pre-initialised to an empty string so that the
    project's ``load_dotenv()`` call cannot smuggle values from the local
    ``.env`` file into the test (dotenv does not override vars that are
    already set, even if their value is the empty string).

    ``code`` is the snippet executed in the subprocess; the default merely
    imports the module (enough to trigger the fail-closed guards). Pass a
    custom snippet to read back resolved setting values (see A5.5 guard).
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
        "DJANGO_AUDIT_HASH_KEY": "",
    }
    env.update(env_overrides)
    result = subprocess.run(
        [sys.executable, "-c", code],
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

    def test_missing_audit_hash_key_raises(self):
        """A4.2 (Refs #1024 / #1016): ohne DJANGO_AUDIT_HASH_KEY darf Prod nicht
        starten — sonst fällt ``services.audit.hash`` still auf
        SHA256(SECRET_KEY) zurück und ein SECRET_KEY-Leak knackt rückwirkend
        die Audit-Hashes."""
        result = _run_prod_import(
            {
                "DJANGO_SECRET_KEY": "x" * 50,
                "ALLOWED_HOSTS": "example.org",
                "ENCRYPTION_KEY": "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZmE=",
            }
        )
        assert result.returncode != 0
        assert "AUDIT_HASH_KEY" in result.stderr

    def test_encryption_keys_plural_accepted(self):
        """ENCRYPTION_KEYS alone (without singular ENCRYPTION_KEY) must load."""
        result = _run_prod_import(
            {
                "DJANGO_SECRET_KEY": "x" * 50,
                "ALLOWED_HOSTS": "example.org",
                "ENCRYPTION_KEYS": "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZmE=",
                "DJANGO_AUDIT_HASH_KEY": "y" * 50,
            }
        )
        assert result.returncode == 0, f"Import failed unexpectedly: {result.stderr}"

    def test_clamav_disabled_raises(self):
        """#1267 (T2): In Produktion MUSS der Virenscan aktiv sein.

        ``base.py`` defaultet ``CLAMAV_ENABLED=false`` (Dev/Test-Bypass:
        ``virus_scan.scan_file`` liefert dann ohne ClamAV-Kontakt „clean"). Wird
        der Scanner in Prod explizit abgeschaltet, würden Uploads ungescannt
        akzeptiert — daher fail-closed: lieber Server-Start-Fehler als stiller
        Scanner-Bypass."""
        result = _run_prod_import(
            {
                "DJANGO_SECRET_KEY": "x" * 50,
                "ALLOWED_HOSTS": "example.org",
                "ENCRYPTION_KEY": "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZmE=",
                "DJANGO_AUDIT_HASH_KEY": "y" * 50,
                "CLAMAV_ENABLED": "false",
            }
        )
        assert result.returncode != 0
        assert "CLAMAV_ENABLED" in result.stderr

    def test_clamav_enabled_by_default(self):
        """Ohne explizites ``CLAMAV_ENABLED`` ist der Scanner in Prod an (Default
        ``true``) und der Guard lässt den Import passieren (#1267)."""
        result = _run_prod_import(
            {
                "DJANGO_SECRET_KEY": "x" * 50,
                "ALLOWED_HOSTS": "example.org",
                "ENCRYPTION_KEY": "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZmE=",
                "DJANGO_AUDIT_HASH_KEY": "y" * 50,
            }
        )
        assert result.returncode == 0, f"Import failed unexpectedly: {result.stderr}"


class TestProdSettingsSecurityDefaultsGuard:
    """A5.5 (Refs #1024 / #1016): explizite Defense-in-Depth-Settings einfrieren.

    ``SESSION_COOKIE_HTTPONLY`` und ``SECURE_CROSS_ORIGIN_OPENER_POLICY`` sind
    unter Django 6 bereits sicher per Default — hier in ``prod.py`` explizit
    gesetzt, damit ein Default-Wechsel oder versehentliches Entfernen auffällt
    (Auditierbarkeit/Regressionsschutz, kein offenes Loch). Die Request-Body-
    Limits werden ebenfalls explizit verankert. Dieser Test friert die Werte ein.
    """

    _VALID_ENV = {
        "DJANGO_SECRET_KEY": "x" * 50,
        "ALLOWED_HOSTS": "example.org",
        "ENCRYPTION_KEY": "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZmE=",
        "DJANGO_AUDIT_HASH_KEY": "y" * 50,
    }

    def _resolved(self):
        code = (
            "import json, anlaufstelle.settings.prod as p; "
            "print(json.dumps({"
            "'httponly': p.SESSION_COOKIE_HTTPONLY, "
            "'coop': p.SECURE_CROSS_ORIGIN_OPENER_POLICY, "
            "'data_max': p.DATA_UPLOAD_MAX_MEMORY_SIZE, "
            "'file_max': p.FILE_UPLOAD_MAX_MEMORY_SIZE}))"
        )
        result = _run_prod_import(self._VALID_ENV, code=code)
        assert result.returncode == 0, f"prod import failed: {result.stderr}"
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_session_cookie_httponly_explicit(self):
        assert self._resolved()["httponly"] is True

    def test_cross_origin_opener_policy_explicit(self):
        assert self._resolved()["coop"] == "same-origin"

    def test_request_body_limits_explicit(self):
        resolved = self._resolved()
        assert resolved["data_max"] > 0
        assert resolved["file_max"] > 0


class TestBaseSettingsDotenvGuard:
    """A7.3 (Refs #1024 / #1016): ``load_dotenv()`` darf in ``base.py`` nicht
    bedingungslos laufen.

    In Produktion wird die Konfiguration über die Orchestrierungs-Env
    (Docker/systemd) gesetzt. Eine versehentlich ins Image/Volume geratene
    ``.env`` soll diese nicht still ergänzen — der Aufruf muss per
    ``DJANGO_LOAD_DOTENV`` abschaltbar und an die Datei-Existenz gebunden sein.
    """

    _BASE_SETTINGS = SRC_DIR / "anlaufstelle" / "settings" / "base.py"

    def test_load_dotenv_is_opt_out_guarded(self):
        source = self._BASE_SETTINGS.read_text()
        assert "DJANGO_LOAD_DOTENV" in source, (
            "base.py muss load_dotenv() per DJANGO_LOAD_DOTENV abschaltbar machen "
            "— sonst ergänzt eine versehentlich gemountete .env in Prod still die "
            "Orchestrierungs-Env. Refs #1024."
        )


class TestProdSharedCacheGuard:
    """A5.1 (Refs #1024 / #1016): Prod nutzt einen prozessübergreifenden
    DatabaseCache statt des per-Worker isolierten LocMemCache — sonst zählen
    django-ratelimit und das Health-Caching pro Worker getrennt."""

    _VALID_ENV = {
        "DJANGO_SECRET_KEY": "x" * 50,
        "ALLOWED_HOSTS": "example.org",
        "ENCRYPTION_KEY": "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZmE=",
        "DJANGO_AUDIT_HASH_KEY": "y" * 50,
    }

    def test_uses_database_cache_with_ratelimit(self):
        code = (
            "import json, anlaufstelle.settings.prod as p; "
            "print(json.dumps({"
            "'backend': p.CACHES['default']['BACKEND'], "
            "'location': p.CACHES['default']['LOCATION'], "
            "'rl_cache': getattr(p, 'RATELIMIT_USE_CACHE', None)}))"
        )
        result = _run_prod_import(self._VALID_ENV, code=code)
        assert result.returncode == 0, f"prod import failed: {result.stderr}"
        data = json.loads(result.stdout.strip().splitlines()[-1])
        assert data["backend"] == "django.core.cache.backends.db.DatabaseCache"
        assert data["location"] == "anlaufstelle_cache"
        assert data["rl_cache"] == "default"


class TestProdPlaceholderSecretsGuard:
    """Security N6: .env.example lieferte nicht-leere change-me-Platzhalter,
    die Guards pruefen aber nur auf leer. Wer die Datei kopiert und nur
    ENCRYPTION_KEY setzt, startet Prod mit oeffentlich bekanntem SECRET_KEY
    (faelschbare Reset-/Invite-Token) und AUDIT_HASH_KEY."""

    _VALID_ENV = {
        "DJANGO_SECRET_KEY": "x" * 50,
        "ALLOWED_HOSTS": "example.org",
        "ENCRYPTION_KEY": "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZmE=",
        "DJANGO_AUDIT_HASH_KEY": "y" * 50,
    }

    def test_change_me_secret_key_rejected(self):
        result = _run_prod_import({**self._VALID_ENV, "DJANGO_SECRET_KEY": "change-me-to-a-random-string"})
        assert result.returncode != 0
        assert "DJANGO_SECRET_KEY" in result.stderr

    def test_short_secret_key_rejected(self):
        result = _run_prod_import({**self._VALID_ENV, "DJANGO_SECRET_KEY": "x" * 16})
        assert result.returncode != 0
        assert "DJANGO_SECRET_KEY" in result.stderr

    def test_change_me_audit_hash_key_rejected(self):
        result = _run_prod_import({**self._VALID_ENV, "DJANGO_AUDIT_HASH_KEY": "change-me-to-a-separate-random-string"})
        assert result.returncode != 0
        assert "AUDIT_HASH_KEY" in result.stderr

    def test_valid_env_still_boots(self):
        result = _run_prod_import(self._VALID_ENV)
        assert result.returncode == 0, f"prod import failed: {result.stderr}"

    @pytest.mark.architecture
    def test_env_example_ships_empty_secret_placeholders(self):
        """Die Wurzel des Footguns: .env.example darf keine nicht-leeren
        Platzhalter fuer Secrets mehr ausliefern.

        ``architecture``-Marker: liest ``.env.example`` aus dem Repo-Root —
        die Datei existiert in mutmuts ``mutants/``-Arbeitskopie nicht,
        der Mutation-Vorlauf bräche sonst ab (in ``make ci`` läuft der
        Guard weiterhin)."""
        content = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        for var in ("DJANGO_SECRET_KEY", "DJANGO_AUDIT_HASH_KEY", "BACKUP_ENCRYPTION_KEY"):
            match = re.search(rf"^{var}=(.*)$", content, re.MULTILINE)
            assert match is not None, f"{var} fehlt in .env.example"
            assert match.group(1).strip() == "", (
                f"{var} muss in .env.example LEER sein — nicht-leere Platzhalter "
                "ueberleben den Nur-leer-Guard (Review N6)."
            )


class TestProdAllowedHostsWildcardGuard:
    """C4 (Refs #1376 I4): Prod darf nicht mit einem Wildcard-Eintrag `*` in
    ALLOWED_HOSTS starten. `*` ist in Django Match-all — es hebelt die
    einzige Huerde gegen Host-Header-Poisoning (z. B. gefaelschte
    Passwort-Reset-Links) aus. Subdomain-Patterns wie `.example.com` sind
    KEIN Match-all und bleiben erlaubt."""

    _VALID_ENV = {
        "DJANGO_SECRET_KEY": "x" * 50,
        "ENCRYPTION_KEY": "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZmE=",
        "DJANGO_AUDIT_HASH_KEY": "y" * 50,
    }

    def test_wildcard_only_rejected(self):
        result = _run_prod_import({**self._VALID_ENV, "ALLOWED_HOSTS": "*"})
        assert result.returncode != 0
        assert "ALLOWED_HOSTS" in result.stderr

    def test_wildcard_as_list_element_rejected(self):
        result = _run_prod_import({**self._VALID_ENV, "ALLOWED_HOSTS": "example.org,*"})
        assert result.returncode != 0
        assert "ALLOWED_HOSTS" in result.stderr

    def test_subdomain_pattern_still_allowed(self):
        """Regression: `.example.com`-Subdomain-Patterns sind kein Match-all
        und duerfen den Guard nicht ausloesen."""
        result = _run_prod_import({**self._VALID_ENV, "ALLOWED_HOSTS": "example.org,.example.org"})
        assert result.returncode == 0, f"prod import failed: {result.stderr}"
