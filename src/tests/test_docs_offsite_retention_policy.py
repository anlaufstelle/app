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
