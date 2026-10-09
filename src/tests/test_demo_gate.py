"""Verify the demo gate without starting a browser or server."""

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture
ROOT = Path(__file__).resolve().parents[2]


def test_demo_target_is_locked_and_selects_demo_specs():
    result = subprocess.run(["make", "-n", "test-e2e-demo"], cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    for expected in (
        "flock",
        "E2E_DEMO_MODE=1",
        "E2E_SEED_SCALE=demo",
        "E2E_PORT_BASE=8854",
        "test_demo_scenario_switch.py",
        "test_system_compliance_maintenance_results.py::TestDemoCards",
        "-rs",
    ):
        assert expected in result.stdout


def test_expected_demo_fails_for_broken_server(monkeypatch):
    from tests.e2e._profiles import require_demo

    monkeypatch.setenv("E2E_DEMO_MODE", "1")
    monkeypatch.setenv("E2E_SEED_SCALE", "demo")
    try:
        with pytest.raises(AssertionError, match="Demo server"):
            require_demo(lambda: False)
    except pytest.skip.Exception as error:
        pytest.fail(f"Expected demo was skipped: {error}")


def test_standard_profile_skips_without_probing(monkeypatch):
    from tests.e2e._profiles import require_demo

    monkeypatch.delenv("E2E_DEMO_MODE", raising=False)
    with pytest.raises(pytest.skip.Exception, match="demo profile"):
        require_demo(lambda: pytest.fail("Unexpected probe"))


def test_expected_demo_requires_demo_seed(monkeypatch):
    from tests.e2e._profiles import require_demo

    monkeypatch.setenv("E2E_DEMO_MODE", "1")
    monkeypatch.setenv("E2E_SEED_SCALE", "standard")
    with pytest.raises(AssertionError, match="E2E_SEED_SCALE"):
        require_demo(lambda: True)


def test_demo_dry_run_does_not_execute_flock(tmp_path):
    import os

    marker = tmp_path / "executed"
    executable = tmp_path / "flock"
    executable.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 97\n")
    executable.chmod(0o755)
    result = subprocess.run(
        ["make", "-n", "test-e2e-demo"],
        cwd=ROOT,
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker.exists(), "Dry run must not execute the shared lock"


@pytest.mark.parametrize("port", ["8844", "8854"])
def test_e2e_origins_follow_server_port(port):
    import os
    import sys

    env = {**os.environ, "E2E_PORT_BASE": port, "DJANGO_SETTINGS_MODULE": "anlaufstelle.settings.e2e"}
    env.pop("EXTERNAL_ORIGIN", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from django.conf import settings; "
            f"assert settings.EXTERNAL_ORIGIN == 'http://localhost:{port}'; "
            f"assert 'http://localhost:{port}' in settings.OTP_WEBAUTHN_ALLOWED_ORIGINS",
        ],
        cwd=ROOT / "src",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_release_checklist_requires_demo_before_release_and_in_evidence():
    path = ROOT / "docs/dev/release-checklist.md"
    if not path.is_file():
        pytest.skip("Dev documentation is absent from public snapshots")
    checklist = path.read_text()
    section = checklist.split("## 1.", 1)[1].split("## 2.", 1)[0]
    evidence = next(line for line in checklist.splitlines() if line.startswith("**Nachweise aus dem Pre-Release"))
    assert "make test-e2e-demo" in section
    assert "make test-e2e-demo" in evidence
