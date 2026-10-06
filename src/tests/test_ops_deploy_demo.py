"""Regression guards for the demo deployment environment handling."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

SCRIPT = Path("dev-ops/deploy/deploy-demo.sh")


def _remote_block() -> str:
    if not SCRIPT.exists():
        pytest.skip("dev-ops/deploy/deploy-demo.sh nicht im Public-Snapshot (Refs #2408)")
    text = SCRIPT.read_text(encoding="utf-8")
    start = text.index("bash -se <<'EOF'\n") + len("bash -se <<'EOF'\n")
    end = text.index("\nEOF\n", start)
    return text[start:end]


def test_demo_deploy_does_not_export_stale_env_values() -> None:
    block = _remote_block()
    stale_source = "set -a; . ./.env" + ".demo; set +a"

    assert stale_source not in block
    assert 'export SOURCE_CODE_VERSION="$APP_VERSION"' in block


def test_demo_deploy_checks_container_source_code_version_after_recreate() -> None:
    block = _remote_block()

    assert "docker inspect anlaufstelle-web-1" in block
    assert "SOURCE_CODE_VERSION" in block
    assert "ACTUAL_SOURCE_CODE_VERSION" in block
    assert '"$ACTUAL_SOURCE_CODE_VERSION" = "$EXPECTED_SOURCE_CODE_VERSION"' in block
