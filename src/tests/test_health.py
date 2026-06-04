"""Tests für den Health-Endpoint.

A7.1/A7.2 (Refs #1024): ``/health/`` liefert anonymen Callern nur einen
schlanken Liveness-Payload (Status-Indikatoren, keine Recon-Details). Die
Detailfelder ``version``, ``smtp``, ``last_backup_age_hours`` und
``disk_free_pct`` gibt es nur für interne/Token-Caller (Header
``X-Health-Token`` == ``HEALTH_DETAIL_TOKEN``) oder authentifizierte Sessions.
"""

from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import Client

DETAIL_TOKEN = "test-health-detail-token"  # noqa: S105 — Test-Fixture, kein echtes Secret


@pytest.fixture(autouse=True)
def _clear_health_cache():
    """A7.2: die Detail-Checks (smtp/backup/disk) sind gecacht — pro Test leeren,
    damit Mocks nicht über Testgrenzen hinweg gecachte Werte sehen."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def detail_token(settings):
    """Setzt das Detail-Token und gibt es zurück (für Detail-Requests)."""
    settings.HEALTH_DETAIL_TOKEN = DETAIL_TOKEN
    return DETAIL_TOKEN


def _detail_get(**extra):
    """GET /health/ mit gültigem Detail-Token -> voller Payload."""
    return Client().get("/health/", HTTP_X_HEALTH_TOKEN=DETAIL_TOKEN, **extra)


@pytest.mark.django_db
class TestHealthLiveness:
    """Anonyme Caller: schlanke Liveness, keine Recon-Details (A7.1)."""

    def test_returns_200_and_json(self):
        response = Client().get("/health/")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["database"] == "connected"

    def test_no_auth_required(self):
        assert Client().get("/health/").status_code == 200

    def test_content_type_is_json(self):
        assert Client().get("/health/")["Content-Type"] == "application/json"

    def test_anonymous_payload_omits_recon_detail_fields(self):
        """version, smtp, last_backup_age_hours, disk_free_pct nur intern/Token."""
        data = Client().get("/health/").json()
        for field in ("version", "smtp", "last_backup_age_hours", "disk_free_pct"):
            assert field not in data, f"{field} darf nicht an anonyme Caller geliefert werden (A7.1)"

    def test_liveness_keeps_health_indicators(self):
        """status/database/virus_scanner/clamav/encryption_key bleiben (Monitoring)."""
        data = Client().get("/health/").json()
        for field in ("status", "database", "virus_scanner", "clamav", "encryption_key"):
            assert field in data

    def test_virus_scanner_disabled_by_default(self):
        """In Tests ist CLAMAV_ENABLED=False — der Scanner gilt als ``disabled``."""
        assert Client().get("/health/").json()["virus_scanner"] == "disabled"

    def test_virus_scanner_connected_when_reachable(self, settings):
        settings.CLAMAV_ENABLED = True
        with patch("core.views.health.clamav_ping", return_value=True):
            data = Client().get("/health/").json()
        assert data["virus_scanner"] == "connected"
        assert data["status"] == "ok"

    def test_virus_scanner_unavailable_degrades_status(self, settings):
        settings.CLAMAV_ENABLED = True
        with patch("core.views.health.clamav_ping", return_value=False):
            data = Client().get("/health/").json()
        assert data["virus_scanner"] == "unavailable"
        assert data["status"] == "degraded"

    def test_clamav_alias_disabled(self):
        """Refs #798 (C-30): ``clamav``-Alias bleibt im Liveness-Payload —
        die Doku nutzt ``jq '.clamav'`` und erwartet ``ok``/``error``/``disabled``."""
        assert Client().get("/health/").json()["clamav"] == "disabled"

    def test_clamav_alias_ok_when_connected(self, settings):
        settings.CLAMAV_ENABLED = True
        with patch("core.views.health.clamav_ping", return_value=True):
            assert Client().get("/health/").json()["clamav"] == "ok"

    def test_clamav_alias_error_when_unavailable(self, settings):
        settings.CLAMAV_ENABLED = True
        with patch("core.views.health.clamav_ping", return_value=False):
            assert Client().get("/health/").json()["clamav"] == "error"

    def test_encryption_key_ok_in_test_env(self):
        assert Client().get("/health/").json()["encryption_key"] == "ok"

    def test_encryption_key_error_is_critical(self):
        """Encryption-Key bleibt ein kritischer Liveness-Check (503 für alle)."""
        with patch("core.services.file_vault.encrypt_field", side_effect=RuntimeError("no key")):
            response = Client().get("/health/")
        data = response.json()
        assert data["encryption_key"] == "error"
        assert data["status"] == "error"
        assert response.status_code == 503


@pytest.mark.django_db
class TestHealthDetail:
    """Interne/Token-Caller: voller Payload inkl. Recon-Detailfelder (A7.1)."""

    def test_detail_token_unlocks_version(self, detail_token):
        assert _detail_get().json()["version"] == "dev"

    def test_wrong_token_stays_lean(self, detail_token):
        data = Client().get("/health/", HTTP_X_HEALTH_TOKEN="falsch").json()
        assert "version" not in data
        assert "smtp" not in data

    def test_authenticated_session_gets_detail(self, settings, admin_user):
        client = Client()
        client.force_login(admin_user)
        data = client.get("/health/").json()
        assert "version" in data
        assert "smtp" in data

    def test_smtp_disabled_for_locmem_backend(self, detail_token, settings):
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        assert _detail_get().json()["smtp"]["status"] == "disabled"

    def test_smtp_unreachable_degrades(self, detail_token, settings):
        settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
        settings.EMAIL_HOST = "smtp.invalid.example"
        settings.EMAIL_PORT = 25
        with patch("socket.create_connection", side_effect=OSError("offline")):
            data = _detail_get().json()
        assert data["smtp"]["status"] == "unreachable"
        assert data["status"] == "degraded"

    def test_backup_age_field_populated_when_files_exist(self, detail_token, tmp_path, settings):
        settings.BACKUP_DIR = tmp_path
        (tmp_path / "anlaufstelle_2026-01-01.sql.gz.enc").write_bytes(b"x")
        assert _detail_get().json()["last_backup_age_hours"] is not None

    def test_backup_age_warns_above_48h(self, detail_token, tmp_path, settings):
        import os as os_mod
        from datetime import datetime, timedelta

        settings.BACKUP_DIR = tmp_path
        old_file = tmp_path / "anlaufstelle_2026-01-01.sql.gz.enc"
        old_file.write_bytes(b"x")
        ancient = (datetime.now() - timedelta(hours=72)).timestamp()
        os_mod.utime(old_file, (ancient, ancient))

        data = _detail_get().json()
        assert data["last_backup_age_hours"] is not None
        assert data["last_backup_age_hours"] > 48
        assert data["status"] == "degraded"

    def test_disk_free_pct_field_populated(self, detail_token, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        data = _detail_get().json()
        assert data["disk_free_pct"] is not None
        assert 0 <= data["disk_free_pct"] <= 100


# Refs #835 (C-68): SOURCE_CODE_URL ist im Footer ueber den Context-Processor.
class TestAGPLFooter:
    def test_default_source_code_url_in_footer(self, client, db):
        response = client.get("/login/")
        assert b"https://github.com/anlaufstelle/app" in response.content

    def test_custom_source_code_url_renders(self, client, db, settings):
        settings.SOURCE_CODE_URL = "https://git.example.org/fork/anlaufstelle"
        response = client.get("/login/")
        assert b"https://git.example.org/fork/anlaufstelle" in response.content
        assert b"github.com/anlaufstelle" not in response.content

    def test_source_code_version_truncated(self, client, db, settings):
        settings.SOURCE_CODE_VERSION = "abcdef1234567890"
        response = client.get("/login/")
        # truncatechars:8 -> "abcdef1…" (7 Zeichen + Ellipsis)
        assert b"abcdef1" in response.content
