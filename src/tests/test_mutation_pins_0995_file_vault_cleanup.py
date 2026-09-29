"""Mutationspins fuer den budgetierten Medienbaum-Cleanup."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core.services.file_vault import cleanup as cleanup_module


def test_service_verwirft_nichtpositives_budget_vor_db_und_baum(monkeypatch, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)

    def unexpected_reference_query():
        raise AssertionError("Referenzmenge darf bei ungueltigem Budget nicht geladen werden")

    monkeypatch.setattr(cleanup_module.Attachment.objects, "values_list", unexpected_reference_query)

    with pytest.raises(ValueError, match="mindestens 1"):
        cleanup_module.cleanup_orphan_storage_files(budget=0)


def test_zustandsdatei_wird_ueber_atomaren_replace_aktiviert(monkeypatch, tmp_path):
    state_path = tmp_path / ".cleanup-orphan-storage-files-state.json"
    original_replace = os.replace
    calls: list[tuple[Path, Path]] = []

    def record_replace(source, target):
        calls.append((Path(source), Path(target)))
        original_replace(source, target)

    monkeypatch.setattr(cleanup_module.os, "replace", record_replace)

    cleanup_module._write_cleanup_cursor(state_path, "facility/a.enc")

    assert len(calls) == 1
    source, target = calls[0]
    assert source.parent == tmp_path
    assert source.name.endswith(".tmp")
    assert target == state_path
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"after": "facility/a.enc"}
    assert not source.exists()


def test_traversierung_ist_sortiert_und_folgt_keinem_verzeichnis_symlink(tmp_path):
    media_root = tmp_path / "media"
    outside = tmp_path / "outside"
    (media_root / "b").mkdir(parents=True)
    (media_root / "a").mkdir()
    outside.mkdir()
    (media_root / "b" / "second.enc").write_bytes(b"b")
    (media_root / "a" / "first.enc").write_bytes(b"a")
    (outside / "outside.enc").write_bytes(b"outside")
    (media_root / "linked").symlink_to(outside, target_is_directory=True)

    paths = [relative for relative, _path in cleanup_module._iter_encrypted_files(media_root)]

    assert paths == ["a/first.enc", "b/second.enc"]
