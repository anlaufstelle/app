"""Operator notice for the intentionally unbound restore precheck."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_ops_restore_media_order import _run_hwm_restore, _signed_hwm_payload

pytestmark = pytest.mark.architecture


def test_bound_anchor_restore_names_unbound_precheck(tmp_path: Path):
    result, _, _ = _run_hwm_restore(tmp_path, _signed_hwm_payload(tmp_path))

    assert result.returncode == 0, result.stderr
    assert "Vorpruefung lief ohne Audit-Anker" in result.stderr
    assert "vollstaendige Satzpruefung erfolgt mit dem gebundenen Anker" in result.stderr
