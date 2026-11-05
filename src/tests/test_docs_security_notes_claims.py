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
import yaml

from tests._skip_profile import DEFAULT_PROFILE

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


def _checklist_reference(label: str) -> str:
    """The checklist reference as the docs of this tree carry it.

    The development tree links the ticket by its full URL. The release build
    drops that link and keeps the ticket number as plain text, ending the
    sentence; the public form is therefore pinned without the link.
    """
    if DEFAULT_PROFILE == "public":
        return f"{label} #1597."
    return f"[{label} #1597]({CHECKLIST_URL})"


# Required status checks of the app ruleset ``main-protection`` (ID 26602264),
# copied from the maintainer's read-only export of that ruleset.
REQUIRED_CHECKS = (
    "lint",
    "check",
    "typecheck",
    "lock-check",
    "audit",
    "licenses",
    "translation-versions",
    "translations-coverage",
    "test (1)",
    "test (2)",
    "test (3)",
    "coverage",
    *(f"E2E-Scheibe {n}" for n in range(1, 9)),
    "Analyze (javascript)",
    "Analyze (python)",
)
CHECK_WORKFLOWS = ("lint.yml", "test.yml", "e2e.yml", "codeql.yml")
MATRIX_PLACEHOLDER = re.compile(r"\$\{\{\s*matrix\.(\w+)\s*\}\}")


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


def _backticked(text: str) -> list[str]:
    return re.findall(r"`([^`]+)`", text)


def _workflow_check_names() -> set[str]:
    """Check-run names the shipped workflows produce (job ``name`` or ID, matrix expanded)."""
    names: set[str] = set()
    for workflow in CHECK_WORKFLOWS:
        jobs = yaml.safe_load((REPO_ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8"))["jobs"]
        for job_id, job in jobs.items():
            matrix = (job.get("strategy") or {}).get("matrix") or {}
            template = job.get("name")
            if not matrix:
                names.add(template or job_id)
                continue
            assert len(matrix) == 1, f"{workflow}:{job_id} uses a multi-key matrix"
            (values,) = matrix.values()
            for value in values:
                if template is None:
                    names.add(f"{job_id} ({value})")
                else:
                    names.add(MATRIX_PLACEHOLDER.sub(lambda m, v=value: str(v), template))
    return names


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
    for rule in ("`deletion`", "`non_fast_forward`"):
        assert rule in integrity
    assert "Keine Bypass-Liste; gilt auch für Admins" in integrity
    assert "Repository admin" not in integrity
    protection = _table_row(section, "main-protection")
    for rule in ("`deletion`", "`non_fast_forward`", "`required_linear_history`", "`required_status_checks`"):
        assert rule in protection
    assert "strikt: Branch muss aktuell sein" in protection
    assert "Rolle „Repository admin“" in protection
    assert "Modus „Always“" in protection
    assert "Keines der beiden Rulesets enthält eine Pull-Request-Regel (`pull_request`)" in section
    assert "keine Restrict-updates-Regel (`update`)" in section


def test_branch_protection_names_the_expected_bypass_fields() -> None:
    section = _german_branch_section()
    assert "bei `main-integrity` eine leere `bypass_actors`-Liste" in section
    assert (
        "bei `main-protection` genau ein Eintrag `RepositoryRole` mit `actor_id: 5` "
        "(Rolle „Repository admin“) und `bypass_mode: always`"
    ) in section


def test_branch_protection_explains_what_the_bypass_covers() -> None:
    section = _german_branch_section()
    assert (
        "An den Pflicht-Checks kommt der Release-Push nur über den Bypass vorbei, "
        "weil sie erst nach dem Push auf `main` laufen; "
        "die lineare Historie hält ein Fast-Forward-Push ohnehin ein."
    ) in section
    assert "die ein Fast-Forward-Push ohnehin einhält" not in section


def test_branch_protection_lists_exactly_the_required_checks() -> None:
    lines = [
        line
        for line in _german_branch_section().splitlines()
        if line.startswith("Erforderliche Status-Checks in `main-protection`")
    ]
    assert len(lines) == 1, f"expected one required-checks line, found {len(lines)}"
    assert f"({len(REQUIRED_CHECKS)}, alle aus GitHub Actions)" in lines[0]
    listed = [name for name in _backticked(lines[0]) if name != "main-protection"]
    assert sorted(listed) == sorted(REQUIRED_CHECKS)
    assert len(REQUIRED_CHECKS) == 22


def test_required_checks_are_produced_by_the_shipped_workflows() -> None:
    missing = sorted(set(REQUIRED_CHECKS) - _workflow_check_names())
    assert missing == [], f"required checks no shipped workflow job produces: {missing}"


def test_branch_protection_names_the_evidence_boundary() -> None:
    section = _german_branch_section()
    assert _checklist_reference("Maintainer-Checkliste") in section
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
    assert (
        "`main-integrity` (ID 26607015) restricts deletions and blocks force pushes "
        "without any bypass actors, also for admins."
    ) in section
    assert (
        "`main-protection` (ID 26602264) repeats both rules and adds required linear history "
        f"and {len(REQUIRED_CHECKS)} strict required status checks ("
    ) in section
    assert (
        "an “Always” bypass for the “Repository admin” role (`RepositoryRole`, `actor_id: 5`) as the only bypass actor"
    ) in section
    assert "Neither ruleset has a pull request rule" in section
    assert (
        "The release push gets past the required checks only through the bypass, "
        "because they run on `main` after the push; a fast-forward push keeps linear history anyway."
    ) in section
    assert _checklist_reference("maintainer checklist") in section
    assert "does not prove active GitHub settings" in section


def test_english_branch_protection_excerpt_lists_exactly_the_required_checks() -> None:
    section = _english_branch_section()
    opener = "strict required status checks ("
    assert opener in section, "English excerpt lacks the strict required-checks list"
    start = section.index(opener)
    assert "), with" in section[start:], "English required-checks list is not closed before the bypass clause"
    end = section.index("), with", start)
    assert sorted(_backticked(section[start:end])) == sorted(REQUIRED_CHECKS)


def test_english_branch_protection_excerpt_drops_the_superseded_target() -> None:
    section = _english_branch_section()
    assert "`main-pr`" not in section
    assert "named release identity" not in section
    assert "planned" not in section
