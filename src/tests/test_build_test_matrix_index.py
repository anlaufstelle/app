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


@pytest.mark.parametrize(
    "row",
    [
        "| Offline | facility_admin + fachkraft | C (2 Profile) |||",
        "|| facility_admin + fachkraft | C (2 Profile) |||",
    ],
)
def test_split_meta_row_preserves_empty_edge_cells(index_module, row):
    cells = index_module.split_meta_row(row)
    assert len(cells) == 5
    assert cells[0] == ("Offline" if row.startswith("| Offline") else "")
    assert cells[1:] == ["facility_admin + fachkraft", "C (2 Profile)", "", ""]


@pytest.mark.parametrize("placeholder", ["—", "", "`test_offline.py`"])
def test_parse_metadata_keeps_mobile_and_e2e_semantics(index_module, tmp_path, placeholder):
    matrix = tmp_path / "matrix.md"
    matrix.write_text(
        "### TC-ID: ENT-OFFL-14 — Offline\n\n"
        "| Bereich | Rolle | Browser | Mobile | E2E |\n"
        "|---|---|---|---|---|\n"
        f"| Offline | facility_admin + fachkraft | C (2 Profile) | {placeholder} | {placeholder} |\n",
        encoding="utf-8",
    )
    tests = index_module.parse_matrix(matrix, {"test_offline.py"})
    assert len(tests) == 1
    assert tests[0].mobile == placeholder
    assert tests[0].e2e == placeholder
    assert tests[0].has_e2e is bool(placeholder.startswith("`"))
    fallback = index_module.parse_matrix(matrix, set())
    assert fallback[0].has_e2e == tests[0].has_e2e


@pytest.fixture
def matrix_tree(index_module, tmp_path, monkeypatch):
    root = tmp_path
    matrix_dir = root / "docs" / "testing"
    matrix_dir.mkdir(parents=True)
    (root / "dev-ops").mkdir()
    monkeypatch.setattr(index_module, "ROOT", root)
    monkeypatch.setattr(index_module, "MATRIX_DIR", matrix_dir)
    monkeypatch.setattr(index_module, "SOURCE", matrix_dir / "manual-test-matrix.md")
    monkeypatch.setattr(index_module, "TARGET", matrix_dir / "test-matrix-index.md")
    monkeypatch.setattr(index_module, "discover_e2e_files", lambda: {"test_offline.py"})
    for section, filename in index_module.SECTION_FILES.items():
        tcid = {"A": "SMK-A-VORM-01", "B": "ENT-OFFL-14", "C": "AUD-01", "D": "DEV-01"}[section]
        heading = f"### TC-ID: {tcid}" if section == "B" else f"#### {tcid}"
        (matrix_dir / filename).write_text(
            f"{heading} — Offline\n\n"
            "| Bereich | Rolle | Browser | Mobile | E2E |\n"
            "|---|---|---|---|---|\n"
            "| Offline | facility_admin | C | — | — |\n",
            encoding="utf-8",
        )
    tests = index_module.parse_all_sections({"test_offline.py"})
    appendix = index_module.render_anhang_c(tests)
    appendix = appendix.replace(index_module.issue_link(909), "#909")
    index_module.SOURCE.write_text(
        f"# Matrix\n{index_module.ANHANG_C_START}\n{appendix}{index_module.ANHANG_C_END}\n",
        encoding="utf-8",
    )
    return root


@pytest.mark.parametrize("explicit", [True, False])
def test_public_check_accepts_excluded_index_but_checks_appendix(index_module, matrix_tree, explicit, capsys):
    if not explicit:
        (matrix_tree / "dev-ops").rmdir()
    args = ["--check", "--public"] if explicit else ["--check"]
    assert index_module.main(args) == 0
    assert not index_module.TARGET.exists()
    original = index_module.SOURCE.read_text(encoding="utf-8")
    index_module.SOURCE.write_text(original.replace("| A | 1 |", "| A | 9 |"), encoding="utf-8")
    assert index_module.main(args) == 1
    assert "STALE" in capsys.readouterr().err


def test_dev_check_still_requires_index(index_module, matrix_tree):
    assert index_module.main(["--check"]) == 1


def test_public_check_still_requires_all_sections(index_module, matrix_tree):
    (index_module.MATRIX_DIR / index_module.SECTION_FILES["B"]).unlink()
    with pytest.raises(SystemExit) as error:
        index_module.main(["--check", "--public"])
    assert error.value.code == 1


def test_public_generation_does_not_recreate_excluded_index(index_module, matrix_tree):
    assert index_module.main(["--public"]) == 0
    assert not index_module.TARGET.exists()
    assert index_module.main(["--check", "--public"]) == 0


def test_dev_generation_and_stale_index_detection(index_module, matrix_tree):
    assert index_module.main([]) == 0
    assert index_module.main(["--check"]) == 0
    original = index_module.TARGET.read_text(encoding="utf-8")
    index_module.TARGET.write_text(original.replace("ENT-OFFL-14", "ENT-OFFL-99"), encoding="utf-8")
    assert index_module.main(["--check"]) == 1


@pytest.mark.parametrize(
    ("heading", "expected"),
    [
        ("SMK-A-VORM-01 — Tagesstart", "smk-a-vorm-01--tagesstart"),
        (
            "TC-ID: ENT-AUTH-01 — Erfolgreicher Login mit korrektem Passwort",
            "tc-id-ent-auth-01--erfolgreicher-login-mit-korrektem-passwort",
        ),
        (
            "TC-ID: ENT-MFA-01 — TOTP-Setup: QR scannen, Test-Code bestätigen, Aktivierung",
            "tc-id-ent-mfa-01--totp-setup-qr-scannen-test-code-bestätigen-aktivierung",
        ),
        (
            "TC-ID: ENT-MFA-08 — `mfa_required=True` erzwingen (Setup-Redirect)",
            "tc-id-ent-mfa-08--mfa_requiredtrue-erzwingen-setup-redirect",
        ),
        (
            "TC-ID: ENT-CLIENT-15 — Laufender Löschantrag ist am Datensatz sichtbar (Badge, Banner, Zweitantrag)",
            "tc-id-ent-client-15--laufender-löschantrag-ist-am-datensatz-sichtbar-badge-banner-zweitantrag",
        ),
        (
            "TC-ID: ENT-CASE-12 — Mobile-Detail mit Tab-Navigation Episoden/Goals/Events",
            "tc-id-ent-case-12--mobile-detail-mit-tab-navigation-episodengoalsevents",
        ),
        ("Ä Ö Ü ß – — / : `a_b-c`", "ä-ö-ü-ß-----a_b-c"),
        ("  Two  spaces\tand\ncontrols  ", "two--spacesandcontrols"),
    ],
)
def test_github_slug_matches_confirmed_heading_anchors(index_module, heading, expected):
    """Expected matrix anchors come from GitHub's rendered v0.99.5 outline."""
    assert index_module.github_slug(heading) == expected


def test_github_slug_numbers_duplicates_and_suffix_collisions(index_module):
    seen = {}
    headings = ["Status-Legende", "Status-Legende", "Status-Legende-1", "Status-Legende", "Status-Legende-1"]
    assert [index_module.github_slug(heading, seen) for heading in headings] == [
        "status-legende",
        "status-legende-1",
        "status-legende-1-1",
        "status-legende-2",
        "status-legende-1-2",
    ]
    assert index_module.github_slug("Status-Legende", {}) == "status-legende"


@pytest.mark.parametrize("separator", ["—", "-"])
def test_case_links_use_actual_heading_and_document_duplicate_order(index_module, tmp_path, separator):
    heading = f"SMK-A-VORM-01 {separator} Tagesstart"
    matrix = tmp_path / "matrix.md"
    metadata = "\n| Bereich | Rolle | Browser | Mobile | E2E |\n|---|---|---|---|---|\n| Smoke | staff | C | — | — |\n"
    matrix.write_text(
        f"```markdown\n## {heading}\n```\n"
        f"~~~markdown\n## {heading}\n~~~\n"
        f"## {heading}\n"
        f"#### {heading}{metadata}"
        f"#### {heading}{metadata}",
        encoding="utf-8",
    )
    tests = index_module.parse_matrix(matrix, {"test_offline.py"})
    assert len(tests) == 2
    base = "smk-a-vorm-01--tagesstart" if separator == "—" else "smk-a-vorm-01---tagesstart"
    assert [index_module.tc_link(case) for case in tests] == [
        f"[`SMK-A-VORM-01`](manual-test-matrix-a.md#{base}-1)",
        f"[`SMK-A-VORM-01`](manual-test-matrix-a.md#{base}-2)",
    ]


def test_real_matrix_generation_and_case_links_are_current(index_module):
    assert index_module.main(["--check"]) == 0
    tests = index_module.parse_all_sections()
    rendered = index_module.render_index(tests)
    assert all(index_module.tc_link(case) in rendered for case in tests)
    assert index_module.check_stale(tests, public=not (index_module.ROOT / "dev-ops").is_dir()) is False


@pytest.mark.parametrize("suffix", ["", "\n# Preserved after\n"])
def test_index_regeneration_preserves_manual_content(index_module, tmp_path, suffix):
    target = tmp_path / "index.md"
    prefix = "# Preserved before\n\n"
    target.write_text(
        f"{prefix}{index_module.AUTOGEN_START}\nold\n{index_module.AUTOGEN_END}\n{suffix}",
        encoding="utf-8",
    )
    result = index_module.write_index("new\n", target)
    assert result.startswith(prefix)
    assert "\nold\n" not in result
    assert f"{index_module.AUTOGEN_START}\nnew\n{index_module.AUTOGEN_END}" in result
    assert result.endswith(suffix) if suffix else result.endswith(f"{index_module.AUTOGEN_END}\n")
    assert index_module.write_index("new\n", target) == result


@pytest.mark.parametrize("problem", ["no_header", "truncated", "too_few_cells"])
def test_invalid_metadata_cannot_create_case_links(index_module, tmp_path, problem):
    source = tmp_path / "matrix.md"
    heading = "#### SMK-A-TEST-01 — Example\n"
    table_header = "| Bereich | Rolle | Browser | Mobile | E2E |\n"
    content = {
        "no_header": heading,
        "truncated": heading + table_header,
        "too_few_cells": heading + table_header + "|---|---|---|---|---|\n| Smoke | staff | C |\n",
    }[problem]
    source.write_text(content, encoding="utf-8")
    if problem == "no_header":
        assert index_module.parse_matrix(source, {"test_offline.py"}) == []
    else:
        with pytest.raises(SystemExit) as error:
            index_module.parse_matrix(source, {"test_offline.py"})
        assert error.value.code == 1


@pytest.mark.parametrize("problem", ["missing_hub", "empty_sections", "wrong_section"])
def test_invalid_matrix_tree_fails_before_generating_links(index_module, matrix_tree, problem):
    if problem == "missing_hub":
        index_module.SOURCE.unlink()
    elif problem == "empty_sections":
        for filename in index_module.SECTION_FILES.values():
            (index_module.MATRIX_DIR / filename).write_text("# Empty\n", encoding="utf-8")
    else:
        source = index_module.MATRIX_DIR / index_module.SECTION_FILES["A"]
        source.write_text(source.read_text(encoding="utf-8").replace("SMK-A-VORM-01", "DEV-01"), encoding="utf-8")
    if problem == "wrong_section":
        with pytest.raises(SystemExit) as error:
            index_module.main([])
        assert error.value.code == 1
    else:
        assert index_module.main([]) == 1
    assert not index_module.TARGET.exists()


def test_empty_coverage_inventory_does_not_invent_case_links(index_module):
    assert "manual-test-matrix-a.md#" not in index_module.render_index([], set())
    assert "manual-test-matrix-b.md#" not in "\n".join(index_module.render_extras([]))


def test_missing_index_markers_are_stale_and_unmarked_appendix_is_preserved(index_module, matrix_tree):
    index_module.TARGET.write_text("# Missing markers\n", encoding="utf-8")
    tests = index_module.parse_all_sections()
    assert index_module.check_stale(tests) is True
    original = "# Unmarked appendix\n"
    index_module.SOURCE.write_text(original, encoding="utf-8")
    assert index_module.patch_anhang_c("generated\n", index_module.SOURCE) == (False, None)
    assert index_module.SOURCE.read_text(encoding="utf-8") == original
