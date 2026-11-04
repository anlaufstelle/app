"""Verdict logic and database-less behaviour of the nightly guards, without seeding or browsers."""

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture
ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "nightly_guards.py"


@pytest.fixture(scope="module")
def nightly():
    spec = spec_from_file_location("nightly_guards", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    # Dataclasses resolve postponed annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def result(nightly, name, status):
    return nightly.Result(name, status, 1.0, "detail")


def test_one_red_guard_turns_the_run_red(nightly):
    results = [result(nightly, "perf-medium", nightly.PASS), result(nightly, "demo", nightly.FAIL)]

    assert nightly.exit_code(results) == 1


def test_skipped_guards_stay_green_unless_requested(nightly):
    results = [result(nightly, "perf-medium", nightly.SKIP), result(nightly, "demo", nightly.PASS)]

    assert nightly.exit_code(results) == 0
    assert nightly.exit_code(results, fail_on_skip=True) == 1


def test_summary_names_every_guard_with_its_verdict(nightly):
    results = [result(nightly, "perf-large", nightly.FAIL), result(nightly, "docs-screens", nightly.PASS)]

    lines = nightly.summary_lines(results)

    assert lines[0] == "== nightly guards =="
    assert lines[1].startswith("perf-large    FAIL")
    assert lines[2].startswith("docs-screens  PASS")


def test_seed_stamp_round_trips_through_the_database_comment(nightly):
    stamp = nightly.SeedStamp("large", datetime(2026, 11, 1, 2, 0, tzinfo=UTC), "ff45ee7")

    parsed = nightly.parse_seed_stamp(stamp.render())

    assert parsed == stamp


def test_seed_stamp_with_code_hash_round_trips(nightly):
    stamp = nightly.SeedStamp("medium", datetime(2026, 11, 1, 2, 0, tzinfo=UTC), "ff45ee7", "0123456789ab")

    assert "code=0123456789ab" in stamp.render()
    assert nightly.parse_seed_stamp(stamp.render()) == stamp


def test_seed_with_other_or_missing_code_hash_is_not_reused(nightly):
    now = datetime(2026, 11, 8, tzinfo=UTC)
    seeded = now - timedelta(days=1)
    same = nightly.SeedStamp("medium", seeded, "", "aaaaaaaaaaaa")
    other = nightly.SeedStamp("medium", seeded, "", "bbbbbbbbbbbb")
    legacy = nightly.SeedStamp("medium", seeded)

    assert nightly.seed_is_fresh(same, "medium", 7, now, code="aaaaaaaaaaaa")
    assert not nightly.seed_is_fresh(other, "medium", 7, now, code="aaaaaaaaaaaa")
    assert not nightly.seed_is_fresh(legacy, "medium", 7, now, code="aaaaaaaaaaaa")


def test_seed_code_hash_follows_the_seed_modules(nightly, tmp_path, monkeypatch):
    package = tmp_path / "src" / "core" / "seed"
    package.mkdir(parents=True)
    command = tmp_path / "src" / "core" / "management" / "commands"
    command.mkdir(parents=True)
    (package / "events.py").write_text("A = 1\n")
    (command / "seed.py").write_text("B = 1\n")
    (tmp_path / "src" / "core" / "other.py").write_text("C = 1\n")
    monkeypatch.setattr(nightly, "ROOT", tmp_path)

    first = nightly.seed_code_hash()
    (tmp_path / "src" / "core" / "other.py").write_text("C = 2\n")
    assert nightly.seed_code_hash() == first  # unrelated modules do not matter
    (package / "events.py").write_text("A = 2\n")
    second = nightly.seed_code_hash()
    assert second != first
    (command / "seed.py").write_text("B = 2\n")
    assert nightly.seed_code_hash() not in {first, second}
    assert len(first) == 12


@pytest.mark.parametrize("comment", [None, "", "manual database", "nightly-seed scale=large seeded=yesterday"])
def test_foreign_or_broken_comments_are_no_stamp(nightly, comment):
    assert nightly.parse_seed_stamp(comment) is None


def test_seed_is_reused_only_for_the_same_scale_within_the_age_limit(nightly):
    now = datetime(2026, 11, 4, 2, 0, tzinfo=UTC)
    fresh = nightly.SeedStamp("large", now - timedelta(days=6, hours=23))
    stale = nightly.SeedStamp("large", now - timedelta(days=7, minutes=1))

    assert nightly.seed_is_fresh(fresh, "large", 7, now)
    assert not nightly.seed_is_fresh(stale, "large", 7, now)
    assert not nightly.seed_is_fresh(fresh, "medium", 7, now)
    assert not nightly.seed_is_fresh(None, "large", 7, now)


def test_pytest_detail_reads_the_last_result_line(nightly, tmp_path):
    log = tmp_path / "demo.log"
    log.write_text("collected 6 items\n...\n=========== 6 passed, 2 skipped in 61.52s (0:01:01) ===========\n")

    assert nightly.Runner.pytest_detail(log) == "6 passed, 2 skipped"


def test_perf_detail_names_slow_and_empty_cards(nightly, tmp_path):
    output = tmp_path / "perf-large.json"
    output.write_text(
        '{"limit": 2, "reports": ['
        '{"report": "general-key-figures", "median": 0.4, "passed": true, "record_numeric_visible": 5},'
        '{"report": "ban-overview", "median": 2.6, "passed": false, "record_numeric_visible": 0},'
        '{"report": "hybrid-pdf", "median": 1.1, "passed": true, "record_numeric_visible": 2}]}'
    )

    detail = nightly.Runner.perf_detail(output)

    assert detail.startswith("3 reports, slowest median 2.60 s (ban-overview)")
    assert "over 2 s: ban-overview" in detail
    assert "no published number: ban-overview" in detail


def run_script(tmp_path, *args):
    env = {**os.environ, "POSTGRES_HOST": "127.0.0.1", "POSTGRES_PORT": "1", "HOME": str(tmp_path)}
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--state-dir", str(tmp_path / "state"), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_guards_report_a_missing_database_as_skipped_and_still_summarise(tmp_path):
    completed = run_script(tmp_path, "perf-medium", "demo", "docs-screens")

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.count("skipped: no database") >= 3
    assert "== nightly guards ==" in completed.stdout
    for name in ("perf-medium", "demo", "docs-screens"):
        assert f"{name}" in completed.stdout.split("== nightly guards ==")[1]


def test_fail_on_skip_makes_a_database_less_run_red(tmp_path):
    completed = run_script(tmp_path, "--fail-on-skip", "perf-medium")

    assert completed.returncode == 1, completed.stdout + completed.stderr


def test_unknown_guard_is_rejected_before_anything_runs(tmp_path):
    completed = run_script(tmp_path, "perf-huge")

    assert completed.returncode == 2
    assert "unknown guard" in completed.stderr


def test_make_target_passes_the_parameters_through():
    # Without the outer make variables a nested make would print directory lines.
    env = {key: value for key, value in os.environ.items() if not key.startswith("MAKE")}
    completed = subprocess.run(
        ["make", "-n", "nightly-guards", "NIGHTLY_GUARDS=perf-medium docs-screens", "NIGHTLY_SEED_MAX_AGE_DAYS=3"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "scripts/nightly_guards.py" in completed.stdout
    assert "--seed-max-age-days 3" in completed.stdout
    assert completed.stdout.rstrip().endswith("perf-medium docs-screens")


def test_relative_interpreter_paths_become_absolute(nightly):
    """A relative PYTHON= value must survive the snapshot's other working directory."""
    assert nightly.resolve_python("bin/python") == str(ROOT / "bin/python")
    assert nightly.resolve_python("/usr/bin/python3") == "/usr/bin/python3"
    assert nightly.resolve_python(sys.executable) == sys.executable
    assert nightly.resolve_python("python3").startswith("/")


class FakeCursor:
    def __init__(self, existing):
        self.existing = existing
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, statement, params=None):
        self.statements.append(str(statement))

    def fetchone(self):
        return (1,) if self.existing else None


class FakeConnection:
    def __init__(self, existing):
        self.cursor_object = FakeCursor(existing)

    def cursor(self):
        return self.cursor_object


def test_missing_e2e_database_is_created_once(nightly):
    missing = FakeConnection(existing=False)
    present = FakeConnection(existing=True)

    assert nightly.ensure_database(missing, "anlaufstelle_e2e") is True
    assert any("CREATE DATABASE" in statement for statement in missing.cursor_object.statements)
    assert nightly.ensure_database(present, "anlaufstelle_e2e") is False
    assert not any("CREATE DATABASE" in statement for statement in present.cursor_object.statements)
