import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

IMAGE_TAG = re.compile(r"ghcr\.io/anlaufstelle/app:(v\d+\.\d+\.\d+)\b")
COMPOSE_DEFAULT = re.compile(r"ghcr\.io/anlaufstelle/app:\$\{APP_VERSION:-(v\d+\.\d+\.\d+)\}")


def _operator_docs() -> list[Path]:
    # Tracked files only (lesson: tree guards must not pick up local scratch files).
    listed = subprocess.run(
        ["git", "ls-files", "README.md", "README.en.md", "docs/*.md", "docs/en/*.md"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    return [Path(name) for name in listed if Path(name).exists()]


def _compose_default_version() -> str:
    versions = set(COMPOSE_DEFAULT.findall(Path("docker-compose.prod.yml").read_text(encoding="utf-8")))
    assert len(versions) == 1, versions
    return versions.pop()


def test_concrete_image_tags_in_operator_docs_are_the_published_compose_default() -> None:
    allowed = _compose_default_version()
    offenders = [
        f"{path}:{number}: {tag}"
        for path in _operator_docs()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        for tag in IMAGE_TAG.findall(line)
        if tag != allowed
    ]

    assert offenders == [], (
        "Example image tags must exist in the registry: use the compose default "
        f"({allowed}) or the placeholder vX.Y.Z with a hint how to check a tag."
    )
