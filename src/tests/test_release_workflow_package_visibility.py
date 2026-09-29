import os
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.architecture

WORKFLOW_PATH = Path(".github/workflows/release.yml")


def _build_job() -> dict[str, object]:
    if not WORKFLOW_PATH.exists():
        pytest.skip(".github/workflows/release.yml is excluded from this release tree")
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return workflow["jobs"]["build-and-push"]


def _visibility_step(job: dict[str, object] | None = None) -> dict[str, object]:
    steps = (job or _build_job())["steps"]
    return next(step for step in steps if step.get("name") == "Ensure package visibility is public")


def _run_visibility_script(tmp_path: Path, *, token: str, gh_exit: int) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh_stub = bin_dir / "gh"
    gh_stub.write_text(f"#!/bin/sh\nexit {gh_exit}\n", encoding="utf-8")
    gh_stub.chmod(0o755)
    environment = os.environ.copy()
    environment.update(
        {
            "GH_TOKEN": token,
            "REPOSITORY": "anlaufstelle/app",
            "PATH": f"{bin_dir}{os.pathsep}{environment['PATH']}",
        }
    )
    return subprocess.run(
        ["bash", "-c", str(_visibility_step()["run"])],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_visibility_step_is_last_and_failures_are_not_ignored() -> None:
    job = _build_job()
    step = _visibility_step(job)

    assert job["steps"][-1] is step
    assert step.get("continue-on-error", False) is False


def test_missing_visibility_token_fails_with_manual_recovery_path(tmp_path: Path) -> None:
    result = _run_visibility_script(tmp_path, token="", gh_exit=0)

    assert result.returncode != 0
    assert "::error::" in result.stdout
    assert "GHCR_VISIBILITY_TOKEN" in result.stdout
    assert "package settings" in result.stdout.lower()


def test_failed_visibility_api_call_fails_the_step(tmp_path: Path) -> None:
    result = _run_visibility_script(tmp_path, token="available", gh_exit=1)

    assert result.returncode != 0


def test_successful_visibility_api_call_succeeds(tmp_path: Path) -> None:
    result = _run_visibility_script(tmp_path, token="available", gh_exit=0)

    assert result.returncode == 0, result.stderr
