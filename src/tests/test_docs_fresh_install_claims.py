"""Guards fuer den ausgelieferten Stand der Fresh-Install-Dokumentation."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS = (
    Path("docs/admin-guide.md"),
    Path("docs/en/admin-guide.md"),
    Path("docs/monitoring-guide.md"),
)
FORBIDDEN_CLAIMS = (
    re.compile(r"noch ausstehenden", re.IGNORECASE),
    re.compile(r"Ziel ab Compose-Fix", re.IGNORECASE),
    re.compile(r"Bis #2219 integriert", re.IGNORECASE),
    re.compile(r"Until #2219 is integrated", re.IGNORECASE),
    re.compile(r"Target after Compose fix", re.IGNORECASE),
    re.compile(r"erreicht.{0,120}noch nicht", re.IGNORECASE | re.DOTALL),
    re.compile(r"does not yet reach", re.IGNORECASE),
    re.compile(r"Zielstand nach", re.IGNORECASE),
    re.compile(r"target state after", re.IGNORECASE),
)


def _stale_claims(*, root: Path) -> list[str]:
    findings = []
    for relative_path in DOCUMENTS:
        text = (root / relative_path).read_text(encoding="utf-8")
        for pattern in FORBIDDEN_CLAIMS:
            if pattern.search(text):
                findings.append(f"{relative_path}: {pattern.pattern}")
    return findings


@pytest.mark.parametrize("claim", [pattern.pattern for pattern in FORBIDDEN_CLAIMS])
def test_scanner_detects_stale_fresh_install_claims(tmp_path: Path, claim: str) -> None:
    for relative_path in DOCUMENTS:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Aktueller Stand", encoding="utf-8")
    (tmp_path / DOCUMENTS[0]).write_text(claim.replace(".{0,120}", " jetzt "), encoding="utf-8")

    assert _stale_claims(root=tmp_path)


def test_shipped_docs_do_not_describe_compose_fix_as_pending() -> None:
    assert _stale_claims(root=REPO_ROOT) == []
