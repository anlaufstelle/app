"""Tests für den Health-Endpoint."""

from unittest.mock import patch

import pytest
from django.test import Client


@pytest.mark.django_db
class TestHealthEndpoint:
    """Health-Endpoint: 200, JSON-Format, kein Auth nötig."""

    def test_returns_200_and_json(self):
        client = Client()
        response = client.get("/health/")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["database"] == "connected"
        assert data["version"] == "dev"

    def test_no_auth_required(self):
        client = Client()
        response = client.get("/health/")
        assert response.status_code == 200

    def test_content_type_is_json(self):
        client = Client()
        response = client.get("/health/")
        assert response["Content-Type"] == "application/json"

    def test_virus_scanner_disabled_by_default(self):
        """In Tests ist CLAMAV_ENABLED=False — der Scanner gilt als ``disabled``."""
        client = Client()
        response = client.get("/health/")
        data = response.json()
        assert data["virus_scanner"] == "disabled"

    def test_virus_scanner_connected_when_reachable(self, settings):
        settings.CLAMAV_ENABLED = True
        with patch("core.views.health.clamav_ping", return_value=True):
            response = Client().get("/health/")
        data = response.json()
        assert data["virus_scanner"] == "connected"
        assert data["status"] == "ok"

    def test_virus_scanner_unavailable_degrades_status(self, settings):
        settings.CLAMAV_ENABLED = True
        with patch("core.views.health.clamav_ping", return_value=False):
            response = Client().get("/health/")
        data = response.json()
        assert data["virus_scanner"] == "unavailable"
        assert data["status"] == "degraded"

    def test_clamav_alias_disabled(self):
        """Refs #798 (C-30): ``clamav``-Alias spiegelt ``virus_scanner`` —
        die Doku (release-checklist.md / coolify-deployment.md) nutzt
        ``jq '.clamav'`` und erwartet ``ok``/``error``/``disabled``."""
        response = Client().get("/health/")
        data = response.json()
        assert data["clamav"] == "disabled"

    def test_clamav_alias_ok_when_connected(self, settings):
        settings.CLAMAV_ENABLED = True
        with patch("core.views.health.clamav_ping", return_value=True):
            response = Client().get("/health/")
        assert response.json()["clamav"] == "ok"

    def test_clamav_alias_error_when_unavailable(self, settings):
        settings.CLAMAV_ENABLED = True
        with patch("core.views.health.clamav_ping", return_value=False):
            response = Client().get("/health/")
        assert response.json()["clamav"] == "error"


@pytest.mark.django_db
class TestHealthExtendedComponents:
    """Refs #796 (C-28): SMTP, Encryption-Key, Backup-Alter, Disk-Frei."""

    def test_encryption_key_ok_in_test_env(self):
        data = Client().get("/health/").json()
        assert data["encryption_key"] == "ok"

    def test_encryption_key_error_critical(self):
        with patch("core.services.encryption.encrypt_field", side_effect=RuntimeError("no key")):
            response = Client().get("/health/")
        data = response.json()
        assert data["encryption_key"] == "error"
        assert data["status"] == "error"
        assert response.status_code == 503

    def test_smtp_disabled_for_locmem_backend(self, settings):
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        data = Client().get("/health/").json()
        assert data["smtp"]["status"] == "disabled"

    def test_smtp_unreachable_degrades(self, settings):
        settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
        settings.EMAIL_HOST = "smtp.invalid.example"
        settings.EMAIL_PORT = 25
        # socket.create_connection wird ausgehebelt, damit der Test offline laeuft.
        with patch("socket.create_connection", side_effect=OSError("offline")):
            data = Client().get("/health/").json()
        assert data["smtp"]["status"] == "unreachable"
        assert data["status"] == "degraded"

    def test_backup_age_field_populated_when_files_exist(self, tmp_path, settings):
        settings.BACKUP_DIR = tmp_path
        (tmp_path / "anlaufstelle_2026-01-01.sql.gz.enc").write_bytes(b"x")
        data = Client().get("/health/").json()
        assert data["last_backup_age_hours"] is not None

    def test_backup_age_warns_above_48h(self, tmp_path, settings):
        import os as os_mod
        from datetime import datetime, timedelta

        settings.BACKUP_DIR = tmp_path
        old_file = tmp_path / "anlaufstelle_2026-01-01.sql.gz.enc"
        old_file.write_bytes(b"x")
        ancient = (datetime.now() - timedelta(hours=72)).timestamp()
        os_mod.utime(old_file, (ancient, ancient))

        data = Client().get("/health/").json()
        assert data["last_backup_age_hours"] is not None
        assert data["last_backup_age_hours"] > 48
        assert data["status"] == "degraded"

    def test_disk_free_pct_field_populated(self, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        data = Client().get("/health/").json()
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
