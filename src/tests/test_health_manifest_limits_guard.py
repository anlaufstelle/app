"""Resource bounds for backup-manifest discovery and parsing."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from core.services.system import health
from tests.test_health_backup_source import _publish_complete_set

pytestmark = pytest.mark.django_db


def test_oversized_manifest_is_rejected_before_reading(tmp_path: Path):
    manifest = tmp_path / "oversized.manifest"
    manifest.write_bytes(b"key=" + b"x" * (1024 * 1024))

    assert health._parse_backup_manifest(manifest) is None


def test_manifest_candidate_limit_stops_further_validation(tmp_path: Path, monkeypatch):
    manifests = [tmp_path / f"backup-{index}.manifest" for index in range(3)]
    for manifest in manifests:
        manifest.write_text("backup_timestamp=2026-09-23_120000\n", encoding="utf-8")
    checked: list[Path] = []
    monkeypatch.setattr(health, "MAX_BACKUP_MANIFESTS", 2)
    monkeypatch.setattr(health, "_is_complete_backup_set", lambda path: checked.append(path) or False)

    assert health.last_backup_info(tmp_path) is None
    assert len(checked) == 2


def test_manifests_below_retention_directory_depth_are_not_scanned(tmp_path: Path, monkeypatch):
    deep_manifest = tmp_path / "daily" / "unexpected" / "deep.manifest"
    deep_manifest.parent.mkdir(parents=True)
    deep_manifest.write_text("backup_timestamp=2026-09-23_120000\n", encoding="utf-8")
    checked: list[Path] = []
    monkeypatch.setattr(health, "_is_complete_backup_set", lambda path: checked.append(path) or False)

    assert health.last_backup_info(tmp_path) is None
    assert deep_manifest not in checked


def test_normal_manifest_still_parses(tmp_path: Path):
    manifest = tmp_path / "normal.manifest"
    manifest.write_text("backup_timestamp=2026-09-23_120000\ndb_sha256=abc\n", encoding="utf-8")

    assert health._parse_backup_manifest(manifest) == {
        "backup_timestamp": "2026-09-23_120000",
        "db_sha256": "abc",
    }


def test_candidate_limit_keeps_newest_set(tmp_path: Path, monkeypatch):
    start = date(2025, 1, 1)
    for offset in range(300):
        timestamp = (start + timedelta(days=offset)).strftime("%Y-%m-%d") + "_020000"
        _publish_complete_set(tmp_path, timestamp)
    newest = "2026-09-12_020000"
    _publish_complete_set(tmp_path, newest)

    original_glob = Path.glob
    monkeypatch.setattr(
        Path,
        "glob",
        lambda self, pattern: iter(sorted(original_glob(self, pattern))),
    )

    info = health.last_backup_info(tmp_path)

    assert info is not None
    assert newest in info["path"]
