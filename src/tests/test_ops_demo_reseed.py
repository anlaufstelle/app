"""Regression tests for observable demo reset failures."""

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "dev-ops/deploy/demo-reseed.sh"


def _run_reseed(tmp_path: Path, docker_exit: int) -> subprocess.CompletedProcess[str]:
    if not SCRIPT.exists():
        pytest.skip("dev-ops/ nicht im öffentlichen Release-Baum")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(f"#!/usr/bin/env bash\nexit {docker_exit}\n", encoding="utf-8")
    (bin_dir / "logger").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (bin_dir / "docker").chmod(0o755)
    (bin_dir / "logger").chmod(0o755)
    (tmp_path / ("." + "env.demo")).write_text("POSTGRES_DB=test\n", encoding="utf-8")
    environment = os.environ.copy()
    environment.update({"PATH": f"{bin_dir}:{environment['PATH']}", "ANLAUFSTELLE_DEMO_ROOT": str(tmp_path)})
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )


def test_failed_reset_is_visible_and_nonzero(tmp_path: Path) -> None:
    result = _run_reseed(tmp_path, 1)

    assert result.returncode != 0
    assert "FEHLGESCHLAGEN" in result.stderr
    assert "Exit 1" in result.stderr
    assert "Seed-Reset abgeschlossen" not in result.stdout


def test_successful_reset_has_only_success_message(tmp_path: Path) -> None:
    result = _run_reseed(tmp_path, 0)

    assert result.returncode == 0
    assert "Seed-Reset abgeschlossen" in result.stdout
    assert "FEHLGESCHLAGEN" not in result.stderr
