"""Guards for the tag@sha256 image pins in the compose files and their Dependabot coverage."""

import re
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
PINNED_IMAGE = re.compile(r"^\s*image:\s*(?P<ref>[^\s@#]+@sha256:[0-9a-f]{64})\s*$", re.MULTILINE)
FROM_LINE = re.compile(r"^FROM\s+(?P<ref>\S+@sha256:[0-9a-f]{64})", re.MULTILINE)


def pinned_refs(text: str, pattern: re.Pattern[str]) -> list[str]:
    return [match.group("ref") for match in pattern.finditer(text)]


def digests_by_image(files: dict[str, str]) -> dict[str, set[str]]:
    """Map ``name:tag`` to the set of digests used across the given compose file texts."""
    result: dict[str, set[str]] = {}
    for text in files.values():
        for ref in pinned_refs(text, PINNED_IMAGE):
            name, digest = ref.split("@", 1)
            result.setdefault(name, set()).add(digest)
    return result


def _compose_files() -> dict[str, str]:
    try:
        listed = subprocess.run(
            ["git", "ls-files", "docker-compose*.yml", "dev-ops/deploy/*/docker-compose*.yml"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("not a git checkout (release snapshot)")
    return {name: (REPO_ROOT / name).read_text(encoding="utf-8") for name in listed}


def test_digest_consistency_scanner_flags_diverging_pins() -> None:
    a = "image: node:24-alpine@sha256:" + "a" * 64
    b = "image: node:24-alpine@sha256:" + "b" * 64
    assert digests_by_image({"one.yml": a, "two.yml": a}) == {"node:24-alpine": {"sha256:" + "a" * 64}}
    assert len(digests_by_image({"one.yml": a, "two.yml": b})["node:24-alpine"]) == 2


def test_same_image_has_one_digest_in_every_compose_file() -> None:
    files = _compose_files()
    assert files, "no compose files found"
    diverging = {name: sorted(digests) for name, digests in digests_by_image(files).items() if len(digests) > 1}
    assert diverging == {}


def test_local_compose_node_image_matches_dockerfile_node_stage() -> None:
    dockerfile = pinned_refs((REPO_ROOT / "Dockerfile").read_text(encoding="utf-8"), FROM_LINE)
    node_refs = [ref for ref in dockerfile if ref.startswith("node:")]
    assert node_refs, "Dockerfile has no pinned node stage"
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert node_refs[0] in pinned_refs(compose, PINNED_IMAGE)


def _dependabot_ecosystems() -> dict[str, set[str]]:
    config = yaml.safe_load((REPO_ROOT / ".github/dependabot.yml").read_text(encoding="utf-8"))
    ecosystems: dict[str, set[str]] = {}
    for entry in config["updates"]:
        directories = entry.get("directories") or [entry["directory"]]
        ecosystems.setdefault(entry["package-ecosystem"], set()).update(directories)
    return ecosystems


def test_dependabot_watches_the_directories_of_all_pinned_compose_files() -> None:
    if not (REPO_ROOT / ".github/dependabot.yml").exists():
        pytest.skip("dependabot.yml is not part of the release snapshot")
    directories = {"/" + str(Path(name).parent) if Path(name).parent != Path(".") else "/" for name in _compose_files()}
    watched = _dependabot_ecosystems().get("docker-compose", set())
    assert directories <= watched, f"unwatched compose directories: {sorted(directories - watched)}"
