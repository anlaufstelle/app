"""Operator notice for the intentionally unbound restore precheck."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_ops_restore_media_order import _run_hwm_restore, _signed_hwm_payload

pytestmark = pytest.mark.architecture


def test_normal_bound_anchor_restore_has_no_unchecked_warning(tmp_path: Path):
    result, _, _ = _run_hwm_restore(tmp_path, _signed_hwm_payload(tmp_path))

    assert result.returncode == 0, result.stderr
    assert "Anker wurde nicht geprueft" not in result.stderr


def test_bound_anchor_without_configured_path_warns_that_anchor_was_not_checked(tmp_path: Path):
    result, _, _ = _run_hwm_restore(
        tmp_path,
        _signed_hwm_payload(tmp_path),
        hwm_configured=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Anker wurde nicht geprueft" in result.stderr
