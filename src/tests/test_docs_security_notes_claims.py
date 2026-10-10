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


def test_branch_protection_contract_names_the_maintainer_evidence() -> None:
    text = SECURITY_NOTES.read_text(encoding="utf-8")
    assert (
        "Branch-Schutz/Rulesets stehen für das private Dev-Repo ohne GitHub Pro nicht zur Verfügung; "
        "Ersatz sind lokale Gates, signierte Tags und der Release-Dry-Run. "
        "Für das öffentliche app-Repo gilt ein Ruleset (kein Force-Push, keine Löschung)."
    ) in text
    assert "#1597" in text
