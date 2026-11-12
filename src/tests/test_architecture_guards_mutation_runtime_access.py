"""Architecture guard: tests that read repo files outside ``src/`` must be deselectable.

mutmut copies only the ``[tool.mutmut] also_copy`` trees into ``mutants/``;
``Makefile``, ``scripts/``, ``dev-ops/``, ``docs/`` and the like are missing
there. The stats run executes every test that the marker filter
(``not e2e and not slow and not architecture``) lets through. A test that
reads such a path at run time raises ``FileNotFoundError``; because the stats
run is fail-fast, one such test aborts it with ``failed to collect stats``.

The import-time half of this problem is covered by
``test_architecture_guards_mutation_isolation.py``. This guard covers the
run-time half: every test function that touches a path outside the copied
trees must carry ``@pytest.mark.architecture`` (on the function, its class, or
as module-level ``pytestmark``), or skip itself at run time when the path is
missing.
"""

from __future__ import annotations

import ast
from pathlib import PurePosixPath

import pytest

from tests.test_architecture_guards_mutation_isolation import (
    _ROOT_NAMES,
    _SAFE_PROBE_METHODS,
    TESTS_DIR,
    _rel_is_outside,
)

pytestmark = pytest.mark.architecture

#: Location of the tests tree relative to the repository root.
_TESTS_REL = "src/tests"


def _resolve(expr: ast.AST, here: PurePosixPath, bound: dict[str, str]) -> str | None:
    """Repo-relative POSIX path an expression denotes, or ``None`` if unknown.

    ``here`` is the repo-relative path of the test file, so that
    ``Path(__file__).resolve().parents[k]`` and ``.parent`` chains resolve to
    the real ancestor (``parents[1]`` of a top-level test is ``src``, not the root).
    """
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value.strip("/")
    if isinstance(expr, ast.Name):
        if expr.id in bound:
            return bound[expr.id]
        return "" if expr.id in _ROOT_NAMES else None
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div):
        left = _resolve(expr.left, here, bound)
        right = _resolve(expr.right, here, bound)
        if left is None or right is None:
            return None
        return "/".join(part for part in (left, right) if part)
    if isinstance(expr, ast.Attribute) and expr.attr == "parent":
        inner = _resolve(expr.value, here, bound)
        return None if inner is None else str(PurePosixPath(inner).parent).removeprefix(".")
    if isinstance(expr, ast.Subscript) and isinstance(expr.value, ast.Attribute) and expr.value.attr == "parents":
        base = _resolve(expr.value.value, here, bound)
        index = expr.slice
        if base is None or not (isinstance(index, ast.Constant) and isinstance(index.value, int)):
            return None
        node = PurePosixPath(base)
        for _ in range(index.value + 1):
            node = node.parent
        return str(node).removeprefix(".")
    if isinstance(expr, ast.Call):
        func = expr.func
        if isinstance(func, ast.Attribute) and func.attr in {"resolve", "absolute", "expanduser"} and not expr.args:
            return _resolve(func.value, here, bound)
        if isinstance(func, ast.Name) and func.id == "Path" and len(expr.args) == 1:
            arg = expr.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.startswith("/"):
                return None  # absolute path: not a repo file
            if isinstance(arg, ast.Name) and arg.id == "__file__":
                return str(here)
            return _resolve(arg, here, bound)
    return None


def _module_bindings(tree: ast.Module, here: PurePosixPath) -> dict[str, str]:
    bound: dict[str, str] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            # A plain string constant is not a path (messages, regexes, keys).
            rel = None if isinstance(stmt.value, ast.Constant) else _resolve(stmt.value, here, bound)
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    if rel is None:
                        bound.pop(target.id, None)
                    else:
                        bound[target.id] = rel
    return bound


def _touches_outside(func: ast.AST, here: PurePosixPath, bound: dict[str, str], dangerous: set[str]) -> bool:
    """Does the function body build a stripped path or call a helper that does?"""
    probed: set[int] = set()
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _SAFE_PROBE_METHODS
        ):
            probed.update(id(sub) for sub in ast.walk(node))
    for node in ast.walk(func):
        if id(node) in probed:
            continue
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in dangerous:
            return True
        if isinstance(node, (ast.BinOp, ast.Name)) or (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Path"
        ):
            rel = _resolve(node, here, bound)
            if rel and _rel_is_outside(rel):
                return True
    return False


def _dangerous_helpers(tree: ast.Module, here: PurePosixPath, bound: dict[str, str]) -> set[str]:
    funcs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    dangerous: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name, node in funcs.items():
            if name not in dangerous and _touches_outside(node, here, bound, dangerous):
                dangerous.add(name)
                changed = True
    return dangerous


def _mentions_architecture_marker(node: ast.AST) -> bool:
    return any(isinstance(sub, ast.Attribute) and sub.attr == "architecture" for sub in ast.walk(node))


def _module_is_marked(tree: ast.Module) -> bool:
    return any(
        isinstance(stmt, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in stmt.targets)
        and _mentions_architecture_marker(stmt.value)
        for stmt in tree.body
    )


def _skips_at_run_time(func: ast.AST) -> bool:
    return any(
        isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "skip"
        for sub in ast.walk(func)
    )


def find_unmarked_outside_readers(
    source: str, filename: str = "<test>", rel: str = f"{_TESTS_REL}/test_probe.py"
) -> list[tuple[int, str]]:
    """Return ``(lineno, test name)`` for each unprotected test touching a stripped path."""
    tree = ast.parse(source, filename=filename)
    if _module_is_marked(tree):
        return []
    here = PurePosixPath(rel)
    bound = _module_bindings(tree, here)
    dangerous = _dangerous_helpers(tree, here, bound)
    findings: list[tuple[int, str]] = []

    def check(func: ast.FunctionDef | ast.AsyncFunctionDef, class_marked: bool) -> None:
        if not func.name.startswith("test") or class_marked:
            return
        if any(_mentions_architecture_marker(d) for d in func.decorator_list) or _skips_at_run_time(func):
            return
        if _touches_outside(func, here, bound, dangerous):
            findings.append((func.lineno, func.name))

    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            check(stmt, False)
        elif isinstance(stmt, ast.ClassDef):
            marked = any(_mentions_architecture_marker(d) for d in stmt.decorator_list)
            for member in stmt.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    check(member, marked)
    return findings


def test_no_test_reads_stripped_paths_without_the_architecture_marker() -> None:
    offenders: list[str] = []
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        if "e2e" in path.relative_to(TESTS_DIR).parts:
            continue
        rel = f"{_TESTS_REL}/{path.relative_to(TESTS_DIR).as_posix()}"
        for lineno, name in find_unmarked_outside_readers(path.read_text(encoding="utf-8"), str(path), rel):
            offenders.append(f"{rel}:{lineno} {name}")
    assert not offenders, (
        "These tests read files outside the mutmut also_copy trees without "
        "@pytest.mark.architecture (they would abort the mutation stats run):\n" + "\n".join(offenders)
    )


class TestDetectorContract:
    READER = (
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parents[2]\n"
        "def helper():\n    return (ROOT / 'Makefile').read_text()\n"
    )

    def test_inline_makefile_read_is_flagged(self) -> None:
        src = self.READER + "def test_x():\n    helper()\n"
        assert [n for _, n in find_unmarked_outside_readers(src)] == ["test_x"]

    def test_function_marker_is_accepted(self) -> None:
        src = "import pytest\n" + self.READER + "@pytest.mark.architecture\ndef test_x():\n    helper()\n"
        assert find_unmarked_outside_readers(src) == []

    def test_module_marker_is_accepted(self) -> None:
        src = "import pytest\npytestmark = pytest.mark.architecture\n" + self.READER + "def test_x():\n    helper()\n"
        assert find_unmarked_outside_readers(src) == []

    def test_class_marker_is_accepted(self) -> None:
        src = (
            "import pytest\n"
            + self.READER
            + "@pytest.mark.architecture\nclass TestA:\n    def test_x(self):\n        helper()\n"
        )
        assert find_unmarked_outside_readers(src) == []

    def test_run_time_skip_is_accepted(self) -> None:
        src = "import pytest\n" + self.READER + "def test_x():\n    pytest.skip('absent')\n    helper()\n"
        assert find_unmarked_outside_readers(src) == []

    def test_parents_index_one_resolves_to_src(self) -> None:
        src = (
            "from pathlib import Path\nTEMPLATES = Path(__file__).resolve().parents[1] / 'templates'\n"
            "def test_x():\n    TEMPLATES.read_text()\n"
        )
        assert find_unmarked_outside_readers(src) == []

    def test_absolute_paths_are_not_repo_files(self) -> None:
        src = "from pathlib import Path\ndef test_x():\n    Path('/app') / 'media'\n"
        assert find_unmarked_outside_readers(src) == []

    def test_reads_inside_the_copied_trees_are_ignored(self) -> None:
        src = (
            "from pathlib import Path\nROOT = Path(__file__).resolve().parents[2]\n"
            "def test_x():\n    (ROOT / 'src' / 'core' / 'a.py').read_text()\n"
        )
        assert find_unmarked_outside_readers(src) == []
