"""docs/security-notes.md does not promise a client-PII-free audit log.

``AUDIT_DETAIL_KEYS`` in ``core/services/audit/detail_policy.py`` lists, below
the comment "Bewusst zugelassener Freitext", the ``AuditLog.detail`` keys that
carry real free text (e.g. ``recipient``/``purpose`` of a report release,
validated with a heuristic that cannot guarantee PII-free values). A client name typed there stays in the
append-only log. The security notes must therefore name these keys and must
not claim that audit entries contain no client PII (Refs #2486).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
SECURITY_NOTES = REPO_ROOT / "docs" / "security-notes.md"
DETAIL_POLICY = REPO_ROOT / "src" / "core" / "services" / "audit" / "detail_policy.py"

FREE_TEXT_MARKER = "Bewusst zugelassener Freitext"
AUDIT_ROW = "| [`AuditLog.detail`]"
UNIVERSAL_CLAIMS = ("**keine** Klienten-PII in Einträgen", "keine Klienten-PII mehr")


def free_text_keys(source: str) -> list[str]:
    """Keys of ``AUDIT_DETAIL_KEYS`` declared below the free-text marker comment."""
    lines = source.splitlines()
    marker = next(i for i, line in enumerate(lines, start=1) if FREE_TEXT_MARKER in line)
    for node in ast.parse(source).body:
        target = getattr(node, "target", None)
        if isinstance(target, ast.Name) and target.id == "AUDIT_DETAIL_KEYS" and isinstance(node.value, ast.Dict):
            return [
                key.value
                for key in node.value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str) and key.lineno > marker
            ]
    raise AssertionError("AUDIT_DETAIL_KEYS not found")


def test_free_text_keys_reads_only_keys_below_the_marker() -> None:
    source = "\n".join(
        [
            "AUDIT_DETAIL_KEYS: dict[str, str] = {",
            '    "format": "slug",',
            f"    # -- {FREE_TEXT_MARKER} --",
            '    "purpose": "free text",',
            "}",
        ]
    )
    assert free_text_keys(source) == ["purpose"]


def test_policy_declares_free_text_keys() -> None:
    keys = free_text_keys(DETAIL_POLICY.read_text(encoding="utf-8"))
    assert {"recipient", "purpose"} <= set(keys)


def test_notes_make_no_universal_pii_claim() -> None:
    text = SECURITY_NOTES.read_text(encoding="utf-8")
    found = [claim for claim in UNIVERSAL_CLAIMS if claim in text]
    assert found == [], f"security-notes.md claims audit entries carry no client PII: {found}"


def test_audit_row_names_every_free_text_key() -> None:
    rows = [line for line in SECURITY_NOTES.read_text(encoding="utf-8").splitlines() if line.startswith(AUDIT_ROW)]
    assert len(rows) == 1, f"expected one AuditLog.detail row, found {len(rows)}"
    keys = free_text_keys(DETAIL_POLICY.read_text(encoding="utf-8"))
    missing = [key for key in keys if f"`{key}`" not in rows[0]]
    assert missing == [], f"security-notes.md AuditLog.detail row does not name free-text keys: {missing}"


CHECKLIST_URL = "#1597"


def _german_branch_section() -> str:
    text = SECURITY_NOTES.read_text(encoding="utf-8")
    return text.split("### Branch-Schutz und Maintainer-Nachweis", 1)[1].split("\n### ", 1)[0]


def _english_branch_section() -> str:
    text = (REPO_ROOT / "docs/en/security-notes.md").read_text(encoding="utf-8")
    assert "## Branch protection and maintainer evidence" in text
    return text.split("## Branch protection and maintainer evidence", 1)[1].split("\n## ", 1)[0]


def _table_row(section: str, ruleset: str) -> str:
    rows = [line for line in section.splitlines() if line.startswith(f"| `{ruleset}` ")]
    assert len(rows) == 1, f"expected one table row for {ruleset}, found {len(rows)}"
    return rows[0]


def test_branch_protection_keeps_the_private_dev_repository_boundary() -> None:
    section = _german_branch_section()
    assert (
        "Branch-Schutz/Rulesets stehen für das private Dev-Repo ohne GitHub Pro nicht zur Verfügung; "
        "Ersatz sind lokale Gates, signierte Tags und der Release-Dry-Run."
    ) in section


def test_branch_protection_describes_the_configured_app_rulesets() -> None:
    section = _german_branch_section()
    assert "`refs/heads/main`" in section
    integrity = _table_row(section, "main-integrity")
    assert "Keine Bypass-Liste" in integrity
    assert "Repository admin" not in integrity
    protection = _table_row(section, "main-protection")
    assert "Rolle „Repository admin“" in protection
    assert "Modus „Always“" in protection
    assert "Keines der beiden Rulesets enthält eine Pull-Request-Regel (`pull_request`)" in section
    assert "keine Restrict-updates-Regel (`update`)" in section


def test_branch_protection_names_the_evidence_boundary() -> None:
    section = _german_branch_section()
    assert f"[Maintainer-Checkliste #1597]({CHECKLIST_URL})" in section
    assert "Quellbaum belegt keine aktive GitHub-Einstellung" in section


def test_branch_protection_drops_the_superseded_target() -> None:
    section = _german_branch_section()
    assert "`main-pr`" not in section
    assert not re.search(r"benannte\w*\s+Release-Identität", section)
    assert "vorgesehen" not in section
    assert "Für das öffentliche app-Repo gilt ein Ruleset" not in section
    assert "nur Repo-Admin" not in section


def test_english_branch_protection_excerpt_mirrors_the_configured_rulesets() -> None:
    section = _english_branch_section()
    assert "private development repository without GitHub Pro" in section
    assert "local gates, signed tags and the release dry run" in section
    assert "`refs/heads/main`" in section
    assert "`main-integrity` (ID 26607015)" in section
    assert "without any bypass actors" in section
    assert "`main-protection` (ID 26602264)" in section
    assert "an “Always” bypass for the “Repository admin” role" in section
    assert "Neither ruleset has a pull request rule" in section
    assert f"[maintainer checklist #1597]({CHECKLIST_URL})" in section
    assert "does not prove active GitHub settings" in section


def test_english_branch_protection_excerpt_drops_the_superseded_target() -> None:
    section = _english_branch_section()
    assert "`main-pr`" not in section
    assert "named release identity" not in section
    assert "planned" not in section
