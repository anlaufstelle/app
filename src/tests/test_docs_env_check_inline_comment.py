"""The documented mandatory-variable check reads a ``.env`` entry like Compose does.

The check is extracted from the ops runbook (DE and EN) and executed as printed: an
inline comment after the value is not part of the value, an empty value is never "set".
"""

from __future__ import annotations

import json
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


# Counter-examples for the value syntax: (id, .env line, value Compose resolves; None = no usable value).
# Expected values were taken from ``docker compose config`` (see ``test_cases_match_compose``).
_S = SECRET
SYNTAX_CASES = [
    ("review-escaped-quote", f'POSTGRES_PASSWORD="prefix\\"{_S}" # configured password', f'prefix"{_S}'),
    ("dq-escaped-quote-no-comment", f'POSTGRES_PASSWORD="prefix\\"{_S}"', f'prefix"{_S}'),
    ("dq-escaped-backslash", f'POSTGRES_PASSWORD="a\\\\{_S}" # c', f"a\\{_S}"),
    ("dq-escaped-dollar", f'POSTGRES_PASSWORD="a\\${_S}"', f"a${_S}"),
    ("dq-backslash-n", f'POSTGRES_PASSWORD="a\\n{_S}"', f"a\n{_S}"),
    ("dq-other-escape-kept", f'POSTGRES_PASSWORD="a\\x{_S}"', f"a\\x{_S}"),
    ("sq-escaped-quote", f"POSTGRES_PASSWORD='a\\'{_S}' # c", f"a'{_S}"),
    ("sq-backslashes-literal", f"POSTGRES_PASSWORD='a\\\\{_S}'", f"a\\\\{_S}"),
    ("dq-single-quote-inside", f'POSTGRES_PASSWORD="a\'{_S}" # c', f"a'{_S}"),
    ("sq-double-quote-inside", f"POSTGRES_PASSWORD='a\"{_S}' # c", f'a"{_S}'),
    ("dq-hash-inside", f'POSTGRES_PASSWORD="a # {_S}" # c', f"a # {_S}"),
    ("sq-hash-inside", f"POSTGRES_PASSWORD='a # {_S}' # c", f"a # {_S}"),
    ("dq-comment-without-space", f'POSTGRES_PASSWORD="{_S}"#c', _S),
    ("dq-trailing-junk", f'POSTGRES_PASSWORD="{_S}" junk', _S),
    ("dq-trailing-spaces", f'POSTGRES_PASSWORD="{_S}"   ', _S),
    ("dq-spaces-inside", f'POSTGRES_PASSWORD=" {_S} "', f" {_S} "),
    ("unquoted-hash-without-space", f"POSTGRES_PASSWORD={_S}#def", f"{_S}#def"),
    ("unquoted-comment", f"POSTGRES_PASSWORD={_S} # c", _S),
    ("unquoted-tab-comment-is-part-of-the-value", f"POSTGRES_PASSWORD={_S}\t# c", f"{_S}\t# c"),
    ("unquoted-trailing-spaces", f"POSTGRES_PASSWORD={_S}   ", _S),
    ("unquoted-leading-spaces", f"POSTGRES_PASSWORD=   {_S}", _S),
    ("export-prefix", f"export POSTGRES_PASSWORD={_S}", _S),
    ("spaces-around-equals", f"POSTGRES_PASSWORD = {_S}", _S),
    # No usable value: the check must say "missing", never "set".
    ("unterminated-quote", f'POSTGRES_PASSWORD="{_S}', None),
    ("empty-then-tab-comment", f"POSTGRES_PASSWORD=\t# {_S} {_S}", None),
    ("empty-then-long-comment", "POSTGRES_PASSWORD= # Password still needs to be configured", None),
    ("empty-dq-then-long-comment", 'POSTGRES_PASSWORD="" # Password still needs to be configured', None),
]


@pytest.mark.parametrize("doc", sorted(ENV_DOCS))
@pytest.mark.parametrize(("line", "value"), [case[1:] for case in SYNTAX_CASES], ids=[case[0] for case in SYNTAX_CASES])
def test_env_check_reads_the_value_like_compose(doc: str, tmp_path: Path, line: str, value: str | None) -> None:
    ok, missing, _ = ENV_DOCS[doc]

    output = _env_check(doc, tmp_path, line)

    if value is None:
        assert "✓" not in output, output
        assert missing.format(name="POSTGRES_PASSWORD") in output
    else:
        assert ok.format(name="POSTGRES_PASSWORD", length=len(value)) in output, output


def _compose_value(tmp_path: Path, line: str) -> str:
    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text(
        "services:\n  check:\n    image: x\n    environment:\n      VALUE: ${POSTGRES_PASSWORD-UNSET}\n",
        encoding="utf-8",
    )
    env_file = tmp_path / "compose.env"
    env_file.write_text(line + "\n", encoding="utf-8")
    environment = {key: val for key, val in os.environ.items() if key != "POSTGRES_PASSWORD"}
    resolved = subprocess.run(
        ["docker", "compose", "--env-file", str(env_file), "-f", str(compose_file), "config", "--format", "json"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert resolved.returncode == 0, resolved.stderr
    # ``config`` prints a literal dollar sign as ``$$``.
    return json.loads(resolved.stdout)["services"]["check"]["environment"]["VALUE"].replace("$$", "$")


def _compose_available() -> bool:
    try:
        return subprocess.run(["docker", "compose", "version"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@pytest.mark.skipif(not _compose_available(), reason="docker compose is not installed")
@pytest.mark.parametrize(
    ("line", "value"),
    [case[1:] for case in SYNTAX_CASES if case[2] is not None],
    ids=[case[0] for case in SYNTAX_CASES if case[2] is not None],
)
def test_cases_match_compose(tmp_path: Path, line: str, value: str) -> None:
    """The expected values of the table above are what Compose really resolves (no daemon needed)."""
    assert _compose_value(tmp_path, line) == value
