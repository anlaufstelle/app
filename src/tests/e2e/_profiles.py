"""Explicit E2E profile prerequisites."""

import os
from collections.abc import Callable

import pytest


def require_demo(probe: Callable[[], bool]) -> None:
    """Skip outside the demo profile; fail if the expected demo is broken."""
    if os.environ.get("E2E_DEMO_MODE") != "1":
        pytest.skip("Requires demo profile (E2E_DEMO_MODE=1, E2E_SEED_SCALE=demo)")
    assert os.environ.get("E2E_SEED_SCALE") == "demo", "Demo profile requires E2E_SEED_SCALE=demo"
    assert probe(), "Demo server must return HTTP 200 for /demo/"
