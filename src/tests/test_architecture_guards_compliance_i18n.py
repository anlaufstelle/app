"""Reject untranslated card keyword literals (Refs #2530)."""

import ast
from pathlib import Path

import pytest

_FIELDS = {"label", "category", "message", "action_hint"}

# Pure technical values without natural language. An entry matches one file, one
# keyword and the exact literal, so a changed command or any new raw text fails.
_ALLOWED_LITERALS = {
    (
        "clamav.py",
        "action_hint",
        "docker compose -f docker-compose.prod.yml exec clamav tail -n 30 /var/log/clamav/freshclam.log",
    ),
}


def _raw_card_literals(source):
    findings = []
    tree = ast.parse(source)
    # Names bound to text in the module, so ``message=text`` cannot hide a literal.
    assigned = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned.setdefault(target.id, []).append(node.value)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        constructor = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
        if constructor != "ComplianceCheck":
            continue
        for keyword in node.keywords:
            if keyword.arg not in _FIELDS:
                continue
            # Calls are handled by request regressions; literals nested in formatting
            # or conditional expressions remain visible to this guard.
            pending = [keyword.value]
            while pending:
                value = pending.pop()
                if isinstance(value, ast.Call):
                    continue
                if isinstance(value, ast.JoinedStr) or isinstance(value, ast.Constant) and isinstance(value.value, str):
                    text = value.value if isinstance(value, ast.Constant) else None
                    findings.append((keyword.arg, value.lineno, text))
                else:
                    if isinstance(value, ast.BinOp):
                        pending.append(value.left)
                    elif isinstance(value, ast.IfExp):
                        pending.extend((value.body, value.orelse))
                    elif isinstance(value, ast.BoolOp):
                        pending.extend(value.values)
                    elif isinstance(value, ast.Name):
                        pending.extend(assigned.get(value.id, ()))
    return findings


@pytest.mark.parametrize("expression", ['"Raw"', 'f"Raw {name}"', '"Raw %s" % name', '"Raw" if flag else _("Text")'])
@pytest.mark.parametrize("field", sorted(_FIELDS))
@pytest.mark.parametrize("constructor", ["ComplianceCheck", "compliance.ComplianceCheck"])
def test_guard_rejects_raw_card_literals(expression, field, constructor):
    assert _raw_card_literals(f"{constructor}({field}={expression})")


def test_guard_follows_names_bound_to_raw_text():
    source = 'text = "Raw"\nComplianceCheck(message=text)\n'
    assert [(f, t) for f, _l, t in _raw_card_literals(source)] == [("message", "Raw")]
    assert not _raw_card_literals('text = _("Text")\nComplianceCheck(message=text)\n')


def test_guard_accepts_translated_text_and_category_keys():
    assert not _raw_card_literals(
        'ComplianceCheck(label=_("Text"), category=ComplianceCategory.SYSTEM, '
        'message=_("%(count)s Text") % {"count": count})'
    )


def _unallowed_findings():
    root = Path(__file__).resolve().parents[1] / "core/services/compliance"
    findings = {}
    for path in sorted(root.glob("*.py")):
        findings[path.name] = [
            (field, line, text)
            for field, line, text in _raw_card_literals(path.read_text())
            if (path.name, field, text) not in _ALLOWED_LITERALS
        ]
    return findings


@pytest.mark.architecture
def test_compliance_card_keywords_use_gettext():
    assert not {path: values for path, values in _unallowed_findings().items() if values}


@pytest.mark.architecture
def test_allowlist_entries_still_match_a_literal():
    root = Path(__file__).resolve().parents[1] / "core/services/compliance"
    for name, field, text in _ALLOWED_LITERALS:
        assert (field, text) in {(f, t) for f, _line, t in _raw_card_literals((root / name).read_text())}


def test_allowlist_is_bound_to_file_and_exact_literal():
    command = next(iter(_ALLOWED_LITERALS))[2]
    source = f'ComplianceCheck(action_hint="{command} --extra")'
    assert [(f, t) for f, _l, t in _raw_card_literals(source)] == [("action_hint", f"{command} --extra")]
    assert ("backup.py", "action_hint", command) not in _ALLOWED_LITERALS
