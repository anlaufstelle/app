"""The off-site retention contract is an explicit destination policy."""

import re
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
                "derselben Löschregel",
                "anderen Anbieter",
                "Kopien der Sicherungssätze",
                "außerhalb des Ziels",
                "gehören nicht zu diesem Nachweis",
                "Löschkonzept",
                "derselben Höchstfrist",
                "belegt deren Löschung selbst",
            ),
            r"weitere[nr]?\s+Kopien",
        ),
        (
            "docs/en/ops-runbook.md",
            "#### Off-Site Retention",
            (
                "configured destination",
                "replicas within the destination",
                "same deletion rule",
                "another provider",
                "opies of the backup sets",
                "outside the destination",
                "are not part of this evidence",
                "deletion concept",
                "same maximum period",
                "evidences their deletion itself",
            ),
            r"(?:further|other)\s+copies",
        ),
    ],
)
def test_runbook_limits_deletion_evidence_to_the_configured_destination(path, heading, requirements, forbidden):
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    section = text.split(heading, 1)[1].split("\n### ", 1)[0]
    missing = [requirement for requirement in requirements if requirement not in section]
    assert not missing, f"{path}: missing pinned wording {missing}"
    assert not re.search(forbidden, section), f"{path}: outdated wording about further copies"


@pytest.mark.parametrize(
    ("path", "marker", "requirements", "forbidden"),
    [
        (
            "docs/ops-runbook.md",
            "Off-Site nach der am Ziel konfigurierten Höchstfrist",
            (
                "zielinternen Replikate",
                "Kopien der Sicherungssätze außerhalb des Ziels",
                "Löschkonzept",
                "derselben Höchstfrist",
                "Altschlüssel bleibt im Eskrow, bis auch diese Kopien abgelaufen sind",
            ),
            r"weitere[nr]?\s+Kopien",
        ),
        (
            "docs/en/ops-runbook.md",
            "off-site expiry follows the maximum retention configured at the destination",
            (
                "replicas within the destination",
                "copies of the backup sets outside the destination",
                "deletion concept",
                "same maximum period",
                "old key stays in escrow until those copies have expired",
            ),
            r"(?:\band|,)\s+other\s+copies|further\s+copies",
        ),
    ],
)
def test_key_rotation_limits_backup_expiry_to_the_configured_destination(path, marker, requirements, forbidden):
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    assert marker in text
    sentence = text.split(marker, 1)[1].split("\n", 1)[0]
    missing = [requirement for requirement in requirements if requirement not in sentence]
    assert not missing, f"{path}: missing pinned wording {missing}"
    assert not re.search(forbidden, sentence), f"{path}: outdated wording about further copies"
