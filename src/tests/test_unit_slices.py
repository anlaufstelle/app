"""Exercise unit slicing and coverage validation through their command line."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture
ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/ci/test_slices.py"


def invoke(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)


def test_weighted_partition_is_complete_disjoint_and_deterministic(tmp_path):
    collection = tmp_path / "collected.txt"
    nodes = [f"src/tests/test_{letter}.py::test_value[{n}]" for letter in "abcdefg" for n in range(3)]
    collection.write_text("\n".join(nodes) + "\n21 tests collected\n")
    weights = tmp_path / "weights.json"
    weights.write_text(
        json.dumps(
            {
                f"src/tests/test_{letter}.py": weight
                for letter, weight in zip("abcdef", [90, 60, 40, 20, 10, 5], strict=True)
            }
        )
    )
    buckets = []
    for index in range(1, 4):
        args = ("--collection", collection, "--weights", weights, "--index", index)
        result = invoke(*args)
        assert result.returncode == 0, result.stderr
        assert invoke(*args).stdout == result.stdout
        buckets.append(result.stdout.splitlines())
    expected = {node.split("::")[0] for node in nodes}
    assert all(buckets)
    assert sum(map(len, buckets)) == len(expected)
    assert set().union(*map(set, buckets)) == expected
    assert all(set(left).isdisjoint(right) for i, left in enumerate(buckets) for right in buckets[i + 1 :])
    assert buckets[0] == ["src/tests/test_a.py"]


@pytest.mark.parametrize("mutation", ["missing", "duplicate"])
def test_partition_validation_rejects_corrupted_file_lists(tmp_path, mutation):
    collection = tmp_path / "collection.txt"
    collection.write_text("a.py::test_a\nb.py::test_b\nc.py::test_c\n")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps([["a.py"], ["b.py"], [] if mutation == "missing" else ["a.py", "c.py"]]))
    result = invoke("--collection", collection, "--validate", manifest)
    assert result.returncode == 2
    assert "partition" in result.stderr


def test_weights_include_all_duration_phases(tmp_path):
    durations = tmp_path / "durations.txt"
    durations.write_text(
        "0.50s setup src/tests/test_a.py::test_one\n"
        "1.25s call src/tests/test_a.py::test_one\n"
        "0.25s teardown src/tests/test_a.py::test_one\n"
        "2.00s call src/tests/test_b.py::test_two[p]\n"
    )
    output = tmp_path / "weights.json"
    result = invoke("--durations", durations, "--output", output)
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text()) == {"src/tests/test_a.py": 2.0, "src/tests/test_b.py": 2.0}


@pytest.mark.parametrize(
    "status, missing", [("success", False), ("failure", False), ("cancelled", False), ("success", True)]
)
def test_coverage_gate_requires_success_and_all_three_files(tmp_path, status, missing):
    for index in range(1, 4):
        directory = tmp_path / f"unit-coverage-{index}"
        directory.mkdir()
        if not (missing and index == 2):
            (directory / f".coverage.{index}").write_bytes(b"coverage")
    result = invoke("--coverage-dir", tmp_path, "--test-result", status)
    assert result.returncode == (0 if status == "success" and not missing else 2), result.stderr


def test_workflow_combines_coverage_after_all_slices():
    import yaml

    jobs = yaml.safe_load((ROOT / ".github/workflows/test.yml").read_text())["jobs"]
    assert jobs["test"]["strategy"]["matrix"]["slice"] == [1, 2, 3]
    assert jobs["test"]["strategy"]["fail-fast"] is False
    assert jobs["test"]["timeout-minutes"] == 35
    combine = jobs["coverage"]
    assert combine["needs"] == ["test"]
    assert combine["if"] == "always()"
    commands = "\n".join(step.get("run", "") for step in combine["steps"])
    assert "needs.test.result" in commands
    assert "--coverage-dir" in commands
    assert "coverage combine" in commands
    assert "coverage report --fail-under=96" in commands
    slice_commands = "\n".join(step.get("run", "") for step in jobs["test"]["steps"])
    assert "make test-unit-slice" in slice_commands
    recipe = subprocess.run(
        ["make", "-n", "test-unit-slice", "INDEX=1"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    slice_commands += recipe.stdout
    assert "--cov-fail-under=96" not in slice_commands
    assert "--cov-fail-under=0" in slice_commands


def test_real_collection_is_partitioned_exactly_once(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-m",
            "not e2e",
            "--ignore=src/tests/e2e",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    collection = tmp_path / "collection.txt"
    collection.write_text(result.stdout)
    nodes = [line for line in result.stdout.splitlines() if ".py::" in line]
    expected = {node.split("::")[0] for node in nodes}
    buckets = []
    for index in range(1, 4):
        sliced = invoke("--collection", collection, "--index", index)
        assert sliced.returncode == 0, sliced.stderr
        buckets.append(sliced.stdout.splitlines())
    assert all(buckets)
    assert sum(map(len, buckets)) == len(expected)
    assert set().union(*map(set, buckets)) == expected
    assert all(set(left).isdisjoint(right) for i, left in enumerate(buckets) for right in buckets[i + 1 :])
    assert sum(sum(node.split("::")[0] in bucket for node in nodes) for bucket in buckets) == len(nodes)


def test_combined_coverage_enforces_the_threshold_on_the_union(tmp_path):
    source = tmp_path / "sample.py"
    source.write_text(
        "import sys\n"
        "def first():\n    value = 1\n    return value\n"
        "def second():\n    value = 2\n    return value\n"
        "if sys.argv[1] == 'first':\n    first()\n"
        "else:\n    second()\n"
    )

    def coverage(*args):
        return subprocess.run(
            [sys.executable, "-m", "coverage", *args],
            cwd=tmp_path,
            # The enclosing pytest-cov run may use a different coverage basename.
            env={**os.environ, "COVERAGE_FILE": str(tmp_path / ".coverage")},
            text=True,
            capture_output=True,
            check=False,
        )

    for index, branch in enumerate(["first", "second", "first"], 1):
        directory = tmp_path / f"unit-coverage-{index}"
        directory.mkdir()
        data = directory / f".coverage.{index}"
        run = coverage("run", "--rcfile=/dev/null", f"--data-file={data}", str(source), branch)
        assert run.returncode == 0, run.stdout + run.stderr
        partial = coverage("report", "--rcfile=/dev/null", f"--data-file={data}", "--fail-under=96")
        assert partial.returncode == 2, partial.stdout + partial.stderr
    gate = invoke("--coverage-dir", tmp_path, "--test-result", "success")
    assert gate.returncode == 0, gate.stderr
    combined = coverage("combine", "--rcfile=/dev/null", *(str(tmp_path / f"unit-coverage-{i}") for i in range(1, 4)))
    assert combined.returncode == 0, combined.stdout + combined.stderr
    report = coverage("report", "--rcfile=/dev/null", "--fail-under=96")
    assert report.returncode == 0, report.stdout + report.stderr
    assert "100%" in report.stdout


def test_coverage_gate_does_not_assume_an_omitted_status_is_success(tmp_path):
    for index in range(1, 4):
        directory = tmp_path / f"unit-coverage-{index}"
        directory.mkdir()
        (directory / f".coverage.{index}").write_bytes(b"coverage")
    result = invoke("--coverage-dir", tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "did not succeed" in result.stderr
