"""Guard: the domain concept's roadmap makes no promise the project does not back.

The roadmap in section 8 lists targets without commitment and without a date.
It must therefore carry no priority column or ticket numbers and excludes KDS.
Sections 7 and 22 describe the KDS export as not implemented and not on the
roadmap. Across both languages, export claims about KDS, addiction-help
statistics and BASiS must match an allowed status statement, dataset definition
or the BASiS review need. Format and scope remain undecided. Section 22 names
the bodies of the German addiction-help statistics.
Section 25 only points to roadmap steps that section 8 actually lists.
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


KDS_EXPORT_RE = re.compile(r"\bKDS[-\s]+(?:Export|Ausgabe)\b", re.IGNORECASE)
KDS_STATUS = "nicht implementiert und nicht in der roadmap"
# Preserve the product name's spelling: ordinary German "Basis" is unrelated.
BASIS_MENTION_RE = re.compile(
    r"(?-i:\bBASiS\b)|\bbasis[-\s]+(?:export|schnittstelle|interface|compatibility|kompatibilität)\b",
    re.IGNORECASE,
)
STATISTICS_MENTION_RE = re.compile(r"Suchthilfestatistik|addiction[-\s]help statistics", re.IGNORECASE)
EXPORT_ACTION_RE = re.compile(r"export|schnittstelle|interface|kompatibilität|compatibility", re.IGNORECASE)
EXPORT_SUBJECT_DE = (
    r"(?:(?:der|die|ein|eine) )?(?:KDS[- ](?:Export|Ausgabe)|BASiS[- ](?:Export|Schnittstelle)"
    r"|Export für die (?:Deutsche )?Suchthilfestatistik)"
)
EXPORT_SUBJECT_EN = (
    r"(?:an? |the )?(?:(?:KDS|BASiS)[- ](?:export|interface)"
    r"|export for (?:the )?(?:German )?addiction[- ]help statistics)"
)
EXPORT_STATUS_DE = r"nicht implementiert und nicht in der roadmap(?: \(§ 8\))?"
EXPORT_STATUS_EN = r"not implemented and is not on the roadmap(?: \((?:German concept )?§ 8\))?"
# Full matches only: a status or definition must not license extra promises.
# These are the permitted statements, not a vocabulary of forbidden futures.
EXPORT_ALLOWED_RE = re.compile(
    "|".join(
        f"(?:{pattern})"
        for pattern in (
            rf"(?:Stand: )?(?:KDS[- ]Export: )?{EXPORT_SUBJECT_DE} (?:ist )?{EXPORT_STATUS_DE}",
            rf"(?:KDS export: )?{EXPORT_SUBJECT_EN} is {EXPORT_STATUS_EN}",
            r"KDS \(Kerndatensatz Suchthilfe\)",
            r"(?:Der )?KDS ist ein Kerndatensatz",
            r"(?:The )?KDS is (?:a|the) core dataset(?: for (?:German )?addiction[- ]help statistics)?",
            r"BASiS-Kompatibilität ist ein fachlicher Prüfbedarf, keine zugesagte implementierte Schnittstelle",
            r"§ 7 und § 22 beschreiben den KDS-Export als nicht implementiert und nicht in der Roadmap \(§ 8\); "
            r"§ 22 erhält den Kerndatensatz als fachlichen Kontext und nennt IFT und EUDA statt DBDD/EBDD; "
            r"BASiS bleibt fachlicher Prüfbedarf ohne implementierte Schnittstelle und ohne Roadmap-Eintrag",
        )
    ),
    re.IGNORECASE,
)
EXPORT_TABLE_ALLOWED = {
    "kds": {
        "kds",
        "kds (kerndatensatz suchthilfe)",
        "einheitlicher kerndatensatz der deutschen suchthilfestatistik. "
        "relevant für einrichtungen, die sich an der deutschen suchthilfestatistik beteiligen; "
        "ausgewertet wird sie vom ift institut für therapieforschung, münchen",
    },
    "basis": {
        "basis",
        "basis (bundesarbeitsgemeinschaft)",
        "standardisierte leistungsdokumentation in der suchthilfe. "
        "überlappung mit der statistik-zuordnung in der domänenbibliothek",
        "fachlicher prüfbedarf; keine schnittstelle implementiert und nicht in der roadmap (§ 8)",
    },
}


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


def _plain_export_statement(statement: str) -> str:
    """Normalize Markdown emphasis before identifying and checking a subject."""
    plain = re.sub(r"^\s*(?:[-*+] |\#{1,6} )", "", statement)
    return " ".join(re.sub(r"[*_`]", "", plain).split()).rstrip(".")


def _export_statements(text: str) -> list[tuple[str, str | None]]:
    """Keep table cells separate and carry their subject into every cell."""
    blocks = re.split(r"\n\s*\n|\n(?=\s*(?:\||[-*+] |\#{1,6} ))", text)
    statements = []
    for block in blocks:
        if block.lstrip().startswith("|"):
            cells = block.strip().strip("|").split("|")
            subject = None
            label = _plain_export_statement(cells[0])
            if KDS_MENTION_RE.search(label):
                subject = "kds"
            elif BASIS_MENTION_RE.search(label):
                subject = "basis"
            if subject:
                statements.extend((cell, subject) for cell in cells)
                continue
            # History cells discuss unrelated features in separate sentences.
            blocks_to_split = cells
        else:
            blocks_to_split = [block]
        for value in blocks_to_split:
            statements.extend((sentence, None) for sentence in re.split(r"(?<=[.!?])\s+", value))
    return statements


def _kds_violations(text: str, *, check_sections: bool = True) -> list[str]:
    """Allow only export status, dataset definitions and the BASiS review need."""
    violations = []
    for statement, subject in _export_statements(text):
        plain = _plain_export_statement(statement)
        relevant = (
            subject
            or KDS_MENTION_RE.search(plain)
            or BASIS_MENTION_RE.search(plain)
            or (STATISTICS_MENTION_RE.search(plain) and EXPORT_ACTION_RE.search(plain))
        )
        if not relevant:
            continue
        allowed = EXPORT_ALLOWED_RE.fullmatch(plain) or plain.casefold() in EXPORT_TABLE_ALLOWED.get(subject, set())
        if not allowed or ((subject == "kds" or KDS_MENTION_RE.search(plain)) and KDS_SCOPE_RE.search(plain)):
            violations.append(statement)
    if check_sections:
        for heading in ("7. Modulstruktur", "22. Regulatorische Landschaft"):
            body = _section(text, heading)
            section_blocks = re.split(r"\n\s*\n|\n(?=\s*(?:\||[-*+] ))", body)
            if not any(
                KDS_EXPORT_RE.search(block) and KDS_STATUS in " ".join(block.casefold().split())
                for block in section_blocks
            ):
                violations.append(f"KDS export status missing in {heading}")
        if KDS_MENTION_RE.search(_section(text, "8. Aktueller Stand und Roadmap")):
            violations.append("KDS must not appear in section 8")
    return violations


def test_kds_mentions_have_no_target_promise_format_or_scope(concept):
    violations = _kds_violations(concept)
    assert not violations, "KDS commitment or format/scope claim:\n" + "\n".join(violations)


@pytest.mark.parametrize("scope", ["3.0", "XML", "JSON", "Aggregate", "BASiS-Schema"])
def test_kds_guard_rejects_format_or_scope_in_any_section(concept, scope):
    text = concept + f"\n\n## Additional section\n\nKDS-Export: ohne Zusage, {scope}.\n"
    assert _kds_violations(text)


@pytest.mark.parametrize("block", ["KDS-Export als Zielbild.", "| KDS-Export | Zielbild |"])
def test_kds_guard_rejects_targets_despite_a_separate_disclaimer(concept, block):
    assert _kds_violations(concept + f"\n\n{block}\n\nOhne Zusage.\n")


def test_kds_guard_accepts_equivalent_wording(concept):
    text = concept.replace(
        "nicht implementiert und nicht in der Roadmap",
        "NICHT IMPLEMENTIERT und nicht in der Roadmap",
    )
    assert not _kds_violations(text)


@pytest.mark.parametrize(
    "heading",
    ["7. Modulstruktur", "22. Regulatorische Landschaft", None],
    ids=["section-7", "section-22", "history-only"],
)
def test_kds_guard_rejects_missing_section_claims(concept, heading):
    if heading is None:
        lines = concept.splitlines(keepends=True)
        text = "".join(line for line in lines if not KDS_MENTION_RE.search(line) or line.startswith("| 1.11 |"))
    else:
        body = _section(concept, heading)
        replacement = "\n".join(line for line in body.splitlines() if not KDS_MENTION_RE.search(line))
        text = concept.replace(body, replacement + "\n\n")
    assert _kds_violations(text)


def test_kds_guard_rejects_export_restored_to_roadmap(concept):
    text = concept.replace(
        "| Thema | Beschreibung |",
        "| Thema | Beschreibung |\n| KDS-Export | Zielbild ohne Zusage zu Format und Umfang |",
    )
    assert _kds_violations(text)


@pytest.mark.parametrize("heading", ["7. Modulstruktur", "22. Regulatorische Landschaft"])
def test_kds_guard_requires_status_in_each_section(concept, heading):
    body = _section(concept, heading)
    text = concept.replace(body, body.replace("nicht implementiert und nicht in der Roadmap", "offen"))
    assert f"KDS export status missing in {heading}" in _kds_violations(text)


@pytest.mark.parametrize(
    "claim",
    [
        "ist Zielbild",
        "wird später ergänzt",
        "ist nicht implementiert, wird aber ergänzt",
        "wird umgesetzt",
        "wird kommen",
        "ist geplant",
        "ist vorgesehen",
        "ist künftig verfügbar",
        "ist zugesagt",
    ],
    ids=[
        "target",
        "later-added",
        "status-and-promise",
        "implemented",
        "coming",
        "planned",
        "intended",
        "future",
        "promised",
    ],
)
@pytest.mark.parametrize("heading", ["22. Regulatorische Landschaft", None], ids=["section-22", "elsewhere"])
def test_kds_guard_rejects_targets_and_promises(concept, claim, heading):
    sentence = f"KDS-Export {claim}; ohne Zusage zu Format und Umfang."
    if heading:
        body = _section(concept, heading)
        text = concept.replace(body, f"\n{sentence}\n" + body)
    else:
        text = concept + f"\n\n{sentence}\n"
    assert any(sentence in violation for violation in _kds_violations(text))


@pytest.mark.parametrize(
    "claim",
    ["is a target", "is a vision", "will be added later", "is planned", "is promised"],
)
def test_kds_guard_rejects_english_targets_and_promises(claim):
    sentence = f"KDS export {claim}; without commitment on format or scope."
    text = SUMMARY_EN.read_text(encoding="utf-8") + f"\n\n{sentence}\n"
    assert any(sentence in violation for violation in _kds_violations(text, check_sections=False))


def test_kds_guard_rejects_export_restored_to_optional_module_targets():
    text = SUMMARY_EN.read_text(encoding="utf-8").replace(
        "include a calendar, wiki and interfaces",
        "include a calendar, wiki, a KDS export and interfaces",
    )
    assert _kds_violations(text, check_sections=False)


def test_english_summary_has_no_kds_target_or_promise():
    text = SUMMARY_EN.read_text(encoding="utf-8")
    assert not _kds_violations(text, check_sections=False)


@pytest.mark.parametrize(
    "sentence",
    [
        "KDS export is not implemented and is not on the roadmap.",
        "KDS-Export ist nicht implementiert und nicht in der Roadmap (§ 8).",
    ],
)
def test_kds_guard_accepts_status_without_a_promise(concept, sentence):
    assert not _kds_violations(concept + f"\n\n{sentence}\n")


@pytest.mark.parametrize(
    ("sentence", "english"),
    [
        ("KDS-Export soll folgen.", False),
        ("KDS-Export ist in Arbeit.", False),
        ("KDS-Export folgt.", False),
        ("KDS-Export kommt.", False),
        ("KDS-Export ist nicht implementiert; er wird nachgeliefert.", False),
        ("KDS-Export ist angestrebt.", False),
        ("KDS-Export ist nicht implementiert, steht aber auf der Roadmap.", False),
        ("KDS-Export ist in der Roadmap enthalten.", False),
        ("Der Export für die Deutsche Suchthilfestatistik ist ein Ausblick.", False),
        ("Ein Export für die Suchthilfestatistik ist Zielbild.", False),
        ("KDS-Export ist nicht implementiert und nicht in der Roadmap; er ist als nächster Schritt eingeplant.", False),
        ("KDS-Export ist nicht implementiert und nicht in der Roadmap; wir arbeiten daran.", False),
        ("KDS-Export wird nachgerüstet.", False),
        ("KDS-Export ist perspektivisch möglich.", False),
        ("KDS export is coming soon.", True),
        ("KDS export will follow.", True),
        ("KDS export is on the roadmap.", True),
        ("KDS export is under development.", True),
        ("KDS export is intended.", True),
        ("KDS export is not implemented but will be delivered.", True),
        ("KDS export is envisaged.", True),
        ("An export for the German addiction-help statistics is a target.", True),
        ("KDS export is not implemented and is not on the roadmap, but we aim to add it.", True),
        ("- KDS-Export für Einrichtungen.", False),
        ("BASiS-Export ist Zielbild.", False),
        ("BASiS-Schnittstelle ist in Arbeit.", False),
        ("BASiS-Export nicht implementiert und nicht in der Roadmap; er soll folgen.", False),
    ],
)
def test_export_guard_rejects_unlisted_claims(concept, sentence, english):
    text = SUMMARY_EN.read_text(encoding="utf-8") if english else concept
    assert _kds_violations(text + f"\n\n{sentence}\n", check_sections=not english)


@pytest.mark.parametrize(
    "row",
    [
        "| BASiS | Zielbild: domänenspezifischer Export |",
        (
            "| BASiS | Fachlicher Prüfbedarf; keine Schnittstelle implementiert und nicht in der Roadmap (§ 8) "
            "| Export folgt. |"
        ),
        "| KDS | KDS-Export nicht implementiert und nicht in der Roadmap (§ 8) | Export folgt. |",
        "| KDS-Export | nicht implementiert und nicht in der Roadmap; soll folgen |",
        (
            "| BASiS | Fachlicher Prüfbedarf; keine Schnittstelle implementiert und nicht in der Roadmap (§ 8). "
            "Export folgt. |"
        ),
    ],
)
def test_export_guard_checks_each_cell_with_its_table_subject(concept, row):
    assert _kds_violations(concept + f"\n\n{row}\n")


@pytest.mark.parametrize(
    "sentence",
    [
        "KDS-Export ist nicht geplant.",
        "KDS export is not planned.",
        "Der KDS ist ein Kerndatensatz und der Export folgt.",
        "KDS-Export ist nicht implementiert und nicht in der Roadmap, folgt aber bald.",
        "Export für die Suchthilfestatistik ist Zielbild.",
        "Export for German addiction-help statistics is coming soon.",
        "BASiS-Kompatibilität ist ein fachlicher Prüfbedarf, die Schnittstelle folgt.",
        "BASiS interface is not implemented and is not on the roadmap, but we aim to add it.",
    ],
)
def test_export_guard_requires_complete_allowed_statements(concept, sentence):
    assert _kds_violations(concept + f"\n\n{sentence}\n")


@pytest.mark.parametrize(
    "sentence",
    [
        "Der KDS ist ein Kerndatensatz.",
        "KDS (Kerndatensatz Suchthilfe)",
        "Ein Export für die Suchthilfestatistik ist nicht implementiert und nicht in der Roadmap (§ 8).",
        "BASiS-Export ist nicht implementiert und nicht in der Roadmap (§ 8).",
        "BASiS-Schnittstelle ist nicht implementiert und nicht in der Roadmap (§ 8).",
        "BASiS-Kompatibilität ist ein fachlicher Prüfbedarf, keine zugesagte implementierte Schnittstelle.",
        "An export for the German addiction-help statistics is not implemented and is not on the roadmap.",
        "BASiS export is not implemented and is not on the roadmap.",
    ],
)
def test_export_guard_accepts_only_status_definition_or_review_need(concept, sentence):
    assert not _kds_violations(concept + f"\n\n{sentence}\n")


@pytest.mark.parametrize(
    "statement",
    [
        "_KDS_-Export steht auf der Roadmap.",
        "_BASiS_-Schnittstelle ist in Arbeit.",
        "| _KDS_ | Export folgt. |",
    ],
)
def test_export_guard_checks_markdown_subjects(concept, statement):
    assert _kds_violations(concept + f"\n\n{statement}\n")


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
