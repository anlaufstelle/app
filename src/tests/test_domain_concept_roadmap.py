"""Guard: the domain concept's roadmap makes no promise the project does not back.

The roadmap in section 8 lists targets without commitment and without a date.
It must therefore carry no priority column and no ticket numbers, and every
mention of KDS must disclaim commitment in its paragraph or table row
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

KDS_MENTION_RE = re.compile(r"\bKDS\b", re.IGNORECASE)
KDS_SCOPE_RE = re.compile(r"3\.0|XML|JSON|Aggregat|BASiS[-\s]+Schema", re.IGNORECASE)
ACCOUNT_IMPORT_RE = re.compile(r"CSV[-\s]Import", re.IGNORECASE)
ACCOUNT_RE = re.compile(r"Benutzer|Konten|Konto|Zugänge", re.IGNORECASE)
# Match a small set of explicit future claims, not general planning language.
# The status disclaimer must not cancel a promise to add an unlisted target.
AUTH_FUTURE_RE = re.compile(
    r"\bwird\b[^.!?\n]*\b(?:ergänzt|umgesetzt|kommen)\b|\b(?:geplant|vorgesehen|später\w*|künftig\w*)\b",
    re.IGNORECASE,
)


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


def _kds_violations(text: str) -> list[str]:
    """Check each paragraph, list item and table row across the whole document."""
    blocks = re.split(r"\n\s*\n|\n(?=\s*(?:\||[-*+] |\#{1,6} ))", text)
    mentions = [block for block in blocks if KDS_MENTION_RE.search(block)]
    assert mentions, "document no longer mentions KDS"
    return [
        block
        for block in mentions
        if "ohne zusage" not in " ".join(block.casefold().split()) or KDS_SCOPE_RE.search(block)
    ]


def test_kds_mentions_disclaim_commitment_without_format_or_scope(concept):
    violations = _kds_violations(concept)
    assert not violations, "KDS commitment or format/scope claim:\n" + "\n".join(violations)


@pytest.mark.parametrize("scope", ["3.0", "XML", "JSON", "Aggregate", "BASiS-Schema"])
def test_kds_guard_rejects_format_or_scope_in_any_section(concept, scope):
    text = concept + f"\n\n## Additional section\n\nKDS-Export: ohne Zusage, {scope}.\n"
    assert _kds_violations(text)


@pytest.mark.parametrize("block", ["KDS-Export als Zielbild.", "| KDS | Export |"])
def test_kds_guard_requires_disclaimer_in_the_same_block(concept, block):
    assert _kds_violations(concept + f"\n\n{block}\n\nOhne Zusage.\n")


def test_kds_guard_accepts_equivalent_wording():
    assert not _kds_violations("Ein Zielbild ohne Zusage: KDS-Ausgabe für die Suchthilfestatistik.")


def test_regulatory_section_names_current_bodies(concept):
    body = _section(concept, "22. Regulatorische Landschaft")
    assert "DBDD" not in body
    assert "EBDD" not in body.replace("früher EBDD", "")
    assert "IFT Institut für Therapieforschung" in body
    assert "EUDA" in body


def _authentication_violations(concept: str) -> list[str]:
    """Check implementation status and future promises independently per sentence."""
    auth = _subsection(_section(concept, "25. Administration und Betrieb"), "Authentifizierung")
    roadmap = "\n".join(_roadmap_table(concept))
    sentences = re.split(r"(?<=[.!?])\s+|\n(?=\s*[-*+] )", auth)
    violations = []
    for sentence in sentences:
        unlisted = any(
            re.search(rf"\b{protocol}\b", sentence, re.IGNORECASE)
            and not re.search(rf"\b{protocol}\b", roadmap, re.IGNORECASE)
            for protocol in ("SAML", "OIDC")
        )
        if ACCOUNT_IMPORT_RE.search(sentence) and ACCOUNT_RE.search(sentence):
            unlisted |= not any(
                ACCOUNT_IMPORT_RE.search(row) and ACCOUNT_RE.search(row) for row in _roadmap_table(concept)
            )
        status_disclaimed = "nicht implementiert" in " ".join(sentence.casefold().split())
        if unlisted and (not status_disclaimed or AUTH_FUTURE_RE.search(sentence)):
            violations.append(sentence)
    return violations


def test_authentication_points_to_listed_roadmap_steps_only(concept):
    violations = _authentication_violations(concept)
    assert not violations, "Unlisted authentication target or future promise:\n" + "\n".join(violations)


@pytest.mark.parametrize("target", ["SAML", "OIDC", "CSV-Import von Benutzerlisten"])
def test_authentication_guard_checks_each_claim_despite_another_disclaimer(concept, target):
    text = concept.replace("### Authentifizierung\n", f"### Authentifizierung\n\n{target} ist vorgesehen.\n")
    assert f"{target} ist vorgesehen." in [claim.strip() for claim in _authentication_violations(text)]


@pytest.mark.parametrize("target", ["SAML", "OIDC", "CSV-Import von Benutzerlisten"])
@pytest.mark.parametrize("listed", [False, True], ids=["explicit-disclaimer", "listed-target"])
def test_authentication_guard_accepts_disclaimed_or_listed_targets(concept, target, listed):
    sentence = f"{target} ist {'vorgesehen' if listed else 'nicht implementiert'}."
    text = concept.replace("### Authentifizierung\n", f"### Authentifizierung\n\n{sentence}\n")
    if listed:
        text = text.replace("| Thema | Beschreibung |", f"| {target} | Zielbild |\n| Thema | Beschreibung |")
    assert not _authentication_violations(text)


@pytest.mark.parametrize("target", ["SAML", "OIDC", "CSV-Import von Benutzerlisten"])
@pytest.mark.parametrize(
    "future",
    [
        "wird aber in späteren Roadmap-Schritten ergänzt",
        "wird ergänzt",
        "wird umgesetzt",
        "wird kommen",
        "ist geplant",
        "ist später verfügbar",
        "ist künftig verfügbar",
    ],
)
def test_authentication_guard_rejects_future_promises_despite_current_status(concept, target, future):
    sentence = f"{target} ist nicht implementiert, {future}."
    text = concept.replace("### Authentifizierung\n", f"### Authentifizierung\n\n{sentence}\n")
    assert sentence in [claim.strip() for claim in _authentication_violations(text)]


def test_authentication_guard_accepts_status_only_for_combined_protocols(concept):
    text = concept.replace("### Authentifizierung\n", "### Authentifizierung\n\nSAML/OIDC: Nicht implementiert.\n")
    assert not _authentication_violations(text)


def test_history_has_entry_1_11(concept):
    assert re.search(r"^\| 1\.11 \|", concept, re.MULTILINE)
    assert re.search(r"^Version: 1\.11$", concept, re.MULTILINE)


def test_english_summary_names_no_priorities():
    text = SUMMARY_EN.read_text(encoding="utf-8")
    assert "priority" not in text.lower().split("## non-functional requirements")[1].split("## ")[0]
