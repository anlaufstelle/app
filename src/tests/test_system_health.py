"""Unit-Tests fuer den System-Health-Service (Refs #871).

Die Funktionen sind defensiv geschrieben — sie sollen niemals den
Dashboard-Render kippen. Diese Tests stellen sicher, dass die jeweiligen
Fehlerpfade ``False`` / leere Defaults liefern und der Happy-Path die
erwarteten Schluesselsetzt.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from django.test import override_settings

from core.services import system_health


class TestCheckDatabase:
    """``check_database()`` — DB-Erreichbarkeit als Bool."""

    @pytest.mark.django_db
    def test_returns_true_when_db_reachable(self):
        """Bei einer gueltigen Test-DB darf die Pruefung True liefern."""
        assert system_health.check_database() is True

    def test_returns_false_when_ensure_connection_raises(self, monkeypatch):
        """Wenn ``connection.ensure_connection`` failt, faellt der
        Check defensiv auf False zurueck — kein Crash."""

        def boom():
            raise RuntimeError("simulated DB outage")

        monkeypatch.setattr(
            "core.services.system_health.connection.ensure_connection",
            boom,
        )
        assert system_health.check_database() is False


class TestPendingMigrations:
    """``pending_migrations()`` — Liste der ungeladenen Migrationen."""

    @pytest.mark.django_db
    def test_returns_empty_list_when_db_in_sync(self):
        """Nach erfolgter ``migrate`` (pytest-django setup) gibt es keine
        ausstehenden Migrationen mehr.
        """
        assert system_health.pending_migrations() == []

    def test_swallows_exception(self, monkeypatch):
        """Wirft die Pruefung intern, gibt es trotzdem eine leere Liste."""

        class _BoomExecutor:
            def __init__(self, *args, **kwargs):
                raise RuntimeError("simulated migration loader failure")

        monkeypatch.setattr(
            "core.services.system_health.MigrationExecutor",
            _BoomExecutor,
        )
        assert system_health.pending_migrations() == []


class TestDiskUsage:
    """``disk_usage()`` — Total/Used/Free + Percent."""

    def test_returns_expected_keys_for_existing_path(self, tmp_path):
        result = system_health.disk_usage(tmp_path)
        for key in ("total_gb", "used_gb", "free_gb", "percent_used"):
            assert key in result, f"missing key {key!r} in disk_usage result"
        # Plausibilitaetscheck: Werte sind nicht None und sinnvoll.
        assert result["total_gb"] is not None
        assert result["used_gb"] is not None
        assert result["free_gb"] is not None
        assert 0 <= result["percent_used"] <= 100

    def test_returns_none_values_on_error(self, monkeypatch):
        """Wenn ``shutil.disk_usage`` failt (z.B. nicht-existenter Pfad),
        bekommt die Card None-Werte und kann das im Template anzeigen."""

        def boom(_path):
            raise OSError("simulated permission denied")

        monkeypatch.setattr("core.services.system_health.shutil.disk_usage", boom)
        result = system_health.disk_usage("/nope")
        assert result["total_gb"] is None
        assert result["used_gb"] is None
        assert result["free_gb"] is None
        assert result["percent_used"] is None


class TestLastBackupInfo:
    """``last_backup_info()`` — juengste Datei + Stale-Heuristik."""

    def test_returns_none_when_no_backup_dir(self, tmp_path):
        """Setting fehlt -> kein Backup-Info."""
        # Explizit ``backup_dir=None`` und kein BACKUP_DIR-Setting.
        with override_settings(BACKUP_DIR=None):
            assert system_health.last_backup_info() is None

    def test_returns_none_when_dir_empty(self, tmp_path):
        """Ein leeres Backup-Verzeichnis liefert None — der Sentinel
        differenziert "kein Backup gefunden" sauber von "Backup OK"."""
        result = system_health.last_backup_info(tmp_path)
        assert result is None

    def test_returns_youngest_file(self, tmp_path):
        """Jueneste Datei wird gefunden, ``age_hours`` ist eine Float."""
        old = tmp_path / "old.sql.gz.enc"
        old.write_bytes(b"x")
        # Mtime auf vor 2 Tagen setzen.
        old_mtime = (datetime.now(tz=UTC) - timedelta(hours=48)).timestamp()
        os.utime(old, (old_mtime, old_mtime))

        new = tmp_path / "new.sql.gz.enc"
        new.write_bytes(b"y")
        # frisches mtime — sollte jueneste Datei sein.

        result = system_health.last_backup_info(tmp_path)
        assert result is not None
        assert Path(result["path"]).name == "new.sql.gz.enc"
        assert isinstance(result["mtime"], datetime)
        assert result["age_hours"] >= 0
        # Frisches Backup ist nicht stale.
        assert result["is_stale"] is False

    def test_marks_as_stale_when_older_than_threshold(self, tmp_path):
        """Datei aelter als ``BACKUP_STALE_THRESHOLD_HOURS`` -> ``is_stale=True``."""
        ancient = tmp_path / "ancient.sql.gz.enc"
        ancient.write_bytes(b"z")
        ancient_mtime = (
            datetime.now(tz=UTC) - timedelta(hours=system_health.BACKUP_STALE_THRESHOLD_HOURS + 5)
        ).timestamp()
        os.utime(ancient, (ancient_mtime, ancient_mtime))

        result = system_health.last_backup_info(tmp_path)
        assert result is not None
        assert result["is_stale"] is True


class TestAppVersions:
    """``app_versions()`` — Python/Django/App."""

    def test_returns_all_three_versions(self):
        result = system_health.app_versions()
        assert "python_version" in result
        assert "django_version" in result
        assert "app_version" in result

    def test_python_version_is_dotted(self):
        result = system_health.app_versions()
        # ``3.13.0`` oder aehnlich.
        parts = result["python_version"].split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)

    def test_app_version_falls_back_when_pyproject_unreadable(self, monkeypatch):
        """Failt der ``tomllib.load``-Read, bekommen wir trotzdem einen
        nicht-leeren ``app_version``-Wert (env oder ``"unknown"``)."""

        def boom(_path):
            raise OSError("simulated read failure")

        monkeypatch.setattr("core.services.system_health.tomllib.load", boom)
        result = system_health.app_versions()
        assert result["app_version"] in (os.environ.get("APP_VERSION", "unknown"), "unknown")
