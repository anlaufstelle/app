"""Verdict logic and database-less behaviour of the nightly guards, without seeding or browsers."""

import argparse
import contextlib
import os
import re
import subprocess
import sys
import types
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


SEED_DETERMINING_FILES = [
    "src/core/seed/events.py",
    "src/core/management/commands/seed.py",
    "src/core/packages/default.yaml",
    "src/core/packages/loader.py",
    "src/core/services/facility_bootstrap.py",
    "src/core/services/dynamic_protection.py",
    "src/core/services/file_vault/vault.py",
    "src/core/services/events/fields.py",
    "src/core/demo.py",
    "src/core/constants.py",
]


@pytest.mark.parametrize("relative", SEED_DETERMINING_FILES)
def test_seed_code_hash_covers_everything_that_shapes_the_seed_data(nightly, tmp_path, monkeypatch, relative):
    for name in SEED_DETERMINING_FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("VALUE = 1\n")
    monkeypatch.setattr(nightly, "ROOT", tmp_path)
    before = nightly.seed_code_hash()

    (tmp_path / relative).write_text("VALUE = 2\n")

    assert nightly.seed_code_hash() != before


def test_every_seed_code_glob_matches_a_real_file(nightly):
    """A rename must not shrink the coverage of the hash unnoticed."""
    for pattern in nightly.SEED_CODE_GLOBS:
        assert list(ROOT.glob(pattern)), f"{pattern} matches nothing"


class MaintenanceConnectionStub:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def reuse_runner(nightly, tmp_path, monkeypatch, *, migrate_exit):
    args = argparse.Namespace(
        python=sys.executable, state_dir=str(tmp_path), seed_max_age_days=7, perf_limit=2, perf_runs=1
    )
    runner = nightly.Runner(args)
    fresh = nightly.SeedStamp("medium", datetime.now(UTC) - timedelta(days=1), "", nightly.seed_code_hash())
    commands = []

    def fake_run(command, log, **kwargs):
        commands.append(command)
        return migrate_exit if "migrate" in command else 0

    monkeypatch.setattr(nightly, "maintenance_connection", MaintenanceConnectionStub)
    monkeypatch.setattr(runner, "seed_stamp", lambda connection, database: fresh)
    monkeypatch.setattr(runner, "run", fake_run)
    monkeypatch.setattr(runner, "lock", contextlib.nullcontext)
    return runner, commands


def test_a_reused_seed_is_migrated_before_the_measurement(nightly, tmp_path, monkeypatch):
    runner, commands = reuse_runner(nightly, tmp_path, monkeypatch, migrate_exit=0)

    result = runner.perf("medium")

    assert "seed reused" in result.detail
    kinds = ["migrate" if "migrate" in command else "perf" for command in commands]
    assert kinds == ["migrate", "perf"]


def test_a_failing_migration_of_the_reused_seed_fails_the_guard_without_measuring(nightly, tmp_path, monkeypatch):
    runner, commands = reuse_runner(nightly, tmp_path, monkeypatch, migrate_exit=1)

    result = runner.perf("medium")

    assert result.status == nightly.FAIL and "migrate" in result.detail
    assert len(commands) == 1


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


def fake_strip_list(monkeypatch, paths=()):
    module = types.ModuleType("verify_release_test_guard")
    module.parse_stripped_paths_from_build_release = lambda path: list(paths)
    monkeypatch.setitem(sys.modules, "verify_release_test_guard", module)


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args], check=True)


def test_public_snapshot_holds_tracked_files_only_and_no_env_file(nightly, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / ".gitignore").write_text(".env\n")
    (repo / "tracked.py").write_text("X = 1\n")
    (repo / "stripped.py").write_text("Y = 1\n")
    git(repo, "add", ".gitignore", "tracked.py", "stripped.py")
    git(repo, "commit", "-q", "-m", "init")
    (repo / "untracked.py").write_text("Z = 1\n")  # not part of what a release would contain
    (repo / ".env").write_text("SECRET_KEY=do-not-copy\n")
    monkeypatch.setattr(nightly, "ROOT", repo)
    fake_strip_list(monkeypatch, ["stripped.py"])
    snapshot = tmp_path / "state" / "public-tree"

    stripped = nightly.Runner.build_public_snapshot(snapshot)

    assert stripped == 1
    assert (snapshot / "tracked.py").is_file()
    assert not (snapshot / "untracked.py").exists() and not (snapshot / "stripped.py").exists()
    assert not (snapshot / ".env").exists()
    assert "do-not-copy" not in "".join(p.read_text() for p in snapshot.rglob("*") if p.is_file())


def test_public_run_takes_its_environment_at_run_time_with_the_test_settings(nightly, tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("DJANGO_SETTINGS_MODULE=anlaufstelle.settings.prod\nFROM_DOTENV=yes\n")
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\nDJANGO_SETTINGS_MODULE = "anlaufstelle.settings.test"\n'
    )
    monkeypatch.setattr(nightly, "ROOT", tmp_path)
    monkeypatch.delenv("FROM_DOTENV", raising=False)
    monkeypatch.setenv("FROM_PROCESS", "yes")

    env = nightly.public_run_env(snapshot)

    assert env["DJANGO_SETTINGS_MODULE"] == "anlaufstelle.settings.test"  # not the prod value of .env
    assert env["FROM_DOTENV"] == "yes" and env["FROM_PROCESS"] == "yes"
    assert not (snapshot / ".env").exists()


PACKAGE_FILES = [
    "Makefile",
    "scripts/nightly_guards.py",
    "scripts/check_statistics_performance.py",
    "docs/dev/contributor-tools.md",
]


@pytest.mark.parametrize("relative", PACKAGE_FILES)
def test_shipped_files_name_no_internal_release_wave(relative):
    """Public files describe the failures they guard against, not the internal wave they happened in."""
    path = ROOT / relative
    if not path.exists():  # docs/dev/ is not part of the public tree
        return
    assert not re.search(r"[Ww]ave [A-Z]\b|Welle [A-Z]\b", path.read_text())
