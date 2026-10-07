"""Keep executable release guidance aligned with shipped image and gate names."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture
ROOT = Path(__file__).resolve().parents[2]


def release_findings(root):
    dev = root / "docs/dev"
    checklist = (dev / "release-checklist.md").read_text()
    workflow = (dev / "ci-workflows.md").read_text()
    exclusions = (dev / "release-exclusions.md").read_text()
    errors = []
    if "ghcr.io/anlaufstelle/app" in checklist:
        errors.append("wrong image repository")
    if re.search(r"^docker compose[^\n]*pull ghcr\.io/", checklist, re.MULTILINE):
        errors.append("compose pull expects a service, not an image")
    for gate in ("fence-lint", "--compare-render-structure", "--list-dead-fragments"):
        if gate not in checklist:
            errors.append(f"missing release gate: {gate}")
    if "Visibility-public-Flip" in workflow or "Visibility-Step" in workflow:
        errors.append("obsolete visibility mutation")
    if "Verify the image is publicly pullable" not in workflow:
        errors.append("missing anonymous pull check")
    if "aktuell **36**" in exclusions:
        errors.append("hardcoded strip count")
    return errors


def test_release_documentation_uses_current_commands():
    if not (ROOT / "docs" / "dev").exists():
        pytest.skip("Dev documentation is absent from public snapshots")
    assert release_findings(ROOT) == []


def test_guard_detects_wrong_repository_and_compose_service(tmp_path):
    dev = tmp_path / "docs/dev"
    dev.mkdir(parents=True)
    (dev / "release-checklist.md").write_text("docker compose pull ghcr.io/anlaufstelle/app:vPREVIOUS")
    (dev / "ci-workflows.md").write_text("Visibility-public-Flip")
    (dev / "release-exclusions.md").write_text("aktuell **36**")
    errors = release_findings(tmp_path)
    assert "wrong image repository" in errors
    assert "compose pull expects a service, not an image" in errors
    assert "obsolete visibility mutation" in errors
    assert "hardcoded strip count" in errors
    assert len([error for error in errors if error.startswith("missing release gate")]) == 3
