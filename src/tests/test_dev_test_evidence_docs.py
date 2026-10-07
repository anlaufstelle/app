"""Current test guidance must distinguish status from historical evidence."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture
ROOT = Path(__file__).resolve().parents[2]


def test_status_matrix_replaces_run_log_requirement():
    if not (ROOT / "docs" / "dev").exists():
        pytest.skip("docs/dev/ docs/testing/release-test-profiles.md docs/testing/run-template.md: public exclusions")
    status = ROOT / "docs/dev/test-status.md"
    assert status.is_file()
    for relative in [
        "docs/dev/release-checklist.md",
        "docs/testing/release-test-profiles.md",
        "docs/testing/run-template.md",
    ]:
        text = (ROOT / relative).read_text()
        assert "Run-Log-Pflicht" not in text
        assert "test-status.md" in text
    pointers = ROOT / "docs/testing/runs"
    assert {path.name for path in pointers.iterdir()} == {"README.md", "2026-05-25-major-release-v0.12.0.md"}
    for pointer in pointers.iterdir():
        text = pointer.read_text()
        assert "../../dev/run-logs-archive/" in text
        assert "test-status.md" in text
        assert "Run-Log-Pflicht" not in text
    archive = ROOT / "docs/dev/run-logs-archive"
    assert (archive / "2026-08-22-checkpoint-dev-v0.83.md").is_file()
    assert (archive / "2026-08-26-checkpoint-dev-v0.85.md").is_file()


def test_mutation_reference_names_existing_process_tree_regression():
    path = ROOT / "docs/testing/mutation-testing.md"
    if not path.exists():
        pytest.skip("docs/testing/mutation-testing.md and dev-ops/release/ are absent from public snapshots")
    text = path.read_text()
    assert "TestLeaseUeberlebtDenEnkel" not in text
    name = "TestProzessbaumWirdVollstaendigAbgeraeumt"
    assert name in text
    assert f"class {name}:" in (ROOT / "dev-ops/release/tests/test_run_mutmut_cleanup.py").read_text()
    assert "2026-08-19" in text
    assert "historisch" in text.lower()
