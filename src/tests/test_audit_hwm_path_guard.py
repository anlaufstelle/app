"""Path traversal guard for audit high-water-mark persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.services.audit.hwm import HighWaterMarkStore


def test_save_rejects_parent_directory_component_before_writing(tmp_path: Path, monkeypatch):
    working = tmp_path / "working"
    working.mkdir()
    monkeypatch.chdir(working)
    outside = tmp_path / "outside" / "hwm.json"
    store = HighWaterMarkStore(path="../outside/hwm.json")

    with pytest.raises(ValueError, match=r"\.\."):
        store.save()

    assert not outside.exists()


def test_save_still_accepts_normal_relative_path(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = HighWaterMarkStore(path="audit/hwm.json", chains={"system": {"count": 1}})

    store.save()

    assert (tmp_path / "audit" / "hwm.json").is_file()
