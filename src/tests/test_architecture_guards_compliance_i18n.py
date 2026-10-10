"""Reject untranslated card keyword literals (Refs #2530)."""

import ast
from pathlib import Path

import pytest

_FIELDS = {"label", "category", "message", "action_hint"}


def _raw_card_literals(source):
    findings = []
    for node in ast.walk(ast.parse(source)):
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
                    findings.append((keyword.arg, value.lineno))
                else:
                    if isinstance(value, ast.BinOp):
                        pending.append(value.left)
                    elif isinstance(value, ast.IfExp):
                        pending.extend((value.body, value.orelse))
                    elif isinstance(value, ast.BoolOp):
                        pending.extend(value.values)
    return findings


@pytest.mark.parametrize("expression", ['"Raw"', 'f"Raw {name}"', '"Raw %s" % name', '"Raw" if flag else _("Text")'])
@pytest.mark.parametrize("field", sorted(_FIELDS))
@pytest.mark.parametrize("constructor", ["ComplianceCheck", "compliance.ComplianceCheck"])
def test_guard_rejects_raw_card_literals(expression, field, constructor):
    assert _raw_card_literals(f"{constructor}({field}={expression})")


def test_guard_accepts_translated_text_and_category_keys():
    assert not _raw_card_literals(
        'ComplianceCheck(label=_("Text"), category=ComplianceCategory.SYSTEM, '
        'message=_("%(count)s Text") % {"count": count})'
    )


@pytest.mark.architecture
@pytest.mark.xfail(strict=True, reason="#2530: technical ClamAV command remains a raw literal")
def test_compliance_card_keywords_use_gettext():
    root = Path(__file__).resolve().parents[1] / "core/services/compliance"
    findings = {path.name: _raw_card_literals(path.read_text()) for path in sorted(root.glob("*.py"))}
    assert not {path: values for path, values in findings.items() if values}
