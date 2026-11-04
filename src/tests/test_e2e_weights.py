"""Exercise duration conversion and weighted partitioning through their CLIs."""

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.architecture
ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "src/tests/e2e"
WEIGHTS = ROOT / "scripts/ci/e2e_weights.json"


def run(script, *args):
    return subprocess.run(
        [sys.executable, str(ROOT / script), *map(str, args)], capture_output=True, text=True, check=False
    )


def test_weighted_partition_and_positive_median_fallback(tmp_path):
    base = tmp_path / "specs"
    base.mkdir()
    for name in "abcd":
        (base / f"test_{name}.py").write_text("def test_one(): pass\n")
    weights = tmp_path / "weights.json"
    weights.write_text(json.dumps({"test_a.py": 6, "test_b.py": 2, "_meta": {}}))
    args = ("--base-dir", base, "--weights", weights, "--slices", 2)
    first = run("scripts/e2e_slice.py", *args, "--index", 1)
    assert first.returncode == 0, first.stderr
    assert first.stdout.splitlines() == [str(base / "test_a.py"), str(base / "test_b.py")]
    assert first.stdout == run("scripts/e2e_slice.py", *args, "--index", 1).stdout
    summary = run("scripts/e2e_slice.py", *args, "--summary")
    assert summary.returncode == 0, summary.stderr
    assert summary.stdout.count("8.00s") == 2
    assert "max/mean: 1.000" in summary.stdout
    assert "unmeasured: 2" in summary.stdout
    assert "test_c.py" in summary.stdout and "test_d.py" in summary.stdout
    weights.write_text('{"_meta": {}}')
    summary = run("scripts/e2e_slice.py", *args, "--summary")
    assert summary.returncode == 0
    assert "2.00s" in summary.stdout and "unmeasured: 4" in summary.stdout


def test_workflow_partition_union_and_weight_coverage():
    job = yaml.safe_load((ROOT / ".github/workflows/e2e.yml").read_text())["jobs"]["test-e2e"]
    count = job["env"]["E2E_SLICES"]
    assert job["strategy"]["matrix"]["slice"] == list(range(1, count + 1))
    files = sorted(
        p.relative_to(BASE).as_posix()
        for p in BASE.rglob("*.py")
        if p.name.startswith("test_") or p.name.endswith("_test.py")
    )
    flat = []
    for index in range(1, count + 1):
        result = run("scripts/e2e_slice.py", "--slices", count, "--index", index)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip()
        flat.extend(Path(p).relative_to("src/tests/e2e").as_posix() for p in result.stdout.splitlines())
    assert Counter(flat) == Counter(files)
    weights = json.loads(WEIGHTS.read_text())
    assert set(weights) - {"_meta"} <= set(files)
    summary = run("scripts/e2e_slice.py", "--slices", count, "--summary")
    missing = set(files) - set(weights)
    assert f"unmeasured: {len(missing)}" in summary.stdout
    for path in missing:
        assert path in summary.stdout
    validated = run("scripts/ci/e2e_weights.py", "--validate", WEIGHTS)
    assert validated.returncode == 0, validated.stderr


def test_converter_sums_junit_files_classnames_and_duration_phases(tmp_path):
    specs = sorted(BASE.glob("test_*.py"))[:2]
    a, b = [p.relative_to(BASE).as_posix() for p in specs]
    xml1 = tmp_path / "one.xml"
    xml1.write_text(
        f'<testsuites><testsuite><testcase file="src/tests/e2e/{a}" name="a" time="2.5"/>'
        f'<testcase classname="tests.e2e.{Path(b).stem}.TestGroup" name="b" time="3"/></testsuite></testsuites>'
    )
    xml2 = tmp_path / "two.xml"
    xml2.write_text(f'<testsuite><testcase classname="src.tests.e2e.{Path(a).stem}" name="c" time="1.5"/></testsuite>')
    durations = tmp_path / "durations.txt"
    durations.write_text(f"0.50s setup src/tests/e2e/{a}::test_one\n1.00s teardown src/tests/e2e/{b}::test_two\n")
    output = tmp_path / "weights.json"
    result = run(
        "scripts/ci/e2e_weights.py", "--from-junit", xml1, xml2, "--from-durations", durations, "--output", output
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(output.read_text())
    assert data[a] == 4.5 and data[b] == 4.0
    assert all(data["_meta"][key] for key in ("source", "measured_at", "head"))
    assert run("scripts/ci/e2e_weights.py", "--validate", output).returncode == 0
    durations_only = run("scripts/ci/e2e_weights.py", "--from-durations", durations, "--output", output)
    assert durations_only.returncode == 0, durations_only.stderr
    assert json.loads(output.read_text())[a] == 0.5


@pytest.mark.parametrize("value", [0, -1, "slow", True, float("nan"), float("inf")])
def test_invalid_weights_fail_without_a_partial_file_list(tmp_path, value):
    weights = tmp_path / "invalid.json"
    weights.write_text(json.dumps({sorted(BASE.glob("test_*.py"))[0].name: value, "_meta": {}}))
    result = run("scripts/ci/e2e_weights.py", "--validate", weights)
    assert result.returncode != 0
    result = run("scripts/e2e_slice.py", "--weights", weights, "--slices", 2, "--index", 1)
    assert result.returncode != 0 and result.stdout == ""


@pytest.mark.parametrize(
    "content",
    [
        "<testsuite/>",
        "<broken",
        '<testsuite><testcase classname="unknown.Test" time="1"/></testsuite>',
        '<testsuite><testcase file="../test_bad.py" time="1"/></testsuite>',
        '<testsuite><testcase file="src/tests/e2e/test_absent.py" time="1"/></testsuite>',
    ],
)
def test_converter_rejects_empty_malformed_and_unknown_inputs(tmp_path, content):
    source = tmp_path / "input.xml"
    source.write_text(content)
    output = tmp_path / "output.json"
    result = run("scripts/ci/e2e_weights.py", "--from-junit", source, "--output", output)
    assert result.returncode != 0
    assert not output.exists()


def test_workflow_uploads_per_slice_junit_even_on_failure():
    steps = yaml.safe_load((ROOT / ".github/workflows/e2e.yml").read_text())["jobs"]["test-e2e"]["steps"]
    pytest_step = next(step for step in steps if "python -m pytest $files" in step.get("run", ""))
    assert "--junitxml=e2e-durations-${{ matrix.slice }}.xml" in pytest_step["run"]
    upload = next(step for step in steps if step.get("with", {}).get("name") == "e2e-durations-${{ matrix.slice }}")
    assert upload["uses"].startswith("actions/upload-artifact@")
    assert upload["if"] == "always()"
    assert upload["with"]["path"] == "e2e-durations-${{ matrix.slice }}.xml"
    assert upload["with"]["if-no-files-found"] == "error"


def test_converter_rejects_support_modules(tmp_path):
    source = tmp_path / "support.xml"
    source.write_text('<testsuite><testcase file="src/tests/e2e/conftest.py" time="1"/></testsuite>')
    result = run("scripts/ci/e2e_weights.py", "--from-junit", source, "--output", tmp_path / "weights.json")
    assert result.returncode != 0


def test_slicer_ignores_stale_weights_but_validator_rejects_them(tmp_path):
    base = tmp_path / "specs"
    base.mkdir()
    for name in "abc":
        (base / f"test_{name}.py").write_text("def test_one(): pass\n")
    weights = tmp_path / "weights.json"
    weights.write_text(json.dumps({"test_a.py": 6, "test_b.py": 2, "test_deleted.py": 1000, "_meta": {}}))
    args = ("--base-dir", base, "--weights", weights, "--slices", 2)
    summary = run("scripts/e2e_slice.py", *args, "--summary")
    assert summary.returncode == 0, summary.stderr
    assert "stale: 1" in summary.stdout
    assert "unmeasured: 1" in summary.stdout
    assert "6.00s" in summary.stdout  # The stale weight must not affect the fallback median.
    selected = []
    for index in (1, 2):
        result = run("scripts/e2e_slice.py", *args, "--index", index)
        assert result.returncode == 0, result.stderr
        selected.extend(result.stdout.splitlines())
    assert Counter(selected) == Counter(str(path) for path in base.glob("test_*.py"))
    existing = sorted(BASE.glob("test_*.py"))[0].name
    weights.write_text(json.dumps({existing: 6, "test_deleted.py": 1000, "_meta": {}}))
    validated = run("scripts/ci/e2e_weights.py", "--validate", weights)
    assert validated.returncode != 0
    assert "Unknown spec path: test_deleted.py" in validated.stderr


CSV_HEADER = "test,slice,p1,p1_s,p2,p2_s,p3,p3_s\n"


def _csv_fixture(tmp_path, specs):
    """Build a small three-pass CSV; ``specs`` are real spec file names."""
    a, b = specs
    stem_a, stem_b = Path(a).stem, Path(b).stem
    rows = [
        # Median of three passed runs is 2.0 (not the mean 2.67 nor the maximum 4.0).
        f"src.tests.e2e.{stem_a}.TestOne::test_x[chromium],1,pass,1.0,pass,2.0,pass,5.0\n",
        # A failed run is ignored: median of the two passed runs (1.0, 3.0) = 2.0.
        f"src.tests.e2e.{stem_a}.TestOne::test_y[chromium],1,pass,1.0,fail,90.0,pass,3.0\n",
        # No passed run at all (all skipped): the maximum of the runs counts.
        f"src.tests.e2e.{stem_b}.TestTwo::test_z[chromium],2,skip,0.5,skip,2.5,skip,1.5\n",
        # Module-level test without a class, parametrised id containing a comma-free bracket.
        f"src.tests.e2e.{stem_b}::test_w[chromium-a],2,pass,4.0,pass,4.0,pass,4.0\n",
    ]
    path = tmp_path / "durations.csv"
    path.write_text(CSV_HEADER + "".join(rows))
    return path


def test_converter_reads_csv_with_median_of_passed_runs_and_scale(tmp_path):
    a, b = [p.name for p in sorted(BASE.glob("test_*.py"))[:2]]
    source = _csv_fixture(tmp_path, (a, b))
    output = tmp_path / "weights.json"
    head = "ff45ee7f650a958f02c9f7678ca223797213986d"
    result = run(
        "scripts/ci/e2e_weights.py",
        "--from-csv",
        source,
        "--scale",
        "0.5",
        "--measured-at",
        "2026-11-04",
        "--head",
        head,
        "--output",
        output,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(output.read_text())
    assert data[a] == pytest.approx((2.0 + 2.0) * 0.5)
    assert data[b] == pytest.approx((2.5 + 4.0) * 0.5)
    assert data["_meta"] == {"source": "durations.csv", "measured_at": "2026-11-04", "head": head, "scale": 0.5}
    assert run("scripts/ci/e2e_weights.py", "--validate", output).returncode == 0


def test_converter_scale_defaults_to_one_and_is_recorded(tmp_path):
    a, b = [p.name for p in sorted(BASE.glob("test_*.py"))[:2]]
    source = _csv_fixture(tmp_path, (a, b))
    output = tmp_path / "weights.json"
    result = run("scripts/ci/e2e_weights.py", "--from-csv", source, "--output", output)
    assert result.returncode == 0, result.stderr
    data = json.loads(output.read_text())
    assert data[a] == pytest.approx(4.0)
    assert data["_meta"]["scale"] == 1.0
    assert data["_meta"]["head"]  # falls back to the current commit


def test_converter_csv_combines_with_junit_input(tmp_path):
    a, b = [p.name for p in sorted(BASE.glob("test_*.py"))[:2]]
    source = _csv_fixture(tmp_path, (a, b))
    xml = tmp_path / "one.xml"
    xml.write_text(f'<testsuite><testcase file="src/tests/e2e/{a}" name="a" time="1.5"/></testsuite>')
    output = tmp_path / "weights.json"
    result = run("scripts/ci/e2e_weights.py", "--from-junit", xml, "--from-csv", source, "--output", output)
    assert result.returncode == 0, result.stderr
    data = json.loads(output.read_text())
    assert data[a] == pytest.approx(5.5)
    assert data["_meta"]["source"] == "one.xml, durations.csv"


@pytest.mark.parametrize("scale", ["0", "-1", "nan", "inf", "fast"])
def test_converter_rejects_invalid_scale(tmp_path, scale):
    a, b = [p.name for p in sorted(BASE.glob("test_*.py"))[:2]]
    source = _csv_fixture(tmp_path, (a, b))
    output = tmp_path / "weights.json"
    result = run("scripts/ci/e2e_weights.py", "--from-csv", source, "--scale", scale, "--output", output)
    assert result.returncode != 0
    assert "e2e_weights:" in result.stderr  # rejected by the converter, not by argument parsing
    assert not output.exists()


@pytest.mark.parametrize(
    "content",
    [
        "",
        CSV_HEADER,
        "name,slice\nx,1\n",
        CSV_HEADER + "src.tests.e2e.test_absent_spec.TestX::test_a,1,pass,1.0,pass,1.0,pass,1.0\n",
        CSV_HEADER + "src.tests.e2e.conftest.TestX::test_a,1,pass,1.0,pass,1.0,pass,1.0\n",
        CSV_HEADER + "src.tests.e2e.test_a11y_reach_and_contrast.TestX::test_a,1,pass,-1.0,pass,1.0,pass,1.0\n",
        CSV_HEADER + "src.tests.e2e.test_a11y_reach_and_contrast.TestX::test_a,1,pass,fast,pass,1.0,pass,1.0\n",
        CSV_HEADER + "src.tests.e2e.test_a11y_reach_and_contrast.TestX::test_a,1,,,,,,\n",
    ],
)
def test_converter_rejects_empty_malformed_and_unknown_csv(tmp_path, content):
    source = tmp_path / "durations.csv"
    source.write_text(content)
    output = tmp_path / "weights.json"
    result = run("scripts/ci/e2e_weights.py", "--from-csv", source, "--output", output)
    assert result.returncode != 0
    assert "e2e_weights:" in result.stderr  # rejected by the converter, not by argument parsing
    assert not output.exists()
