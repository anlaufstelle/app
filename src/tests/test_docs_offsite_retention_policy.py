"""The off-site retention contract is an explicit destination policy."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("path", "heading", "maximum", "requirements"),
    [
        (
            "docs/ops-runbook.md",
            "#### Off-Site-Aufbewahrung",
            "12 Monate",
            ("S3 Lifecycle", "Storage-Box", "Prune", "Ransomware", "Löschkonzept", "Informationspflicht"),
        ),
        (
            "docs/en/ops-runbook.md",
            "#### Off-Site Retention",
            "12 months",
            ("S3 Lifecycle", "Storage Box", "prune", "ransomware", "deletion concept", "information obligations"),
        ),
    ],
)
def test_runbook_requires_bounded_retention_at_the_destination(path, heading, maximum, requirements):
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    assert heading in text, f"Missing destination retention section: {path}"
    section = text.split(heading, 1)[1].split("\n### ", 1)[0]
    assert maximum in section
    assert all(requirement in section for requirement in requirements)
    assert "backup.sh" in section


@pytest.mark.parametrize(
    ("path", "heading", "requirements", "forbidden"),
    [
        (
            "docs/ops-runbook.md",
            "#### Off-Site-Aufbewahrung",
            (
                "das konfigurierte Ziel",
                "zielinternen Replikate",
                "außerhalb des Ziels",
                "Löschkonzept",
                "derselben Höchstfrist",
            ),
            "weitere Kopien",
        ),
        (
            "docs/en/ops-runbook.md",
            "#### Off-Site Retention",
            (
                "configured destination",
                "replicas within the destination",
                "outside the destination",
                "deletion concept",
                "same maximum period",
            ),
            "further copies",
        ),
    ],
)
def test_runbook_limits_deletion_evidence_to_the_configured_destination(path, heading, requirements, forbidden):
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    section = text.split(heading, 1)[1].split("\n### ", 1)[0]
    assert all(requirement in section for requirement in requirements)
    assert forbidden not in section


@pytest.mark.parametrize(
    ("path", "marker", "requirements", "forbidden"),
    [
        (
            "docs/ops-runbook.md",
            "Off-Site nach der am Ziel konfigurierten Höchstfrist",
            ("zielinternen Replikate", "Kopien außerhalb des Ziels", "Löschkonzept", "derselben Höchstfrist"),
            "weiterer Kopien",
        ),
        (
            "docs/en/ops-runbook.md",
            "off-site expiry follows the maximum retention configured at the destination",
            (
                "replicas within the destination",
                "copies outside the destination",
                "deletion concept",
                "same maximum period",
            ),
            "and other copies",
        ),
    ],
)
def test_key_rotation_limits_backup_expiry_to_the_configured_destination(path, marker, requirements, forbidden):
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    assert marker in text
    sentence = text.split(marker, 1)[1].split("\n", 1)[0][:600]
    assert all(requirement in sentence for requirement in requirements)
    assert forbidden not in sentence
