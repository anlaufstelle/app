"""Future-dated backup names remain selectable despite host clock drift."""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path

import pytest

from tests.test_ops_backup_hwm import AUDIT_KEY, _restore_sandbox
from tests.test_ops_backup_verify_set_binding import _sandbox as _backup_sandbox
from tests.test_ops_backup_verify_set_binding import _verify

pytestmark = pytest.mark.architecture


def _put_clock_before_fixture_timestamps(tmp_path: Path, monkeypatch) -> None:
    bindir = tmp_path / "clock-bin"
    bindir.mkdir()
    date = bindir / "date"
    date.write_text(
        """#!/usr/bin/env bash
if [[ "$*" == *"+%Y-%m-%d_%H%M%S"* ]]; then
    printf '2026-01-01_000000\n'
else
    /bin/date "$@"
fi
""",
        encoding="utf-8",
    )
    date.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")


def test_backup_verify_accepts_future_named_set_with_warning(tmp_path: Path, monkeypatch):
    _put_clock_before_fixture_timestamps(tmp_path, monkeypatch)
    root, bindir, _ = _backup_sandbox(tmp_path)

    result = _verify(tmp_path, root, bindir)

    assert result.returncode == 0, result.stderr
    assert "Zeitstempel in der Zukunft" in result.stderr
    assert "wird trotzdem geprueft" in result.stderr


def test_restore_drill_accepts_future_named_set_with_warning(tmp_path: Path, monkeypatch):
    _put_clock_before_fixture_timestamps(tmp_path, monkeypatch)
    signature = hmac.new(AUDIT_KEY.encode(), b"{}", hashlib.sha256).hexdigest()

    _, _, result = _restore_sandbox(
        tmp_path,
        hwm_payload=f'{{"version":1,"chains":{{}},"sig":"{signature}"}}',
    )

    assert result.returncode == 0, result.stderr
    assert "Zeitstempel in der Zukunft" in result.stderr
    assert "wird trotzdem geprueft" in result.stderr
