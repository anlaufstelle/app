"""Copied operations commands preserve explicit Compose file selection (Refs #2719)."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS = (
    "docs/ops-runbook.md",
    "docs/en/ops-runbook.md",
    "docs/admin-guide.md",
    "docs/en/admin-guide.md",
)
# Extract only the file-selection prelude, never execute maintenance commands.
_SELECTION = re.compile(
    r"^compose_files=\(-f docker-compose.prod.yml\)\n"
    r"(?:\[\[ [^\n]+\n){2}"
    r"(?:#[^\n]*\n)*"
    r"(?:if [^\n]+\n(?:[^\n]*\n)*?fi\n)?",
    re.MULTILINE,
)
_OPT_IN = "docker-compose.prod.yml:compose.legal.yml:compose.override.yml:docker-compose.breach-webhook.yml"


@pytest.mark.parametrize("document", DOCS)
@pytest.mark.parametrize(
    ("environment_list", "dotenv", "optional_files", "expected"),
    [
        pytest.param(_OPT_IN, "", True, [], id="environment-opt-in"),
        pytest.param(None, f"COMPOSE_FILE={_OPT_IN}\n", True, [], id="dotenv-opt-in"),
        pytest.param(None, f'  export COMPOSE_FILE = "{_OPT_IN}"\n', True, [], id="exported-dotenv-opt-in"),
        pytest.param(None, "", False, ["-f", "docker-compose.prod.yml"], id="default-base"),
        pytest.param(
            None,
            f"# COMPOSE_FILE={_OPT_IN}\n",
            True,
            ["-f", "docker-compose.prod.yml", "-f", "compose.legal.yml", "-f", "compose.override.yml"],
            id="comment-is-not-opt-in",
        ),
        pytest.param(
            "",
            "",
            True,
            ["-f", "docker-compose.prod.yml", "-f", "compose.legal.yml", "-f", "compose.override.yml"],
            id="empty-environment-is-not-opt-in",
        ),
    ],
)
def test_every_copied_file_selection_preserves_opt_in(
    tmp_path: Path,
    document: str,
    environment_list: str | None,
    dotenv: str,
    optional_files: bool,
    expected: list[str],
) -> None:
    source = (REPO_ROOT / document).read_text(encoding="utf-8")
    selections = list(_SELECTION.finditer(source))
    assert selections, f"{document}: no Compose file selections found"
    assert len(selections) == source.count("compose_files=(-f docker-compose.prod.yml)"), (
        f"{document}: unrecognized Compose file selection"
    )
    (tmp_path / ".env").write_text(dotenv, encoding="utf-8")
    # Having the overlay on disk must never enable it without explicit opt-in.
    (tmp_path / "docker-compose.breach-webhook.yml").touch()
    if optional_files:
        for name in ("compose.legal.yml", "compose.override.yml"):
            (tmp_path / name).touch()
    environment = dict(os.environ)
    environment.pop("COMPOSE_FILE", None)
    environment.pop("BASH_ENV", None)
    if environment_list is not None:
        environment["COMPOSE_FILE"] = environment_list
    for selection in selections:
        line = source.count("\n", 0, selection.start()) + 1
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", selection.group() + '\nprintf "%s\\n" args "${compose_files[@]}"'],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, f"{document}:{line}: {result.stderr}"
        assert result.stdout.splitlines() == ["args", *expected], (
            f"{document}:{line}: copied command overrides the selected Compose files"
        )
