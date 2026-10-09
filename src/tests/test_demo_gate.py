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
    with pytest.raises(AssertionError, match="Demo server"):
        require_demo(lambda: False)


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
