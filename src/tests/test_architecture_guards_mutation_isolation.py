"""Architecture-Guard — kein Import-Zeit-Zugriff auf mutmut-gestrippte Pfade.

Refs #1600. mutmut kopiert vor jedem Run nur die in ``[tool.mutmut] also_copy``
gelisteten Bäume nach ``mutants/`` — ``scripts/``, ``dev-ops/``, ``docs/`` &c.
fehlen dort. Greift ein ausgeliefertes Test-Modul (``src/tests/**/test_*.py``)
**auf Modulebene** (Import-Zeit) lesend auf einen solchen Pfad zu, wirft der
Import beim Sammeln im ``mutants/``-Tree einen ``FileNotFoundError`` — pytest
bricht das gesamte Test-Listing ab ("Failed to collect list of tests"), und der
komplette Mutation-Run zählt **null** Mutanten (alle "not checked"). Genau das
ist beim v0.22.0-Schnitt passiert (``test_translation_source_hash_guard.py`` lud
``scripts/check_translation_versions.py`` beim Import).

Der ``architecture``-Marker schützt NICHT: Marker-Filter (``-m "not
architecture"``) greifen erst **nach** dem Sammeln — der Import-Fehler schlägt
davor zu.

Zulässige Kapselung (dann ist der Zugriff für diesen Guard OK):

* ein Modul-Level ``pytest.skip(..., allow_module_level=True)`` **vor** dem
  Zugriff (Muster ``if not SCRIPT.exists(): pytest.skip(...)`` wie in
  ``test_translation_source_hash_guard.py``), oder
* ein ``try/except`` um den Zugriff.

Der Guard ist AST-basiert und betrachtet ausschließlich *Pfad-Ausdrücke*
(``REPO_ROOT / "scripts" / …`` bzw. ``Path("…")``-Joins) — bloße String-Literale
(URLs, Regexes) sind keine Pfade. Lazy konstruierte Pfad-Konstanten
(``SCRIPT = REPO_ROOT / "scripts" / "x.py"``) sind harmlos; erst der *Zugriff*
(``read_text``, ``exec_module``, ``subprocess`` …) auf Import-Ebene ist das
Problem.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Meta-Test auf dem Repo-Filesystem — wie die übrigen architecture-Guards im
# Mutmut-Subprozess deselektiert (#930). Scannt ``src/tests/`` (Teil des
# also_copy-Trees), greift also selbst NICHT auf gestrippte Pfade zu.
pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = REPO_ROOT / "src" / "tests"

# ``[tool.mutmut] also_copy`` (repo-relativ, POSIX). Alles NICHT unter diesen
# Präfixen fehlt im ``mutants/``-Tree.
_COPIED_PREFIXES = (
    "src/anlaufstelle",
    "src/manage.py",
    "src/core",
    "src/tests",
    "src/templates",
    "src/locale",
    "src/static",
    "pyproject.toml",
)
# Existenz-Proben werfen keinen ``FileNotFoundError`` — sie sind gerade der
# Baustein der ``if not SCRIPT.exists(): pytest.skip(...)``-Kapselung.
_SAFE_PROBE_METHODS = frozenset({"exists", "is_file", "is_dir", "is_symlink"})
# Namen, die per Konvention den Repo-Wurzel-Pfad tragen.
_ROOT_NAMES = frozenset({"REPO_ROOT", "ROOT", "REPO", "PROJECT_ROOT"})


def _rel_path(expr: ast.AST) -> str | None:
    """POSIX-Relativpfad eines ``/``-Join-Ausdrucks, sonst ``None``.

    Erkennt NUR echte Pfad-Ausdrücke: ``ast.BinOp``/``Div`` mit einer
    Repo-Wurzel-Basis (``REPO_ROOT``…), einer ``Path(__file__)…parents[n]``-Kette
    oder ``Path("literal")``. Ein einzelnes String-Literal ist bewusst KEIN Pfad.
    """
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div):
        left = _rel_component(expr.left)
        right = _rel_component(expr.right)
        if left is None or right is None:
            return None
        return "/".join(p for p in (left, right) if p)
    return None


def _rel_component(expr: ast.AST) -> str | None:
    """Relativpfad-Fragment eines einzelnen Join-Knotens, oder ``None``."""
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div):
        return _rel_path(expr)
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value.strip("/")
    if isinstance(expr, ast.Name):
        return "" if expr.id in _ROOT_NAMES else None
    if isinstance(expr, ast.Call):
        func = expr.func
        if isinstance(func, ast.Name) and func.id == "Path" and expr.args:
            first = expr.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                return first.value.strip("/")
            if isinstance(first, ast.Name) and first.id == "__file__":
                return ""
        return None
    if isinstance(expr, (ast.Subscript, ast.Attribute)):
        # z.B. ``Path(__file__).resolve().parents[2]`` → Repo-Wurzel-Basis.
        return ""
    return None


def _denotes_outside_path(expr: ast.AST) -> bool:
    rel = _rel_path(expr)
    if rel is None:
        return False
    rel = rel.replace("\\", "/").strip("/")
    if not rel:
        return False
    # Innerhalb eines kopierten Baums …
    under = any(rel == p or rel.startswith(p + "/") for p in _COPIED_PREFIXES)
    # … oder ein Vorfahr-Verzeichnis eines kopierten Baums (z.B. ``src`` selbst,
    #    das mutmut anlegt, um ``src/core`` aufzunehmen) — beides existiert im
    #    ``mutants/``-Tree. Nur wer WEDER darunter NOCH darüber liegt, ist strip.
    ancestor = any(p == rel or p.startswith(rel + "/") for p in _COPIED_PREFIXES)
    return not (under or ancestor)


def _outside_names(tree: ast.Module) -> set[str]:
    """Modul-Level-Namen, die an einen gestrippten Pfad gebunden sind."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and _denotes_outside_path(node.value):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    names.add(tgt.id)
    return names


def _dangerous_defs(tree: ast.Module, outside_names: set[str]) -> set[str]:
    """Modul-Level-Funktionen, deren Aufruf einen gestrippten Pfad berührt.

    Bis zum Fixpunkt: eine Funktion ist gefährlich, wenn ihr Rumpf einen
    Outside-Namen/-Pfad referenziert ODER eine bereits als gefährlich erkannte
    Funktion aufruft (Transitivität)."""
    funcs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    dangerous: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name, node in funcs.items():
            if name in dangerous:
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and sub.id in outside_names:
                    dangerous.add(name)
                    break
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id in dangerous:
                    dangerous.add(name)
                    break
                if isinstance(sub, ast.expr) and _denotes_outside_path(sub):
                    dangerous.add(name)
                    break
            if name in dangerous:
                changed = True
    return dangerous


_COMPOUND = (ast.If, ast.For, ast.While, ast.With, ast.Try, ast.AsyncFor, ast.AsyncWith)


def _header_exprs(stmt: ast.stmt) -> list[ast.expr]:
    """Ausdrücke, die der *Kopf* eines Compound-Statements auswertet."""
    if isinstance(stmt, (ast.If, ast.While)):
        return [stmt.test]
    if isinstance(stmt, (ast.For, ast.AsyncFor)):
        return [stmt.iter]
    if isinstance(stmt, (ast.With, ast.AsyncWith)):
        return [item.context_expr for item in stmt.items]
    return []


def _import_time_calls(tree: ast.Module) -> list[tuple[ast.Call, bool]]:
    """Alle Import-Zeit-``Call``-Knoten mit ``inside_try``-Flag.

    Steigt in Modul-Level-Compound-Bodies ab, aber NICHT in ``def``/``class``
    oder den ``if __name__ == "__main__"``-Block."""
    out: list[tuple[ast.Call, bool]] = []

    def visit(stmt: ast.stmt, inside_try: bool) -> None:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return
        if isinstance(stmt, ast.If):
            test = stmt.test
            if isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == "__name__":
                return
        if isinstance(stmt, _COMPOUND):
            for expr in _header_exprs(stmt):
                for node in ast.walk(expr):
                    if isinstance(node, ast.Call):
                        out.append((node, inside_try))
            if isinstance(stmt, ast.Try):
                for s in stmt.body:
                    visit(s, True)
                for handler in stmt.handlers:
                    for s in handler.body:
                        visit(s, True)
                for s in stmt.orelse:
                    visit(s, inside_try)
                for s in stmt.finalbody:
                    visit(s, inside_try)
            else:
                for s in stmt.body:
                    visit(s, inside_try)
                for s in getattr(stmt, "orelse", []):
                    visit(s, inside_try)
        else:
            for node in ast.walk(stmt):
                if isinstance(node, ast.Call):
                    out.append((node, inside_try))

    for stmt in tree.body:
        visit(stmt, False)
    return out


def _call_touches_outside(call: ast.Call, outside_names: set[str], dangerous_defs: set[str]) -> bool:
    # Existenz-Proben (``X.exists()`` …) sind ungefährlich.
    if isinstance(call.func, ast.Attribute) and call.func.attr in _SAFE_PROBE_METHODS:
        return False
    if isinstance(call.func, ast.Name) and call.func.id in dangerous_defs:
        return True
    for sub in ast.walk(call):
        if isinstance(sub, ast.Name) and sub.id in outside_names:
            return True
        if isinstance(sub, ast.expr) and sub is not call and _denotes_outside_path(sub):
            return True
    return False


def find_import_time_outside_access(source: str, filename: str = "<test>") -> list[tuple[int, str]]:
    """Liefert ``(lineno, hinweis)`` je ungekapseltem Import-Zeit-Zugriff.

    Ungekapselt = weder in ``try/except`` noch nach einem Modul-Level
    ``pytest.skip(..., allow_module_level=True)``."""
    tree = ast.parse(source, filename=filename)
    outside_names = _outside_names(tree)
    dangerous_defs = _dangerous_defs(tree, outside_names)

    skip_line: int | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "skip":
            for kw in node.keywords:
                if kw.arg == "allow_module_level" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    skip_line = node.lineno if skip_line is None else min(skip_line, node.lineno)

    findings: list[tuple[int, str]] = []
    for call, inside_try in _import_time_calls(tree):
        if not _call_touches_outside(call, outside_names, dangerous_defs):
            continue
        if inside_try:
            continue
        if skip_line is not None and skip_line <= call.lineno:
            continue
        findings.append((call.lineno, "Import-Zeit-Zugriff auf gestrippten Pfad ohne skip-/try-Kapselung"))
    return findings


# ── Guard gegen den echten ``src/tests/``-Baum ──────────────────────────────
def test_no_shipped_test_accesses_stripped_paths_at_import_time() -> None:
    """Kein ``src/tests/**/test_*.py`` darf auf Import-Ebene ungekapselt auf
    einen mutmut-gestrippten Pfad zugreifen — sonst bricht das Test-Listing im
    ``mutants/``-Tree und der Mutation-Run zählt null Mutanten (Refs #1600)."""
    offenders: list[str] = []
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        findings = find_import_time_outside_access(path.read_text(encoding="utf-8"), str(path))
        for lineno, hint in findings:
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {hint}")
    assert not offenders, (
        "Import-Zeit-Zugriff auf mutmut-gestrippte Pfade gefunden — kapsele ihn mit\n"
        "  if not <PFAD>.exists(): pytest.skip(..., allow_module_level=True)\n"
        "oder einem try/except (Muster: test_translation_source_hash_guard.py):\n  " + "\n  ".join(offenders)
    )


# ── Detektor-Kontrakt (pinnt das Verhalten des Guards selbst) ───────────────
_BAD_UNGUARDED = (
    "from pathlib import Path\n"
    "REPO_ROOT = Path(__file__).resolve().parents[2]\n"
    'SCRIPT = REPO_ROOT / "scripts" / "x.py"\n'
    "def _load():\n"
    "    return SCRIPT.read_text()\n"
    "DATA = _load()\n"
)
_BAD_DIRECT = (
    "from pathlib import Path\n"
    "REPO_ROOT = Path(__file__).resolve().parents[2]\n"
    'DATA = (REPO_ROOT / "scripts" / "x.py").read_text()\n'
)
_GOOD_SKIP = (
    "import pytest\n"
    "from pathlib import Path\n"
    "REPO_ROOT = Path(__file__).resolve().parents[2]\n"
    'SCRIPT = REPO_ROOT / "scripts" / "x.py"\n'
    "if not SCRIPT.exists():\n"
    '    pytest.skip("gestrippt", allow_module_level=True)\n'
    "def _load():\n"
    "    return SCRIPT.read_text()\n"
    "DATA = _load()\n"
)
_GOOD_TRY = (
    "from pathlib import Path\n"
    "REPO_ROOT = Path(__file__).resolve().parents[2]\n"
    'SCRIPT = REPO_ROOT / "scripts" / "x.py"\n'
    "try:\n"
    "    DATA = SCRIPT.read_text()\n"
    "except FileNotFoundError:\n"
    "    DATA = None\n"
)
_GOOD_LAZY_IN_FN = (
    "from pathlib import Path\n"
    "REPO_ROOT = Path(__file__).resolve().parents[2]\n"
    'SCRIPT = REPO_ROOT / "scripts" / "x.py"\n'
    "def test_reads():\n"
    "    assert SCRIPT.read_text()\n"
)
_GOOD_COPIED_TREE = (
    "from pathlib import Path\n"
    "REPO_ROOT = Path(__file__).resolve().parents[2]\n"
    'DATA = (REPO_ROOT / "src" / "core" / "models.py").read_text()\n'
)
_GOOD_URL_LITERAL = 'import re\nAPI = "/offline/api/sync"\nPAT = re.compile(r"^[A-Za-z0-9_-/]{5}$")\n'


class TestDetectorContract:
    """Pinnt die Kapselungs-Semantik des Guards (Refs #1600)."""

    def test_unguarded_module_level_call_is_flagged(self) -> None:
        assert find_import_time_outside_access(_BAD_UNGUARDED)

    def test_inline_module_level_access_is_flagged(self) -> None:
        assert find_import_time_outside_access(_BAD_DIRECT)

    def test_module_level_skip_guard_is_accepted(self) -> None:
        assert find_import_time_outside_access(_GOOD_SKIP) == []

    def test_try_except_capsule_is_accepted(self) -> None:
        assert find_import_time_outside_access(_GOOD_TRY) == []

    def test_access_inside_function_body_is_ignored(self) -> None:
        assert find_import_time_outside_access(_GOOD_LAZY_IN_FN) == []

    def test_path_inside_copied_tree_is_ignored(self) -> None:
        assert find_import_time_outside_access(_GOOD_COPIED_TREE) == []

    def test_bare_string_literals_are_not_paths(self) -> None:
        assert find_import_time_outside_access(_GOOD_URL_LITERAL) == []
