"""Restore of a set with a bound audit anchor on a host without an anchor path.

Such a restore stops before anything is changed. Only the explicit
``--skip-audit-anchor`` switch lets it continue, and the output then says what
is not checked and not restored.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.test_ops_restore_media_order import _base_env, _make_sandbox, _run_hwm_restore, _signed_hwm_payload

pytestmark = pytest.mark.architecture


def test_normal_bound_anchor_restore_has_no_unchecked_warning(tmp_path: Path):
    result, _, _ = _run_hwm_restore(tmp_path, _signed_hwm_payload(tmp_path))

    assert result.returncode == 0, result.stderr
    assert "Vorpruefung lief ohne Audit-Anker" not in result.stderr
    assert "Anker wurde nicht geprueft" not in result.stderr


def test_bound_anchor_without_configured_path_aborts_before_any_change(tmp_path: Path):
    result, hwm_target, action_log = _run_hwm_restore(
        tmp_path,
        _signed_hwm_payload(tmp_path),
        hwm_configured=False,
    )

    assert result.returncode == 1, result.stderr
    assert "DJANGO_AUDIT_HWM_PATH" in result.stderr
    assert "--skip-audit-anchor" in result.stderr
    actions = action_log.read_text(encoding="utf-8") if action_log.exists() else ""
    assert " psql " not in actions
    assert " mv " not in actions
    assert " tar " not in actions
    assert hwm_target.read_bytes() == b"old-anchor"


def test_skip_audit_anchor_switch_restores_and_says_what_is_not_checked(tmp_path: Path):
    result, hwm_target, action_log = _run_hwm_restore(
        tmp_path,
        _signed_hwm_payload(tmp_path),
        hwm_configured=False,
        extra_args=("--skip-audit-anchor",),
    )

    assert result.returncode == 0, result.stderr
    assert "--skip-audit-anchor" in result.stderr
    assert "Anker wurde nicht geprueft" in result.stderr
    assert "NICHT eingespielt" in result.stderr
    assert " psql " in action_log.read_text(encoding="utf-8")
    assert hwm_target.read_bytes() == b"old-anchor"


def test_skip_audit_anchor_switch_is_listed_in_the_usage(tmp_path: Path):
    root, bindir, action_log, _, _ = _make_sandbox(tmp_path)

    result = subprocess.run(
        ["bash", str(root / "scripts" / "ops" / "restore.sh")],
        capture_output=True,
        text=True,
        env=_base_env(root, bindir, action_log),
        cwd=root,
        timeout=30,
        stdin=subprocess.DEVNULL,
    )

    assert result.returncode == 1
    assert "[--skip-audit-anchor]" in result.stdout
