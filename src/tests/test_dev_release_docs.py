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
    if "CONTRIBUTING.md#python--django" not in checklist:
        errors.append("wrong Django version anchor")
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
    errors.extend(handoff_findings(checklist))
    return errors


def handoff_findings(checklist):
    """Maintainer pushes bypass the pre-push hook; tag checks must not gate a chain."""
    start = checklist.find("### Maintainer-Handoff vX.Y.Z")
    if start < 0:
        return ["missing maintainer handoff template"]
    end = checklist.find("````", start)
    template = checklist[start : end if end >= 0 else len(checklist)]
    errors = []
    for line in template.splitlines():
        if re.search(r"\bgit\b[^`\n]*\bpush\b", line) and "--no-verify" not in line:
            errors.append(f"handoff push without --no-verify: {line.strip()}")
        if re.search(r"\btag -v\b[^`\n]*&&", line):
            errors.append(f"handoff chains tag -v with &&: {line.strip()}")
    if "Profil `public`" not in template:
        errors.append("handoff lacks the public-profile E2E run")
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


def test_handoff_guard_detects_hooked_pushes_and_chained_tag_checks():
    template = (
        "### Maintainer-Handoff vX.Y.Z\n"
        "git push stage main\n"
        "git -C repo push --no-verify origin vX.Y.Z\n"
        "git tag -v vX.Y.Z && git push --no-verify origin vX.Y.Z\n"
        "````\n"
        "git push origin main\n"
    )
    errors = handoff_findings(template)
    assert errors == [
        "handoff push without --no-verify: git push stage main",
        "handoff chains tag -v with &&: git tag -v vX.Y.Z && git push --no-verify origin vX.Y.Z",
        "handoff lacks the public-profile E2E run",
    ]


@pytest.mark.skipif(not (ROOT / "docs/dev").exists(), reason="docs/dev/ is absent from public snapshots")
def test_dev_repository_guidance_does_not_claim_admin_ruleset_bypass():
    text = (ROOT / "docs/dev/release-checklist.md").read_text()
    paragraph = text.split("**Dev-Repo:**", 1)[1].split("\n\n", 1)[0]
    assert "Bypass-Berechtigte: nur Repo-Admin" not in paragraph
    assert "ohne GitHub Pro" in paragraph
    assert "#branch-schutz-und-maintainer-nachweis" in paragraph


@pytest.mark.skipif(not (ROOT / "docs/dev").exists(), reason="docs/dev/ is absent from public snapshots")
def test_platform_evidence_has_one_handoff_reference_and_read_only_commands():
    text = (ROOT / "docs/dev/release-checklist.md").read_text()
    section = text.split("## 4.2 Plattform-Nachweise am Spiegel (Maintainer)", 1)[1].split("## Rollback", 1)[0]
    template = text.split("### Maintainer-Handoff vX.Y.Z", 1)[1].split("````", 1)[0]
    assert sum("[ ]" in line and "§ 4.2" in line for line in template.splitlines()) == 1
    for ticket in (1079, 1217, 1597, 2529):
        assert f"#{ticket}" in section
    assert "(6a)" in section and "(3)" in section
    assert "403/404" in section and "nicht verifiziert" in section
    for line in section.splitlines():
        if line.startswith(("gh ", "git ", "GH_TOKEN=")):
            assert '|| echo "ABBRUCH:' in line
            assert "# Erwartet:" in line
            assert not re.search(r"gh api.*(?:--method| -X )|gh pr close|git push|gh workflow run", line)
