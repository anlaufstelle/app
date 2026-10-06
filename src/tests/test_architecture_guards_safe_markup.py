"""Architecture guards — no unreviewed escaping bypass (Refs #2468).

Django escapes every template variable. Three constructs switch that off:
the ``|safe``/``|safeseq`` filters and ``{% autoescape off %}`` in templates,
and ``mark_safe()``/``SafeString`` in Python. Each of them is a place where
user-controlled text could reach the browser as markup. On ``/admin-mgmt/``
the CSP carries ``'unsafe-eval'`` and ``'unsafe-inline'`` for styles
(ADR-025), so an injection there is not caught by the policy.

The guards below allow such a construct only with an allowlist entry that
states why it is harmless. Keys are line-independent:

* templates: ``(path, construct)``, e.g. ``("registration/x.html", "autoescape off")``
* Python: ``(path, qualname)`` of the enclosing class/function, ``"<module>"``
  at module level.

Templates are parsed with Django's own ``Lexer`` and ``FilterExpression``
(string arguments such as ``default:"a|safe"`` are not mistaken for
filters; ``{% comment %}`` blocks are skipped). Python is parsed with ``ast``;
aliased imports (``from … import mark_safe as ms``) are followed. Dynamic
lookups (``getattr(module, "mark_safe")``) are out of reach of a static guard;
Ruff ``S308`` covers ``mark_safe`` a second time at lint level.

The scan functions take the root directory as a parameter. The probe tests
run against ``tmp_path`` only; the repository-wide check lists the files via
``git ls-files`` so untracked local files cannot trip it.
"""

from __future__ import annotations

import ast
import fnmatch
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from django.template.base import FilterExpression, Lexer, TokenType, kwarg_re
from django.template.exceptions import TemplateSyntaxError

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_SUBDIR = "src/templates"

UNSAFE_FILTERS = frozenset({"safe", "safeseq"})
AUTOESCAPE_OFF = "autoescape off"
UNSAFE_PY_NAMES = frozenset({"mark_safe", "SafeString"})

#: Templates that switch escaping off, with the reason it is harmless.
TEMPLATE_ALLOWLIST: dict[tuple[str, str], str] = {
    ("registration/invite_email.html", AUTOESCAPE_OFF): (
        "Plain-text mail body: core.services.security.invite renders it into "
        "send_mail(message=...) without html_message, so no HTML client "
        "interprets it; escaping would only turn '&' in the setup link into '&amp;'."
    ),
    ("registration/password_reset_email.html", AUTOESCAPE_OFF): (
        "Plain-text mail body of Django's PasswordResetForm (email_template_name, "
        "no html_email_template_name configured); escaping would break the reset link."
    ),
}

#: Python call sites of ``mark_safe``/``SafeString``, with the reason. Empty:
#: the application builds markup only via templates and ``format_html``.
PY_SAFE_ALLOWLIST: dict[tuple[str, str], str] = {}


@dataclass(frozen=True)
class Finding:
    relpath: str
    lineno: int
    key: str


# --- file lists ----------------------------------------------------------------


def tracked_files(root: Path, *pathspecs: str) -> list[Path]:
    """Files under version control (``git ls-files``), glob fallback without git."""
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-z", "--", *pathspecs],
            cwd=root,
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        proc = None
    if proc is not None and proc.returncode == 0:
        return sorted(root / rel for rel in proc.stdout.decode("utf-8").split("\0") if rel)
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and any(fnmatch.fnmatchcase(path.relative_to(root).as_posix(), spec) for spec in pathspecs)
    )


def production_python_files(root: Path) -> list[Path]:
    """All tracked Python files under ``src/`` except the test suite."""
    return [p for p in tracked_files(root, "src/*.py") if not p.relative_to(root).as_posix().startswith("src/tests/")]


# --- templates -----------------------------------------------------------------


class _FilterRecorder:
    """Stands in for ``django.template.base.Parser``: accepts every filter name."""

    def __init__(self) -> None:
        self.names: list[str] = []

    def find_filter(self, filter_name):
        self.names.append(filter_name)
        return _any_filter


def _any_filter(value, arg=None):  # pragma: no cover - never called, only inspected
    return value


def _filter_names(expression: str) -> list[str]:
    """Filter names of one filter expression, parsed by Django itself."""
    recorder = _FilterRecorder()
    try:
        FilterExpression(expression, recorder)
    except TemplateSyntaxError:
        # Tag keywords and operators (``as``, ``==``, ``in``) are no filter
        # expressions; a malformed expression fails at template compile time.
        return []
    return recorder.names


def scan_template_source(source: str, relpath: str) -> list[Finding]:
    findings: list[Finding] = []
    comment_depth = 0
    for token in Lexer(source).tokenize():
        if token.token_type is TokenType.BLOCK:
            bits = token.split_contents()
            if not bits:
                continue
            tag = bits[0]
            if tag == "comment":
                comment_depth += 1
                continue
            if tag == "endcomment":
                comment_depth = max(0, comment_depth - 1)
                continue
            if comment_depth:
                continue
            if tag == "autoescape" and bits[1:2] == ["off"]:
                findings.append(Finding(relpath, token.lineno, AUTOESCAPE_OFF))
                continue
            expressions = [f"value|{bits[1]}"] if tag == "filter" and len(bits) > 1 else []
            for bit in bits[1:]:
                match = kwarg_re.match(bit)
                expressions.append(match.group(2) if match and match.group(1) else bit)
        elif token.token_type is TokenType.VAR and not comment_depth:
            expressions = [token.contents]
        else:
            continue
        for expression in expressions:
            for name in _filter_names(expression):
                if name in UNSAFE_FILTERS:
                    findings.append(Finding(relpath, token.lineno, f"filter:{name}"))
    return findings


def scan_templates(root: Path, files: list[Path] | None = None) -> list[Finding]:
    """Escaping bypasses in all templates under ``root`` (paths relative to it)."""
    if files is None:
        files = sorted(p for p in root.rglob("*") if p.suffix in {".html", ".txt"} and p.is_file())
    findings: list[Finding] = []
    for path in files:
        relpath = path.relative_to(root).as_posix()
        findings.extend(scan_template_source(path.read_text(encoding="utf-8"), relpath))
    return findings


# --- Python --------------------------------------------------------------------


class _NameUseVisitor(ast.NodeVisitor):
    """Records references to ``names`` with the qualname of the enclosing definition.

    Decorators count for the object they decorate, so ``@csrf_exempt`` on
    ``def view`` and ``@method_decorator(csrf_exempt, ...)`` on ``class View``
    are keyed by ``view`` and ``View``.
    """

    def __init__(self, names: frozenset[str]) -> None:
        self.names = set(names)
        self.scope: list[str] = []
        self.hits: list[tuple[int, str]] = []

    def _qualname(self) -> str:
        return ".".join(self.scope) or "<module>"

    def _visit_definition(self, node) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_definition(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_definition(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_definition(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name in self.names and alias.asname:
                self.names.add(alias.asname)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in self.names:
            self.hits.append((node.lineno, self._qualname()))

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in self.names:
            self.hits.append((node.lineno, self._qualname()))
        self.generic_visit(node)


def scan_python_source(source: str, relpath: str, names: frozenset[str]) -> list[Finding]:
    visitor = _NameUseVisitor(names)
    visitor.visit(ast.parse(source, filename=relpath))
    return [Finding(relpath, lineno, qualname) for lineno, qualname in sorted(visitor.hits)]


def scan_python(root: Path, names: frozenset[str], files: list[Path] | None = None) -> list[Finding]:
    if files is None:
        files = sorted(p for p in root.rglob("*.py") if p.is_file())
    findings: list[Finding] = []
    for path in files:
        findings.extend(scan_python_source(path.read_text(encoding="utf-8"), path.relative_to(root).as_posix(), names))
    return findings


# --- evaluation ----------------------------------------------------------------


def violations(findings: list[Finding], allowlist: dict[tuple[str, str], str]) -> list[str]:
    return [f"{f.relpath}:{f.lineno} ({f.key})" for f in findings if (f.relpath, f.key) not in allowlist]


def stale_entries(findings: list[Finding], allowlist: dict[tuple[str, str], str]) -> list[tuple[str, str]]:
    used = {(f.relpath, f.key) for f in findings}
    return sorted(key for key in allowlist if key not in used)


def _repo_template_findings() -> list[Finding]:
    root = REPO_ROOT / TEMPLATES_SUBDIR
    files = tracked_files(REPO_ROOT, f"{TEMPLATES_SUBDIR}/*.html", f"{TEMPLATES_SUBDIR}/*.txt")
    return scan_templates(root, files)


def _repo_py_findings(names: frozenset[str]) -> list[Finding]:
    return scan_python(REPO_ROOT, names, production_python_files(REPO_ROOT))


# --- repository-wide checks ----------------------------------------------------


class TestNoUnreviewedSafeMarkup:
    def test_templates_do_not_bypass_escaping_outside_allowlist(self):
        found = violations(_repo_template_findings(), TEMPLATE_ALLOWLIST)
        assert not found, (
            "Template switches autoescaping off (|safe, |safeseq or {% autoescape off %}) "
            "without an allowlist entry. Escape the value instead, or add an entry to "
            "TEMPLATE_ALLOWLIST with the reason it cannot carry user input (Refs #2468).\n"
            f"Found: {found}"
        )

    def test_template_allowlist_has_no_stale_entries(self):
        assert not stale_entries(_repo_template_findings(), TEMPLATE_ALLOWLIST)

    def test_python_does_not_mark_strings_safe_outside_allowlist(self):
        found = violations(_repo_py_findings(UNSAFE_PY_NAMES), PY_SAFE_ALLOWLIST)
        assert not found, (
            "mark_safe()/SafeString outside the allowlist. Build markup with "
            "format_html() or a template, or add an entry to PY_SAFE_ALLOWLIST "
            f"with the reason (Refs #2468).\nFound: {found}"
        )

    def test_python_allowlist_has_no_stale_entries(self):
        assert not stale_entries(_repo_py_findings(UNSAFE_PY_NAMES), PY_SAFE_ALLOWLIST)

    def test_scanner_sees_the_real_template_tree(self):
        """Anti-vacuity: without its allowlist the real tree must be red."""
        assert violations(_repo_template_findings(), {}), "template scan found nothing — file list empty?"


# --- probes against tmp_path ---------------------------------------------------


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestTemplateScannerProbes:
    def test_safe_filter_without_allowlist_entry_is_a_violation(self, tmp_path):
        _write(tmp_path, "core/probe.html", "<p>{{ note|safe }}</p>\n")
        assert violations(scan_templates(tmp_path), TEMPLATE_ALLOWLIST) == ["core/probe.html:1 (filter:safe)"]

    def test_safeseq_in_chain_and_in_tag_arguments_is_found(self, tmp_path):
        _write(
            tmp_path,
            "probe.html",
            '{{ items|safeseq|join:", " }}\n{% with shown=note|lower|safe %}{{ shown }}{% endwith %}\n',
        )
        keys = [(f.lineno, f.key) for f in scan_templates(tmp_path)]
        assert keys == [(1, "filter:safeseq"), (2, "filter:safe")]

    def test_autoescape_off_is_found(self, tmp_path):
        _write(tmp_path, "mail.html", "{% autoescape off %}{{ link }}{% endautoescape %}")
        assert [f.key for f in scan_templates(tmp_path)] == [AUTOESCAPE_OFF]

    def test_autoescape_on_and_string_arguments_are_not_findings(self, tmp_path):
        _write(
            tmp_path,
            "ok.html",
            '{% autoescape on %}{{ v|default:"a|safe" }}{% endautoescape %}{% trans "x|safe" %}',
        )
        assert scan_templates(tmp_path) == []

    def test_comment_block_is_skipped(self, tmp_path):
        _write(tmp_path, "c.html", "{% comment %}{{ old|safe }}{% endcomment %}{{ new }}")
        assert scan_templates(tmp_path) == []

    def test_allowlisted_entry_passes_and_unused_entry_is_stale(self, tmp_path):
        _write(tmp_path, "a.html", "{{ x|safe }}")
        allow = {("a.html", "filter:safe"): "probe", ("gone.html", AUTOESCAPE_OFF): "probe"}
        findings = scan_templates(tmp_path)
        assert violations(findings, allow) == []
        assert stale_entries(findings, allow) == [("gone.html", AUTOESCAPE_OFF)]


class TestPythonSafeScannerProbes:
    def test_mark_safe_call_is_a_violation(self, tmp_path):
        _write(
            tmp_path,
            "app/views.py",
            "from django.utils.safestring import mark_safe\n\ndef render(x):\n    return mark_safe(x)\n",
        )
        found = violations(scan_python(tmp_path, UNSAFE_PY_NAMES), PY_SAFE_ALLOWLIST)
        assert found == ["app/views.py:4 (render)"]

    def test_aliased_import_and_attribute_access_are_found(self, tmp_path):
        _write(
            tmp_path,
            "m.py",
            "from django.utils.safestring import mark_safe as ms\n"
            "from django.utils import safestring\n"
            "class Widget:\n"
            "    def html(self, x):\n"
            "        return ms(x) + safestring.SafeString(x)\n",
        )
        keys = [(f.lineno, f.key) for f in scan_python(tmp_path, UNSAFE_PY_NAMES)]
        assert keys == [(5, "Widget.html"), (5, "Widget.html")]

    def test_strings_and_comments_are_not_findings(self, tmp_path):
        _write(tmp_path, "doc.py", '# mark_safe is forbidden\nNOTE = "mark_safe"\n')
        assert scan_python(tmp_path, UNSAFE_PY_NAMES) == []
