"""The release checklist must keep historical checks out of the active delta."""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture
ROOT = Path(__file__).resolve().parents[2]


def run_guard(root):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_hand_check_archive.py"), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def prepare(root):
    dev = root / "docs/dev"
    dev.mkdir(parents=True)
    (dev / "hand-checks.md").write_text("## [Unreleased]\n\n- [#2](https://example.org/2) — New.\n")
    (dev / "hand-checks-archive-v0.99.5.md").write_text("## Archive\n- [#1](https://example.org/1) — Old.\n")
    (dev / "release-checklist.md").write_text("- [ ] `python scripts/check_hand_check_archive.py`\n")
    (root / "CHANGELOG.md").write_text("## [Unreleased]\n[#2](x)\n## [0.99.5]\n[#1](x)\n")
    return dev


def test_rejects_archived_check_in_unreleased(tmp_path):
    dev = prepare(tmp_path)
    with (dev / "hand-checks.md").open("a") as handle:
        handle.write("- [#1](https://example.org/1) — Old.\n")
    result = run_guard(tmp_path)
    assert result.returncode == 1
    assert "#1" in result.stdout


def test_rejects_newly_released_check_in_unreleased(tmp_path):
    prepare(tmp_path)
    (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n\n## [0.99.6]\n[#2](x)\n")
    result = run_guard(tmp_path)
    assert result.returncode == 1
    assert "#2" in result.stdout


def test_accepts_current_delta(tmp_path):
    prepare(tmp_path)
    assert run_guard(tmp_path).returncode == 0


def test_requires_checklist_step(tmp_path):
    dev = prepare(tmp_path)
    (dev / "release-checklist.md").write_text("# Checklist\n")
    result = run_guard(tmp_path)
    assert result.returncode == 1
    assert "checklist" in result.stdout


def test_repository_hand_check_delta_is_current():
    if not (ROOT / "docs" / "dev").exists():
        pytest.skip("Dev documentation is absent from public snapshots")
    result = run_guard(ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
