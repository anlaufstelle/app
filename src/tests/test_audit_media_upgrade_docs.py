"""Operator instructions preserve custom media roots and active hand checks."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("path", ["CHANGELOG.md", "docs/ops-runbook.md", "docs/en/ops-runbook.md"])
def test_media_upgrade_preserves_custom_root_suffix(path):
    text = (ROOT / path).read_text()
    if path == "CHANGELOG.md":
        text = text.split("## [0.99.6]", 1)[0]
    assert "/data/vault" in text
    assert "/media/vault" in text
    assert "/data/media" in text
    assert "/media/media" in text
    assert "MEDIA_ROOT_CONTAINER" in text
    assert "Bestandsanhang" in text if path != "docs/en/ops-runbook.md" else "existing attachment" in text


def test_auth_hand_check_is_in_actual_unreleased_section():
    path = ROOT / "docs/dev/hand-checks.md"
    if not path.is_file():
        pytest.skip("docs/dev/hand-checks.md not in the public snapshot — dev repo only")
    text = path.read_text()
    rules, active = text.split("\n## [Unreleased]\n", 1)
    assert "`/mfa/verify/`" not in rules
    assert "- Neue Einträge landen unter `## [Unreleased]`." in rules
    # The release cut moves the active entries into a per-version archive, so the
    # check lives either in the active section or in an archived release section.
    archives = sorted(path.parent.glob("hand-checks-archive-v*.md"))
    assert "`/mfa/verify/`" in active or any("`/mfa/verify/`" in a.read_text() for a in archives)


@pytest.mark.parametrize("path", ["docs/dev/dev-deployment.md", "dev-ops/deploy/barbara/.env.barbara.example"])
def test_active_deployment_docs_use_current_media_root(path):
    doc = ROOT / path
    if not doc.is_file():
        pytest.skip(f"{path} not in the public snapshot (docs/dev/, dev-ops/ stripped)")
    text = doc.read_text()
    assert "MEDIA_ROOT=/data/media" not in text
    assert "MEDIA_ROOT=/media/media" in text


def test_restore_probe_documents_actual_media_mount():
    text = (ROOT / "docs/disaster-recovery.md").read_text()
    assert "Das Arbeitsverzeichnis liegt auf dem persistenten Volume `/media`" in text
