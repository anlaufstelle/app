"""Regression guards for the demo deployment environment handling."""

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

SCRIPT = Path("dev-ops/deploy/deploy-demo.sh")


def _remote_block() -> str:
    if not SCRIPT.exists():
        pytest.skip("dev-ops/deploy/deploy-demo.sh is not in the public snapshot (Refs #2408)")
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


ENV_NAME = ".env" + ".demo"
SOURCING_ENV_DEMO = re.compile(r"(?:^|[;&|(\s])(?:source|\.)\s+\S*\.env\.demo\b")


def env_demo_sourcing_lines(paths: list[Path]) -> list[str]:
    """Return ``file:line`` for every non-comment line that sources ``.env.demo`` into a shell."""
    hits: list[str] = []
    for path in paths:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            if SOURCING_ENV_DEMO.search(line):
                hits.append(f"{path}:{number}")
    return hits


def _tracked_script_paths() -> list[Path]:
    try:
        listed = subprocess.run(
            ["git", "ls-files", "Makefile", "*.sh"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("not a git checkout (release snapshot)")
    return [Path(name) for name in listed]


def test_env_demo_sourcing_scanner_flags_sourcing_forms(tmp_path: Path) -> None:
    probe = tmp_path / "probe.sh"
    probe.write_text(
        f"# . ./{ENV_NAME} is only mentioned here\n"
        f"set -a && . ./{ENV_NAME} && set +a\n"
        f"source /opt/anlaufstelle/{ENV_NAME}\n"
        f"docker compose --env-file {ENV_NAME} ps\n",
        encoding="utf-8",
    )
    assert env_demo_sourcing_lines([probe]) == [f"{probe}:2", f"{probe}:3"]


def test_env_demo_is_never_sourced_into_a_shell() -> None:
    # Compose reads the file itself via --env-file and the service env_file; sourcing it
    # would let a stale or unquoted value override or break that interpolation.
    paths = _tracked_script_paths()
    assert paths, "git ls-files returned no scripts"
    assert env_demo_sourcing_lines(paths) == []
