"""Unit-Tests fuer den System-Health-Service (Refs #871).

Die Funktionen sind defensiv geschrieben — sie sollen niemals den
Dashboard-Render kippen. Diese Tests stellen sicher, dass die jeweiligen
Fehlerpfade ``False`` / leere Defaults liefern und der Happy-Path die
erwarteten Schluesselsetzt.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from django.test import override_settings

import core.services.system.health as system_health


class TestCheckDatabase:
    """``check_database()`` — DB-Erreichbarkeit als Bool."""

    @pytest.mark.django_db
    def test_returns_true_when_db_reachable(self):
        """Bei einer gueltigen Test-DB darf die Pruefung True liefern."""
        assert system_health.check_database() is True

    def test_returns_false_when_ensure_connection_raises(self, monkeypatch):
        """Wenn ``connection.ensure_connection`` failt, faellt der
        Check defensiv auf False zurueck — kein Crash.

        Bewusst ein Stub fuer die MODUL-Referenz statt eines Patches auf dem
        ``connection``-Proxy: der Proxy delegiert setattr an die geteilte
        ``DatabaseWrapper``-Instanz, und monkeypatchs Undo schreibt den im
        geblockten Zustand gelesenen pytest-django-Blocker-Wrapper als
        Instanz-Attribut zurueck — das ueberschattet dauerhaft jedes
        klassenbasierte ``unblock()`` und kippt das Session-Teardown der
        ``rls_test_role``-Fixture (Refs #1647).
        """

        class _BrokenConnection:
            @staticmethod
            def ensure_connection():
                raise RuntimeError("simulated DB outage")

        monkeypatch.setattr(system_health, "connection", _BrokenConnection())
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
            "core.services.system.health.MigrationExecutor",
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

        monkeypatch.setattr("core.services.system.health.shutil.disk_usage", boom)
        result = system_health.disk_usage("/nope")
        assert result["total_gb"] is None
        assert result["used_gb"] is None
        assert result["free_gb"] is None
        assert result["percent_used"] is None


def _write_backup_set(directory: Path, timestamp: str, *, with_media: bool = True) -> Path:
    """Einen vollstaendigen, veroeffentlichten Backup-Satz anlegen (Refs #2291)."""
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / f"anlaufstelle_{timestamp}.manifest"
    lines = [f"table_{table}=1" for table in system_health.BACKUP_SET_TABLES]
    lines.append(f"backup_timestamp={timestamp}")
    lines.append(f"db_sha256={'a' * 64}")
    dump = directory / f"anlaufstelle_{timestamp}.sql.gz.enc"
    dump.write_bytes(b"dump")
    dump.with_name(f"{dump.name}.hmac").write_text("sig", encoding="utf-8")
    if with_media:
        lines.append(f"media_sha256={'b' * 64}")
        media = directory / f"anlaufstelle_{timestamp}_media.tar.gz.enc"
        media.write_bytes(b"media")
        media.with_name(f"{media.name}.hmac").write_text("sig", encoding="utf-8")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest.with_name(f"{manifest.name}.hmac").write_text("sig", encoding="utf-8")
    return manifest


def _age(path: Path, hours: float) -> None:
    stamp = (datetime.now(tz=UTC) - timedelta(hours=hours)).timestamp()
    os.utime(path, (stamp, stamp))


class TestLastBackupInfo:
    """``last_backup_info()`` — juengster vollstaendiger Satz + Stale-Heuristik."""

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

    def test_returns_youngest_complete_set(self, tmp_path):
        """Der juengste vollstaendige Satz wird gefunden, ``age_hours`` ist eine Float."""
        older = _write_backup_set(tmp_path / "daily", "2026-09-10_020000")
        _age(older, 48)
        newer = _write_backup_set(tmp_path / "daily", "2026-09-12_020000")

        result = system_health.last_backup_info(tmp_path)
        assert result is not None
        assert Path(result["path"]).name == newer.name
        assert isinstance(result["mtime"], datetime)
        assert result["age_hours"] >= 0
        # Frisches Backup ist nicht stale.
        assert result["is_stale"] is False

    def test_incomplete_newer_set_does_not_displace_the_last_complete_one(self, tmp_path):
        """Refs #2291: ein angefangener Satz ist kein Backup — auch nicht das juengste."""
        complete = _write_backup_set(tmp_path / "daily", "2026-09-10_020000")
        _age(complete, 5)
        broken = tmp_path / "daily" / "anlaufstelle_2026-09-12_020000.manifest"
        broken.write_text("", encoding="utf-8")

        result = system_health.last_backup_info(tmp_path)
        assert result is not None
        assert Path(result["path"]).name == complete.name

    @pytest.mark.parametrize(
        "damage",
        ["kein_sidecar", "fehlende_tabelle", "keine_satzkennung", "kurzer_hash", "kaputte_zeile"],
    )
    def test_damaged_manifest_is_no_complete_set(self, tmp_path, damage):
        """Jeder Vertragsbruch des Manifests schliesst den Satz aus (Refs #2291)."""
        manifest = _write_backup_set(tmp_path / "daily", "2026-09-12_020000", with_media=False)
        if damage == "kein_sidecar":
            manifest.with_name(f"{manifest.name}.hmac").unlink()
        else:
            lines = manifest.read_text(encoding="utf-8").splitlines()
            if damage == "fehlende_tabelle":
                lines = [line for line in lines if not line.startswith("table_core_client=")]
            elif damage == "keine_satzkennung":
                lines = [line for line in lines if not line.startswith("backup_timestamp=")]
            elif damage == "kurzer_hash":
                lines = [line if not line.startswith("db_sha256=") else "db_sha256=abc" for line in lines]
            else:
                lines.append("eine zeile ohne trenner")
            manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")

        assert system_health._is_complete_backup_set(manifest) is False
        assert system_health.last_backup_info(tmp_path) is None

    def test_missing_sidecar_of_a_referenced_artifact_breaks_the_set(self, tmp_path):
        manifest = _write_backup_set(tmp_path / "daily", "2026-09-12_020000")
        dump = manifest.with_name("anlaufstelle_2026-09-12_020000.sql.gz.enc")
        dump.with_name(f"{dump.name}.hmac").unlink()

        assert system_health._is_complete_backup_set(manifest) is False

    def test_bound_audit_anchor_must_exist(self, tmp_path):
        manifest = _write_backup_set(tmp_path / "daily", "2026-09-12_020000", with_media=False)
        manifest.write_text(
            manifest.read_text(encoding="utf-8") + f"hwm_sha256={'c' * 64}\n",
            encoding="utf-8",
        )

        assert system_health._is_complete_backup_set(manifest) is False

        (manifest.parent / "audit_hwm-2026-09-12_020000.json").write_text("{}", encoding="utf-8")
        assert system_health._is_complete_backup_set(manifest) is True

    def test_blank_lines_in_the_manifest_are_tolerated(self, tmp_path):
        manifest = _write_backup_set(tmp_path / "daily", "2026-09-12_020000", with_media=False)
        manifest.write_text("\n" + manifest.read_text(encoding="utf-8") + "\n", encoding="utf-8")

        assert system_health._is_complete_backup_set(manifest) is True

    def test_unreadable_manifest_is_no_complete_set(self, tmp_path, monkeypatch):
        manifest = _write_backup_set(tmp_path / "daily", "2026-09-12_020000", with_media=False)

        def boom(*args, **kwargs):
            raise OSError("unlesbar")

        monkeypatch.setattr(Path, "read_text", boom)
        assert system_health._is_complete_backup_set(manifest) is False

    def test_listing_failure_returns_none(self, tmp_path, monkeypatch):
        _write_backup_set(tmp_path / "daily", "2026-09-12_020000")

        def boom(*args, **kwargs):
            raise OSError("kein Zugriff")

        monkeypatch.setattr(Path, "rglob", boom)
        assert system_health.last_backup_info(tmp_path) is None

    def test_marks_as_stale_when_older_than_threshold(self, tmp_path):
        """Satz aelter als ``BACKUP_STALE_THRESHOLD_HOURS`` -> ``is_stale=True``."""
        ancient = _write_backup_set(tmp_path / "daily", "2026-08-01_020000")
        _age(ancient, system_health.BACKUP_STALE_THRESHOLD_HOURS + 5)

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

        monkeypatch.setattr("core.services.system.health.tomllib.load", boom)
        result = system_health.app_versions()
        assert result["app_version"] in (os.environ.get("APP_VERSION", "unknown"), "unknown")

    def test_missing_pyproject_is_not_logged_as_error(self, monkeypatch, caplog):
        """Refs #1504: eine fehlende ``pyproject.toml`` (Container-Deploys
        vor der pyproject-COPY, oder legitim andere Deploy-Formen) ist ein
        erwarteter Fallback-Pfad, kein Fehlerzustand -- darf im
        System-Health-Call nicht auf ERROR/``logger.exception`` loggen.
        Version faellt weiterhin auf den ENV-Wert zurueck."""

        class _RaisingPath:
            def __truediv__(self, _other):
                return self

            def open(self, *_a, **_kw):
                raise FileNotFoundError(2, "No such file or directory", "pyproject.toml")

        class _FakeBaseDirPath:
            def __init__(self, *_a, **_kw):
                pass

            @property
            def parent(self):
                return _RaisingPath()

        monkeypatch.setattr("core.services.system.health.Path", _FakeBaseDirPath)
        caplog.set_level(logging.DEBUG, logger="core.services.system.health")

        result = system_health.app_versions()

        assert result["app_version"] == os.environ.get("APP_VERSION", "unknown")
        assert not any(record.levelno >= logging.ERROR for record in caplog.records)

    def test_other_pyproject_read_errors_stay_at_error_level(self, monkeypatch, caplog):
        """Gegen-Check: Nicht-``FileNotFoundError``-Fehler (z.B. kaputtes
        TOML) bleiben weiterhin auf ``exception``/ERROR-Level -- nur der
        legitim fehlende Datei-Fall wird abgesenkt."""

        def boom(_path):
            raise ValueError("simulated corrupt pyproject.toml")

        monkeypatch.setattr("core.services.system.health.tomllib.load", boom)
        caplog.set_level(logging.DEBUG, logger="core.services.system.health")

        system_health.app_versions()

        assert any(record.levelno >= logging.ERROR for record in caplog.records)
