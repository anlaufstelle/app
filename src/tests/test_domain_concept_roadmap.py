"""Guard: the domain concept's roadmap makes no promise the project does not back.

The roadmap in section 8 lists targets without commitment and without a date.
It must therefore carry no priority column and no ticket numbers, and every
section that mentions the KDS export must describe it the same way and
without anticipating format or scope. Section 22 names the bodies of the
German addiction-help statistics, and section 25 only points to roadmap steps
that section 8 actually lists.
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
CONCEPT_DE = REPO_ROOT / "docs" / "fachkonzept-anlaufstelle.md"
SUMMARY_EN = REPO_ROOT / "docs" / "en" / "domain-concept-summary.md"

#: The one wording every mention of the KDS export has to carry.
KDS_WORDING = "Export für die Deutsche Suchthilfestatistik; ohne Zusage zu Format und Umfang"


def _section(text: str, heading: str) -> str:
    """Return the body of a ``## `` heading up to the next ``## `` heading."""
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    assert match, f"heading not found: {heading}"
    return match.group(1)


def _subsection(text: str, heading: str) -> str:
    match = re.search(rf"^### {re.escape(heading)}\n(.*?)(?=^#{{2,3}} |\Z)", text, re.MULTILINE | re.DOTALL)
    assert match, f"heading not found: {heading}"
    return match.group(1)


@pytest.fixture(scope="module")
def concept() -> str:
    return CONCEPT_DE.read_text(encoding="utf-8")


def _roadmap_table(concept: str) -> list[str]:
    roadmap = _subsection(_section(concept, "8. Aktueller Stand und Roadmap"), "Roadmap")
    return [line for line in roadmap.splitlines() if line.startswith("|")]


def test_roadmap_table_has_no_priority_column(concept):
    rows = _roadmap_table(concept)
    assert rows, "roadmap table missing"
    assert "Priorität" not in rows[0]
    assert all(row.count("|") == 3 for row in rows), "roadmap table must have exactly two columns"


def test_roadmap_table_names_no_ticket_numbers(concept):
    for row in _roadmap_table(concept):
        assert not re.search(r"#\d+|/issues/\d+|ADR-\d+", row), row


def test_roadmap_intro_keeps_the_no_commitment_statement(concept):
    roadmap = _subsection(_section(concept, "8. Aktueller Stand und Roadmap"), "Roadmap")
    assert "Zielbilder" in roadmap
    assert "ohne Zusage und ohne Termin" in roadmap


def test_roadmap_lists_the_agreed_topics(concept):
    table = "\n".join(_roadmap_table(concept))
    for topic in (
        "KDS-Export",
        "Externe API für Trägersysteme",
        "Berichte je Einrichtung konfigurierbar",
        "Module je Einrichtung schaltbar",
        "Einrichtung und erste Schritte im Browser",
        "Konfigurationspakete exportieren und erweitern",
        "Rechtstexte und E-Mail-Vorlagen in der Oberfläche pflegen",
    ):
        assert topic in table, topic
    for undecided in ("Erwähnungen", "mehrere Einrichtungen", "KI-Anbindung"):
        assert undecided not in table, undecided


def test_kds_export_is_described_the_same_in_all_sections(concept):
    mentions = {
        "7": _section(concept, "7. Modulstruktur"),
        "8": _subsection(_section(concept, "8. Aktueller Stand und Roadmap"), "Roadmap"),
        "22": _section(concept, "22. Regulatorische Landschaft"),
    }
    for number, body in mentions.items():
        kds_lines = [line for line in body.splitlines() if "KDS-Export" in line or "KDS (Kerndatensatz" in line]
        assert kds_lines, f"section {number} no longer mentions the KDS export"
        for line in kds_lines:
            assert KDS_WORDING in line, f"section {number}: {line}"
            assert "3.0" not in line and "XML" not in line and "JSON" not in line, line


def test_regulatory_section_names_current_bodies(concept):
    body = _section(concept, "22. Regulatorische Landschaft")
    assert "DBDD" not in body
    assert "EBDD" not in body.replace("früher EBDD", "")
    assert "IFT Institut für Therapieforschung" in body
    assert "EUDA" in body


def test_authentication_points_to_listed_roadmap_steps_only(concept):
    auth = _subsection(_section(concept, "25. Administration und Betrieb"), "Authentifizierung")
    roadmap = "\n".join(_roadmap_table(concept))
    if "Roadmap-Schritte" in auth:
        assert "SAML" in roadmap, "section 25 points to roadmap steps that section 8 does not list"
    # Current state: second factor, passkeys and invitations exist in the code.
    for current in ("Passkey", "Einladung"):
        assert current in auth, current


def test_history_has_entry_1_11(concept):
    assert re.search(r"^\| 1\.11 \|", concept, re.MULTILINE)
    assert re.search(r"^Version: 1\.11$", concept, re.MULTILINE)


def test_english_summary_names_no_priorities():
    text = SUMMARY_EN.read_text(encoding="utf-8")
    assert "priority" not in text.lower().split("## non-functional requirements")[1].split("## ")[0]
