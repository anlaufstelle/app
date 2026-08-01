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
        # 32+ Zeichen, kein change-me-Prefix — der N6-Guard (POSTGRES_PASSWORD)
        # laesst diesen Wert passieren, sodass "should boot"-Tests gruen bleiben.
        "POSTGRES_PASSWORD": "p" * 40,
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "5432",
        # Pre-empt dotenv pass-through of local secrets.
        "DJANGO_SECRET_KEY": "",
        "ALLOWED_HOSTS": "",
        "ENCRYPTION_KEY": "",
        "ENCRYPTION_KEYS": "",
        "DJANGO_AUDIT_HASH_KEY": "",
        # Der N6-Guard liest die DB-Admin-/Bootstrap-Passwoerter direkt aus
        # os.environ (keine Django-Settings). Ohne Vorbelegung koennte eine
        # lokale .env (z.B. POSTGRES_ADMIN_PASSWORD=change-me-admin) per
        # load_dotenv(override=False) durchgreifen und "should boot"-Tests
        # faelschlich rot faerben — daher hier ebenfalls auf leer vorbelegen.
        "POSTGRES_ADMIN_PASSWORD": "",
        "POSTGRES_BOOTSTRAP_PASSWORD": "",
        # Refs #795/#1657: den optionalen Redis-Umschalter leer vorbelegen, damit
        # eine lokale .env den DatabaseCache-Default-Test nicht per dotenv-
        # Durchgriff auf RedisCache kippt.
        "REDIS_URL": "",
        # Refs #1661: analog REDIS_URL — ohne Vorbelegung koennte eine lokale
        # .env (z. B. FILE_UPLOAD_TEMP_DIR=/tmp) per load_dotenv(override=False)
        # durchgreifen und den "unset bleibt Django-Default"-Test faelschlich
        # gruen/rot faerben.
        "FILE_UPLOAD_TEMP_DIR": "",
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

    def test_redis_url_switches_to_redis_cache(self):
        """Refs #795 (voller Scope): Ist ``REDIS_URL`` gesetzt, schaltet prod.py
        vom DatabaseCache-Fallback auf RedisCache um. ``docker-compose.prod.yml``
        setzt die Variable ab Werk auf den mitgelieferten ``redis``-Service;
        derselbe Pfad traegt Betreiber mit einem eigenen, extern betriebenen
        Redis. Hier wird nur die Settings-Aufloesung geprueft (kein
        Verbindungsaufbau)."""
        code = (
            "import json, anlaufstelle.settings.prod as p; "
            "print(json.dumps({"
            "'backend': p.CACHES['default']['BACKEND'], "
            "'location': p.CACHES['default']['LOCATION'], "
            "'rl_cache': getattr(p, 'RATELIMIT_USE_CACHE', None)}))"
        )
        redis_url = "redis://cache.internal:6379/1"
        result = _run_prod_import({**self._VALID_ENV, "REDIS_URL": redis_url}, code=code)
        assert result.returncode == 0, f"prod import failed: {result.stderr}"
        data = json.loads(result.stdout.strip().splitlines()[-1])
        assert data["backend"] == "django.core.cache.backends.redis.RedisCache"
        assert data["location"] == redis_url
        # Rate-Limit haengt am selben "default"-Alias, egal welches Backend.
        assert data["rl_cache"] == "default"

    def test_single_cache_alias_stays_wired_to_ratelimit(self):
        """Refs #795: genau EIN Cache-Alias, und ``RATELIMIT_USE_CACHE`` zeigt darauf.

        Die urspruengliche Issue-Skizze sah einen zweiten Alias ``ratelimit`` auf
        Redis-DB ``/2`` vor. Umgesetzt ist er bewusst nicht: kein Code liest
        ``caches["ratelimit"]``, django-ratelimit haengt an
        ``RATELIMIT_USE_CACHE`` — ein zweiter Alias waere tote Konfiguration und
        muesste im DatabaseCache-Fallback zusaetzlich eine zweite Cache-Tabelle
        (= weitere Migration) tragen. Dieser Guard haelt beides zusammen: wer
        spaeter einen Alias ergaenzt, muss ``RATELIMIT_USE_CACHE`` bewusst
        mitziehen, statt den Rate-Limit-Zaehler still im Default-Alias zu lassen
        (wo ihn ein ``cache.clear()`` mit abraeumt)."""
        code = (
            "import json, anlaufstelle.settings.prod as p; "
            "print(json.dumps({"
            "'aliases': sorted(p.CACHES), "
            "'rl_cache': getattr(p, 'RATELIMIT_USE_CACHE', None)}))"
        )
        result = _run_prod_import(self._VALID_ENV, code=code)
        assert result.returncode == 0, f"prod import failed: {result.stderr}"
        data = json.loads(result.stdout.strip().splitlines()[-1])
        assert data["aliases"] == ["default"]
        assert data["rl_cache"] in data["aliases"]

    def test_missing_redis_package_falls_back_to_database_cache(self, tmp_path):
        """Refs #795: gesetztes ``REDIS_URL`` + fehlendes ``redis``-Paket degradiert
        sichtbar, statt den Stack zu zerlegen.

        Reales Szenario: Betreiber zieht das neue ``docker-compose.prod.yml``
        (das ``REDIS_URL`` ab Werk setzt), faehrt per ``APP_VERSION`` aber noch
        ein aelteres App-Image ohne ``redis-py``. Ohne dieses Netz wuerde JEDER
        Cache-Zugriff werfen — also 500er auf allen rate-limitierten Views.
        Simuliert wird das ueber ein Shadow-Modul ``redis.py``, das den Import
        scheitern laesst; es liegt vor ``site-packages`` im PYTHONPATH."""
        shadow = tmp_path / "redis.py"
        shadow.write_text("raise ImportError('simulated: redis-py not installed')\n", encoding="utf-8")
        code = (
            "import json, anlaufstelle.settings.prod as p; "
            "print(json.dumps({'backend': p.CACHES['default']['BACKEND'], "
            "'location': p.CACHES['default']['LOCATION']}))"
        )
        result = _run_prod_import(
            {
                **self._VALID_ENV,
                "REDIS_URL": "redis://redis:6379/1",
                "PYTHONPATH": os.pathsep.join([str(tmp_path), str(SRC_DIR)]),
            },
            code=code,
        )
        assert result.returncode == 0, f"prod import failed: {result.stderr}"
        data = json.loads(result.stdout.strip().splitlines()[-1])
        assert data["backend"] == "django.core.cache.backends.db.DatabaseCache"
        assert data["location"] == "anlaufstelle_cache"
        # Der Rueckfall darf nicht still passieren.
        assert "REDIS_URL" in result.stderr and "DatabaseCache" in result.stderr


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

    def test_change_me_postgres_password_rejected(self):
        """`.env.example` liefert `POSTGRES_PASSWORD=change-me` (Refs #1591) —
        der N6-Guard muss den Platzhalter genauso ablehnen wie bei SECRET_KEY."""
        result = _run_prod_import({**self._VALID_ENV, "POSTGRES_PASSWORD": "change-me"})
        assert result.returncode != 0
        assert "POSTGRES_PASSWORD" in result.stderr

    def test_change_me_postgres_admin_password_rejected(self):
        result = _run_prod_import({**self._VALID_ENV, "POSTGRES_ADMIN_PASSWORD": "change-me-admin"})
        assert result.returncode != 0
        assert "POSTGRES_ADMIN_PASSWORD" in result.stderr

    def test_change_me_postgres_bootstrap_password_rejected(self):
        result = _run_prod_import({**self._VALID_ENV, "POSTGRES_BOOTSTRAP_PASSWORD": "change-me-bootstrap"})
        assert result.returncode != 0
        assert "POSTGRES_BOOTSTRAP_PASSWORD" in result.stderr

    def test_short_postgres_password_rejected(self):
        """Zu wenig Entropie (< 32 Zeichen) wird wie bei den anderen Secrets abgelehnt."""
        result = _run_prod_import({**self._VALID_ENV, "POSTGRES_PASSWORD": "x" * 16})
        assert result.returncode != 0
        assert "POSTGRES_PASSWORD" in result.stderr

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


class TestProdWebAuthnRpIdDerivation:
    """ADR-032 (Refs #1492): die WebAuthn-RP-ID wird aus ALLOWED_HOSTS abgeleitet,
    wenn keine Env-Var gesetzt ist. Sie darf dabei NICHT still leer bleiben, wenn
    ALLOWED_HOSTS nur Subdomain-Wildcards ('.example.com') enthaelt — sonst
    schluege die Passkey-Registrierung zur Laufzeit lautlos fehl (py_webauthn
    verlangt eine nicht-leere RP-ID)."""

    _VALID_ENV = {
        "DJANGO_SECRET_KEY": "x" * 50,
        "ENCRYPTION_KEY": "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZmE=",
        "DJANGO_AUDIT_HASH_KEY": "y" * 50,
    }

    _READBACK = (
        "import json, anlaufstelle.settings.prod as p; "
        "print(json.dumps({'rp_id': p.OTP_WEBAUTHN_RP_ID, 'origins': p.OTP_WEBAUTHN_ALLOWED_ORIGINS}))"
    )

    def _resolve(self, env_overrides):
        result = _run_prod_import({**self._VALID_ENV, **env_overrides}, code=self._READBACK)
        assert result.returncode == 0, f"prod import failed: {result.stderr}"
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_rp_id_from_concrete_host(self):
        resolved = self._resolve({"ALLOWED_HOSTS": "app.example.org"})
        assert resolved["rp_id"] == "app.example.org"
        assert resolved["origins"] == ["https://app.example.org"]

    def test_rp_id_falls_back_to_wildcard_base_domain(self):
        """Reine Wildcard-Config: RP-ID = Basis-Domain (Dot entfernt), nicht leer."""
        resolved = self._resolve({"ALLOWED_HOSTS": ".example.org"})
        assert resolved["rp_id"] == "example.org"

    def test_explicit_env_rp_id_wins(self):
        resolved = self._resolve({"ALLOWED_HOSTS": "app.example.org", "OTP_WEBAUTHN_RP_ID": "example.org"})
        assert resolved["rp_id"] == "example.org"


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


class TestProdFileUploadTempDir:
    """Refs #1661 (I7 aus #1376): Uploads oberhalb von
    FILE_UPLOAD_MAX_MEMORY_SIZE puffert Django als Klartext-Tempdatei auf
    Platte, bevor sie verschluesselt in den File-Vault wandern
    (core/services/file_vault/storage.py). FILE_UPLOAD_TEMP_DIR muss aus der
    Umgebung durchgereicht werden, damit Betreiber diesen Puffer explizit auf
    ein tmpfs/verschluesseltes Volume legen koennen, statt sich auf Pythons
    impliziten tempfile.gettempdir()-Fallback zu verlassen."""

    _VALID_ENV = {
        "DJANGO_SECRET_KEY": "x" * 50,
        "ALLOWED_HOSTS": "example.org",
        "ENCRYPTION_KEY": "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZmE=",
        "DJANGO_AUDIT_HASH_KEY": "y" * 50,
    }

    _READBACK = (
        "import json, anlaufstelle.settings.prod as p; "
        "print(json.dumps({'temp_dir': getattr(p, 'FILE_UPLOAD_TEMP_DIR', None)}))"
    )

    def _resolved(self, env_overrides=None):
        result = _run_prod_import({**self._VALID_ENV, **(env_overrides or {})}, code=self._READBACK)
        assert result.returncode == 0, f"prod import failed: {result.stderr}"
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_unset_keeps_django_default(self):
        """Kein FILE_UPLOAD_TEMP_DIR gesetzt → Django faellt auf seinen
        eigenen Default (None → tempfile.gettempdir()) zurueck; prod.py darf
        das Setting dann nicht auf einen leeren String o.ae. erzwingen."""
        assert self._resolved()["temp_dir"] is None

    def test_env_var_is_passed_through(self):
        # Ein beliebiger Pfad genuegt hier - geprueft wird nur die
        # Durchreichung, nicht der konkrete Compose-Wert (der ist "/tmp",
        # siehe docker-compose.prod.yml, aber ein Literal-"/tmp" triggert
        # Ruffs S108-Regel fuer Test-Code).
        resolved = self._resolved({"FILE_UPLOAD_TEMP_DIR": "/mnt/tmpfs-uploads"})
        assert resolved["temp_dir"] == "/mnt/tmpfs-uploads"

    def test_blank_env_var_keeps_django_default(self):
        """Eine leer gesetzte Variable (z. B. `FILE_UPLOAD_TEMP_DIR=` in .env)
        darf nicht als expliziter Leerstring-Pfad durchgereicht werden."""
        assert self._resolved({"FILE_UPLOAD_TEMP_DIR": ""})["temp_dir"] is None
