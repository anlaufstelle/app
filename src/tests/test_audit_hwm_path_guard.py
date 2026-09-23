"""Path traversal guard for audit high-water-mark persistence."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

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


@pytest.mark.django_db
def test_verify_command_reports_invalid_hwm_path_without_value_error_traceback(settings, tmp_path: Path):
    settings.AUDIT_HWM_PATH = str(tmp_path / "working" / ".." / "outside" / "hwm.json")

    with pytest.raises(CommandError, match=r"AUDIT_HWM_PATH.*\.\."):
        call_command("verify_audit_chain")
