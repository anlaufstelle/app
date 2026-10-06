"""The hourly demo reset runs the dashboard jobs after the seed (Refs #2413, #2415).

``seed --flush`` deletes the whole AuditLog, including the last-run markers the
compliance dashboard reads, and the demo host has no scheduler. The reset
therefore runs the marker-writing jobs once right after a successful seed, in
``deploy/crontab`` order, plus ``check_db_roles`` (Refs #2415).

Pinned here:

* the job list stays in sync with ``deploy/crontab`` — every cron job is either
  run or explicitly skipped, in the same order, and nothing else is run except
  ``check_db_roles``;
* ``enforce_retention`` is never run: on a fresh ``--scale=demo`` seed it
  soft-deletes seeded anonymous events (older than the 90-day retention);
* a failing job is reported to the journal like a failed reset and makes the
  script exit non-zero, without stopping the remaining jobs;
* a failed seed runs no job at all.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "dev-ops/deploy/demo-reseed.sh"
CRONTAB = ROOT / "deploy" / "crontab"

_EXPECTED_DEMO_JOBS = [
    "verify_audit_chain",
    "create_statistics_snapshots",
    "cleanup_orphan_storage_files",
    "detect_breaches",
    "refresh_statistics_view",
    "check_db_roles",
]


def _script_text() -> str:
    if not SCRIPT.exists():
        pytest.skip("dev-ops/, docker-compose.demo.yml, .env.demo: not in the public snapshot — dev repo only")
    return SCRIPT.read_text(encoding="utf-8")


def _bash_array(text: str, name: str) -> list[str]:
    match = re.search(rf"^{name}=\(\n(.*?)^\)", text, re.MULTILINE | re.DOTALL)
    assert match, f"array {name} not found in {SCRIPT.name}"
    words = []
    for line in match.group(1).splitlines():
        line = line.split("#", 1)[0].strip()
        words.extend(line.split())
    return words


def _crontab_jobs() -> list[str]:
    jobs = []
    for line in CRONTAB.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.search(r"python manage\.py (\S+)", line)
        assert match, f"unexpected crontab line: {line!r}"
        jobs.append(match.group(1))
    return jobs


class TestJobListMatchesCrontab:
    def test_demo_job_list_is_the_expected_one(self):
        assert _bash_array(_script_text(), "DEMO_JOBS") == _EXPECTED_DEMO_JOBS

    def test_every_cron_job_is_run_or_explicitly_skipped(self):
        text = _script_text()
        run = _bash_array(text, "DEMO_JOBS")
        skipped = _bash_array(text, "SKIPPED_CRON_JOBS")
        cron = _crontab_jobs()

        assert not set(run) & set(skipped)
        assert sorted(set(cron) - set(run) - set(skipped)) == []
        assert sorted(set(skipped) - set(cron)) == []

    def test_cron_jobs_keep_the_crontab_order(self):
        cron = _crontab_jobs()
        run = [job for job in _bash_array(_script_text(), "DEMO_JOBS") if job in cron]

        assert run == [job for job in cron if job in run]

    def test_only_check_db_roles_is_added_beyond_the_crontab(self):
        run = _bash_array(_script_text(), "DEMO_JOBS")

        assert [job for job in run if job not in _crontab_jobs()] == ["check_db_roles"]

    def test_retention_never_runs_after_the_reset(self):
        text = _script_text()

        assert "enforce_retention" not in _bash_array(text, "DEMO_JOBS")
        assert "enforce_retention" in _bash_array(text, "SKIPPED_CRON_JOBS")


def _run_reseed(tmp_path: Path, *, seed_exit: int = 0, fail_job: str = "") -> subprocess.CompletedProcess[str]:
    _script_text()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "docker-calls.log"
    journal = tmp_path / "logger-calls.log"
    (bin_dir / "docker").write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{calls}"\n'
        'for arg in "$@"; do\n'
        f'  if [ "$arg" = "seed" ]; then exit {seed_exit}; fi\n'
        f'  if [ -n "{fail_job}" ] && [ "$arg" = "{fail_job}" ]; then exit 3; fi\n'
        "done\n"
        "exit 0\n",
        encoding="utf-8",
    )
    (bin_dir / "logger").write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "{journal}"\n', encoding="utf-8")
    (bin_dir / "docker").chmod(0o755)
    (bin_dir / "logger").chmod(0o755)
    (tmp_path / ("." + "env.demo")).write_text("POSTGRES_DB=test\n", encoding="utf-8")
    environment = os.environ.copy()
    environment.update({"PATH": f"{bin_dir}:{environment['PATH']}", "ANLAUFSTELLE_DEMO_ROOT": str(tmp_path)})
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=environment,
        timeout=60,
    )


def _calls(tmp_path: Path) -> list[str]:
    path = tmp_path / "docker-calls.log"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _journal(tmp_path: Path) -> str:
    path = tmp_path / "logger-calls.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


class TestResetRunsTheJobs:
    def test_jobs_run_after_the_seed_in_order(self, tmp_path):
        result = _run_reseed(tmp_path)

        assert result.returncode == 0, result.stderr
        calls = _calls(tmp_path)
        assert calls[0].endswith("run --rm -T maintenance seed --flush --scale=demo")
        assert [call.rsplit(" ", 1)[-1] for call in calls[1:]] == _EXPECTED_DEMO_JOBS
        for call in calls[1:]:
            assert "-f docker-compose.demo.yml --env-file .env.demo run --rm -T maintenance" in call
        assert _journal(tmp_path) == ""

    def test_failed_seed_runs_no_job(self, tmp_path):
        result = _run_reseed(tmp_path, seed_exit=1)

        assert result.returncode != 0
        assert len(_calls(tmp_path)) == 1

    def test_failed_audit_chain_check_is_not_swallowed(self, tmp_path):
        result = _run_reseed(tmp_path, fail_job="verify_audit_chain")

        assert result.returncode != 0
        assert "verify_audit_chain" in result.stderr
        assert "exit 3" in result.stderr
        journal = _journal(tmp_path)
        assert "-p user.err" in journal
        assert "verify_audit_chain" in journal
        # The remaining jobs still refresh their markers.
        assert [call.rsplit(" ", 1)[-1] for call in _calls(tmp_path)[1:]] == _EXPECTED_DEMO_JOBS

    def test_failed_role_check_is_reported(self, tmp_path):
        result = _run_reseed(tmp_path, fail_job="check_db_roles")

        assert result.returncode != 0
        assert "check_db_roles" in _journal(tmp_path)
