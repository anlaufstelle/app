"""Tests fuer ``scripts/build_test_matrix_index.py`` — Referenz-Erkennung (Refs #1839).

Seit den Familien-Unterpaketen (``src/tests/reporting/`` u. a.) darf die Matrix
Testdateien mit genau einem Unterpaket im Pfad referenzieren. Diese Tests
pinnen, dass solche Pfade als **Unit**-Referenzen zaehlen (nie als E2E), und
dass die bisherige Erkennung fuer flache und ``e2e/``-Pfade unveraendert ist.
"""

from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

# Meta-Test, der ``scripts/`` liest — im Mutmut-Subprozess fehlt der Pfad (#930).
pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "build_test_matrix_index.py"


@pytest.fixture(scope="module")
def index_module():
    spec = spec_from_file_location("build_test_matrix_index", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    # Dataclasses im Skript brauchen den Modul-Eintrag (``sys.modules[cls.__module__]``).
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("cell", "expected_path", "expected_name"),
    [
        ("`test_audit.py`", "", "test_audit.py"),
        ("`src/tests/test_rls.py`", "src/tests/", "test_rls.py"),
        ("`src/tests/e2e/test_dashboard.py`", "src/tests/e2e/", "test_dashboard.py"),
        (
            "`src/tests/reporting/test_reporting_views.py::TestDatasetExportView`",
            "src/tests/reporting/",
            "test_reporting_views.py",
        ),
    ],
)
def test_test_file_ref_matches_flat_e2e_and_subpackage_paths(index_module, cell, expected_path, expected_name):
    match = index_module.TEST_FILE_REF.search(cell)
    assert match is not None, cell
    assert (match.group("path") or "") == expected_path
    assert match.group("name") == expected_name


def test_test_file_ref_rejects_nested_subpackages(index_module):
    """Genau EIN Unterpaket — tiefere Pfade sind keine gueltige Matrix-Referenz."""
    assert index_module.TEST_FILE_REF.search("`src/tests/a/b/test_x.py`") is None


def test_subpackage_reference_counts_as_unit_not_e2e(index_module):
    """Ein expliziter ``src/tests/<familie>/``-Pfad zaehlt nie als E2E — selbst
    wenn derselbe Basename auch unter ``src/tests/e2e/`` existiert."""
    e2e_files = {"test_reporting_views.py", "test_dashboard.py"}
    cell = (
        "`src/tests/reporting/test_reporting_views.py::TestDatasetExportView`, "
        "`src/tests/test_reporting_views.py`, "
        "`src/tests/e2e/test_dashboard.py`, `test_reporting_views.py`"
    )
    found = index_module.cell_referenced_e2e_files(cell, e2e_files)
    # Nur der explizite e2e-Pfad und der nackte Basename mit E2E-Treffer zaehlen.
    assert found == {"test_dashboard.py", "test_reporting_views.py"}
    only_subpackage = "`src/tests/reporting/test_reporting_views.py`"
    assert index_module.cell_referenced_e2e_files(only_subpackage, e2e_files) == set()
