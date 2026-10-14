"""The prod drill must address the same Compose file set as restore.sh and the runbook."""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

import pytest

from tests.test_ops_backup_hwm import AUDIT_KEY, _restore_sandbox

pytestmark = pytest.mark.architecture

FILE_SET = "-f docker-compose.prod.yml -f compose.legal.yml -f compose.override.yml"


def _valid_hwm_payload() -> str:
    signature = hmac.new(AUDIT_KEY.encode(), b"{}", hashlib.sha256).hexdigest()
    return f'{{"version":1,"chains":{{}},"sig":"{signature}"}}'


def test_prod_drill_passes_legal_and_override_files_to_every_compose_call(tmp_path: Path) -> None:
    compose_dir = tmp_path / "restore-root"
    compose_dir.mkdir()
    (compose_dir / "compose.legal.yml").write_text("services: {}\n", encoding="utf-8")
    (compose_dir / "compose.override.yml").write_text("services: {}\n", encoding="utf-8")

    _, docker_log, result = _restore_sandbox(tmp_path, hwm_payload=_valid_hwm_payload())

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    calls = [line for line in docker_log.read_text(encoding="utf-8").splitlines() if line.startswith("compose ")]
    assert calls
    assert all(FILE_SET in line for line in calls), calls


def test_prod_drill_keeps_base_file_only_without_extra_files(tmp_path: Path) -> None:
    _, docker_log, result = _restore_sandbox(tmp_path, hwm_payload=_valid_hwm_payload())

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    calls = [line for line in docker_log.read_text(encoding="utf-8").splitlines() if line.startswith("compose ")]
    assert calls
    assert all("compose.legal.yml" not in line and "compose.override.yml" not in line for line in calls)
