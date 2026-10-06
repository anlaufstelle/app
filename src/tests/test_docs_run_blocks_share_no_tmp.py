"""Copyable operations blocks pass no data between separate ``run --rm`` containers via ``/tmp``.

``maintenance`` runs with ``read_only: true`` and ``/tmp`` as tmpfs, and every
``docker compose run --rm`` starts a fresh container with an empty ``/tmp``. A
block that creates a directory in one ``run --rm`` call and uses it in the next
fails exactly when it is needed (disaster recovery). Several commands inside ONE
container (one ``sh -c`` script) may share ``/tmp``; separate calls may not.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.prod.yml"
OPERATIONS_DOCS = (
    REPO_ROOT / "docs" / "ops-runbook.md",
    REPO_ROOT / "docs" / "en" / "ops-runbook.md",
    REPO_ROOT / "docs" / "disaster-recovery.md",
)

_FENCE = re.compile(r"^\s*```")
_COMPOSE_CALL = re.compile(r"docker compose\b")
_TMP_ASSIGNMENT = re.compile(r"""^\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)=["']?(?P<path>/tmp/[^"'\s]+)""")
_TMP_LITERAL = re.compile(r"/tmp/[A-Za-z0-9_.\-$}{]+")  # noqa: S108 - matches documentation text, no file access


def _shell_blocks(markdown: str) -> list[tuple[int, str]]:
    """All fenced blocks as (first content line number, content)."""
    blocks: list[tuple[int, str]] = []
    current: list[str] | None = None
    start = 0
    for number, line in enumerate(markdown.splitlines(), start=1):
        if _FENCE.match(line):
            if current is None:
                current, start = [], number + 1
            else:
                blocks.append((start, "\n".join(current)))
                current = None
        elif current is not None:
            current.append(line)
    return blocks


def _compose_calls(block: str) -> list[str]:
    """Split a block into the text of each ``docker compose`` call (continuation lines included)."""
    calls: list[str] = []
    for line in block.splitlines():
        if _COMPOSE_CALL.search(line):
            calls.append(line)
        elif calls:
            calls[-1] += "\n" + line
    return calls


def find_cross_container_tmp_use(markdown: str) -> list[str]:
    """Describe every ``/tmp`` path that two or more separate ``run --rm`` calls of one block use."""
    findings: list[str] = []
    for start, block in _shell_blocks(markdown):
        variables = {
            match["name"]: match["path"] for line in block.splitlines() if (match := _TMP_ASSIGNMENT.match(line))
        }
        users: dict[str, int] = {}
        for call in _compose_calls(block):
            if " run " not in call or "--rm" not in call:
                continue
            paths = {path for path in _TMP_LITERAL.findall(call) if "$" not in path and "{" not in path}
            for name, path in variables.items():
                if re.search(rf"\${name}\b|\${{{name}\b", call):
                    paths.add(path)
            for path in paths:
                users[path] = users.get(path, 0) + 1
        findings.extend(
            f"line {start}: {path} is used by {count} separate run --rm containers"
            for path, count in users.items()
            if count > 1
        )
    return findings


OLD_BROKEN_BLOCK = """
```bash
MEDIA_VERIFY_DIR="/tmp/media-verify-$RESTORE_TIMESTAMP"
docker compose -f docker-compose.prod.yml run --rm --entrypoint sh -T maintenance \\
  -c 'mkdir -p "$1"' sh "$MEDIA_VERIFY_DIR" && \\
openssl enc -d -in "$MEDIA_ARCHIVE" \\
  | docker compose -f docker-compose.prod.yml run --rm --entrypoint tar -T maintenance \\
    -C "$MEDIA_VERIFY_DIR" -xf - && \\
docker compose -f docker-compose.prod.yml run --rm --entrypoint sh -T maintenance \\
  -c 'rm -rf "$1"' sh "$MEDIA_VERIFY_DIR"
```
"""

SINGLE_CONTAINER_BLOCK = """
```bash
openssl enc -d -in "$MEDIA_ARCHIVE" \\
  | docker compose -f docker-compose.prod.yml run --rm --entrypoint sh -T maintenance -c '
      mkdir -p /tmp/work && tar -C /tmp/work -xf - && ls /tmp/work > /tmp/list && cat /tmp/list
    '
```
"""

EXEC_BLOCK = """
```bash
docker compose -f docker-compose.prod.yml exec -T web sh -c 'echo x > /tmp/a'
docker compose -f docker-compose.prod.yml exec -T web cat /tmp/a
```
"""


def test_detector_flags_the_broken_pattern() -> None:
    findings = find_cross_container_tmp_use(OLD_BROKEN_BLOCK)

    assert findings == ["line 3: /tmp/media-verify-$RESTORE_TIMESTAMP is used by 3 separate run --rm containers"]


def test_detector_accepts_one_container_and_exec_blocks() -> None:
    assert find_cross_container_tmp_use(SINGLE_CONTAINER_BLOCK) == []
    assert find_cross_container_tmp_use(EXEC_BLOCK) == []


def test_maintenance_is_read_only_with_a_tmpfs_tmp() -> None:
    """The rule above rests on this definition; if it changes, the rule must be revisited."""
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    maintenance = compose["services"]["maintenance"]

    assert maintenance["read_only"] is True
    assert "/tmp" in maintenance["tmpfs"]  # noqa: S108 - compose mount point, no file access


@pytest.mark.parametrize("doc", OPERATIONS_DOCS, ids=lambda path: path.relative_to(REPO_ROOT).as_posix())
def test_shipped_operations_docs_share_no_tmp_between_run_containers(doc: Path) -> None:
    assert find_cross_container_tmp_use(doc.read_text(encoding="utf-8")) == []
