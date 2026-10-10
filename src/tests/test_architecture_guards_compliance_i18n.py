"""Reject untranslated card keyword literals (Refs #2530)."""

import ast
from pathlib import Path

import pytest

_FIELDS = {"label", "category", "message", "detail", "action_hint"}

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
    # Follow assignments and arguments passed to local card-building helpers.
    assigned = {}

    def bind(target, value):
        if isinstance(target, ast.Name):
            assigned.setdefault(target.id, []).append(value)
        elif isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)):
            for item, expression in zip(target.elts, value.elts, strict=False):
                bind(item, expression)

    helpers = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                bind(target, node.value)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in helpers:
            parameters = helpers[node.func.id].args
            for parameter, value in zip(parameters.posonlyargs + parameters.args, node.args, strict=False):
                assigned.setdefault(parameter.arg, []).append(value)
            for keyword in node.keywords:
                if keyword.arg:
                    assigned.setdefault(keyword.arg, []).append(keyword.value)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        constructor = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
        if constructor != "ComplianceCheck":
            continue
        for keyword in node.keywords:
            if keyword.arg not in _FIELDS:
                continue
            pending = [keyword.value]
            seen = set()
            while pending:
                value = pending.pop()
                if id(value) in seen:
                    continue
                seen.add(id(value))
                if isinstance(value, ast.Call):
                    name = value.func.id if isinstance(value.func, ast.Name) else getattr(value.func, "attr", None)
                    if name in {"_", "gettext", "gettext_lazy", "ngettext"}:
                        continue
                    if isinstance(value.func, ast.Attribute) and name in {"format", "join"}:
                        receiver = value.func.value
                        # Punctuation separators are technical formatting, not prose.
                        if (
                            name != "join"
                            or not isinstance(receiver, ast.Constant)
                            or any(character.isalpha() for character in str(receiver.value))
                        ):
                            pending.append(receiver)
                    pending.extend(value.args)
                    pending.extend(item.value for item in value.keywords)
                elif (
                    isinstance(value, ast.JoinedStr) or isinstance(value, ast.Constant) and isinstance(value.value, str)
                ):
                    text = value.value if isinstance(value, ast.Constant) else None
                    findings.append((keyword.arg, value.lineno, text))
                elif isinstance(value, ast.BinOp):
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


@pytest.mark.parametrize(
    "source",
    [
        'key, label = "k", "Raw"\nComplianceCheck(label=label)',
        'ComplianceCheck(detail="Raw text")',
        'ComplianceCheck(message="Raw {}".format(value))',
        'def helper(label):\n    return ComplianceCheck(label=label)\nhelper("Raw")',
        'text = "Raw"\nlabel = text\nComplianceCheck(label=label)',
        'ComplianceCheck(message=str("Raw"))',
        'ComplianceCheck(message="Raw".join(values))',
        'def helper(label):\n    return ComplianceCheck(label=label)\nhelper(label="Raw")',
        "text = other\nother = text\nComplianceCheck(label=text)",
    ],
)
def test_guard_tracks_raw_text_paths(source):
    if source.startswith("text = other"):
        assert not _raw_card_literals(source)
    else:
        assert _raw_card_literals(source)
