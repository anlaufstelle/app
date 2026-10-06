"""The documented mandatory-variable check reads a ``.env`` entry like Compose does.

The check is extracted from the ops runbook (DE and EN) and executed as printed: an
inline comment after the value is not part of the value, an empty value is never "set".
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS = REPO_ROOT / "docs"

pytestmark = pytest.mark.architecture


ENV_ANCHOR = 'ENV_FILE="${ENV_FILE:-.env}"'
ENV_DOCS = {
    "ops-runbook.md": ("✓ {name} gesetzt ({length} Zeichen)", "✗ {name} fehlt oder hat weniger", "✗ {name} steht noch"),
    "en/ops-runbook.md": ("✓ {name} set ({length} characters)", "✗ {name} missing or shorter", "✗ {name} is still"),
}
SECRET = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"  # 32 characters


def _env_check(doc: str, tmp_path: Path, line: str) -> str:
    text = (DOCS / doc).read_text(encoding="utf-8")
    body = text.split(ENV_ANCHOR, 1)[1].split("```", 1)[0]
    env_file = tmp_path / ".env"
    env_file.write_text(f"{line}\nPOSTGRES_ADMIN_USER='admin'\n", encoding="utf-8")
    result = subprocess.run(
        ["bash", "-c", ENV_ANCHOR + body],
        env={**os.environ, "ENV_FILE": str(env_file)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return "\n".join(row for row in result.stdout.splitlines() if "POSTGRES_PASSWORD" in row)


@pytest.mark.parametrize("doc", sorted(ENV_DOCS))
@pytest.mark.parametrize(
    "line",
    [
        "POSTGRES_PASSWORD='' # Password still needs to be configured",
        'POSTGRES_PASSWORD="" # Password still needs to be configured',
        "POSTGRES_PASSWORD= # Password still needs to be configured",
        f"POSTGRES_PASSWORD='short' # {SECRET}",
        "POSTGRES_PASSWORD=''",
    ],
    ids=[
        "single-quoted-empty+comment",
        "double-quoted-empty+comment",
        "unquoted-empty+comment",
        "short+long-comment",
        "empty",
    ],
)
def test_env_check_rejects_empty_and_short_values_even_with_an_inline_comment(
    doc: str, tmp_path: Path, line: str
) -> None:
    _, missing, _ = ENV_DOCS[doc]

    output = _env_check(doc, tmp_path, line)

    assert "✓" not in output, output
    assert missing.format(name="POSTGRES_PASSWORD") in output


@pytest.mark.parametrize("doc", sorted(ENV_DOCS))
@pytest.mark.parametrize(
    "line",
    [
        f"POSTGRES_PASSWORD='{SECRET}'",
        f"POSTGRES_PASSWORD='{SECRET}' # generated with openssl",
        f'POSTGRES_PASSWORD="{SECRET}" # generated with openssl',
        f"POSTGRES_PASSWORD={SECRET} # generated with openssl",
        f"POSTGRES_PASSWORD={SECRET}",
    ],
    ids=["single", "single+comment", "double+comment", "unquoted+comment", "unquoted"],
)
def test_env_check_counts_only_the_value_of_a_valid_entry(doc: str, tmp_path: Path, line: str) -> None:
    ok, _, _ = ENV_DOCS[doc]

    output = _env_check(doc, tmp_path, line)

    assert ok.format(name="POSTGRES_PASSWORD", length=32) in output, output


@pytest.mark.parametrize("doc", sorted(ENV_DOCS))
def test_env_check_flags_a_placeholder_with_an_inline_comment(doc: str, tmp_path: Path) -> None:
    _, _, placeholder = ENV_DOCS[doc]

    output = _env_check(doc, tmp_path, "POSTGRES_PASSWORD='change-me-please-change-me-please-xx' # todo")

    assert placeholder.format(name="POSTGRES_PASSWORD") in output
