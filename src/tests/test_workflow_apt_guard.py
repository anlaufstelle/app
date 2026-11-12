"""Bound every apt invocation of the GitHub workflows so a mirror outage cannot eat the step limit.

Background: the runner mirror list names the Azure mirror first. When it only
resolves badly (about 20 s per name-resolution attempt) apt used to burn the
whole 5-minute step limit on it before it ever tried the fallback mirror. The
workflows therefore install packages only through ``scripts/ci/apt_install.sh``
(skips apt when the packages are present, small retry budget, hard failure on
a partial outage), and this guard keeps the budget arithmetic honest.
"""

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = ROOT / ".github/workflows"
HELPER = ROOT / "scripts/ci/apt_install.sh"
HELPER_CALL = "scripts/ci/apt_install.sh"

# Observed in the failing runs: one failed name resolution takes about 20 s.
RESOLVE_ATTEMPT_SECONDS = 20
# noble, noble-updates, noble-backports, noble-security: one InRelease each.
INRELEASE_FILES = 4
# gettext + libharfbuzz-subset0 and one dependency each, at most.
MAX_DEB_FILES = 3
# A dead first mirror may use at most this share of the step limit; the rest
# has to be enough for the working fallback mirror to deliver the lists/debs.
MAX_DEAD_MIRROR_SHARE = 0.6

PLAYWRIGHT_APT = re.compile(r"\bplaywright\s+(?:install-deps\b|install\b[^\n;&|]*--with-deps\b)")


def _steps():
    workflows = sorted(WORKFLOW_DIR.glob("*.yml"))
    assert workflows
    for path in workflows:
        workflow = yaml.safe_load(path.read_text())
        for job_name, job in workflow.get("jobs", {}).items():
            steps = job.get("steps", [])
            for index, step in enumerate(steps):
                yield path.name, job_name, steps, index, step


def _helper_option(name: str) -> int:
    match = re.search(rf"-o {re.escape(name)}=(\d+)", HELPER.read_text())
    assert match, f"{name} is not set in the helper"
    return int(match.group(1))


def dead_mirror_seconds(*, retries: int, timeout: int, files: int, resolve: int = RESOLVE_ATTEMPT_SECONDS) -> int:
    """Time a dead first mirror costs for ``files`` files before apt moves on to the next mirror.

    Every file is tried ``retries + 1`` times on the dead mirror. One attempt ends at the first of
    two limits: the name resolution (libc resolver, ``resolve`` seconds, apt's ``Timeout`` does not
    apply to it) or apt's own network timeout (connect/stall). The slower one bounds the attempt, so
    the retry count multiplies the resolution time, and ``Timeout`` only matters once it exceeds it.
    """
    return files * (retries + 1) * max(resolve, timeout)


@pytest.mark.architecture
def test_workflows_install_packages_only_through_the_bounded_helper():
    calls = 0
    for name, job, _steps_of_job, _index, step in _steps():
        run = step.get("run", "")
        assert "apt-get" not in run and "apt install" not in run, (name, job, step)
        assert "apt-mirrors" not in run, (name, job, "do not rewrite the runner's mirror list")
        if PLAYWRIGHT_APT.search(run) or HELPER_CALL in run:
            assert 0 < step.get("timeout-minutes", 0) <= 10, (name, job, step)
        if HELPER_CALL in run and "--configure-only" not in run:
            calls += 1
            assert re.search(rf"{re.escape(HELPER_CALL)}\s+[a-z0-9]", run), (name, job, step)
    assert calls >= 3  # lint (gettext), test (gettext + harfbuzz), e2e (harfbuzz)


@pytest.mark.architecture
def test_playwright_system_deps_run_with_the_same_apt_budget():
    """``playwright install --with-deps`` runs its own apt-get; it only obeys apt.conf.d."""
    seen = 0
    for name, job, steps, index, step in _steps():
        if not PLAYWRIGHT_APT.search(step.get("run", "")):
            continue
        seen += 1
        before = [s.get("run", "") for s in steps[:index]]
        assert any(f"{HELPER_CALL} --configure-only" in run for run in before), (name, job, step)
    assert seen >= 2


@pytest.mark.architecture
def test_dead_mirror_cost_is_bounded_by_retries_times_resolution_until_the_timeout_exceeds_it():
    assert dead_mirror_seconds(retries=1, timeout=15, files=4) == 4 * 2 * 20
    assert dead_mirror_seconds(retries=0, timeout=15, files=4) == 4 * 20
    assert dead_mirror_seconds(retries=1, timeout=60, files=1) == 2 * 60  # a long timeout takes over


@pytest.mark.architecture
def test_dead_first_mirror_leaves_room_for_the_fallback_mirror_within_the_step_limit():
    retries = _helper_option("Acquire::Retries")
    timeout = max(_helper_option("Acquire::http::Timeout"), _helper_option("Acquire::https::Timeout"))
    dead_mirror = dead_mirror_seconds(retries=retries, timeout=timeout, files=INRELEASE_FILES + MAX_DEB_FILES)
    checked = 0
    for name, job, _steps_of_job, _index, step in _steps():
        run = step.get("run", "")
        if HELPER_CALL not in run or "--configure-only" in run:
            continue
        checked += 1
        limit = step["timeout-minutes"] * 60
        assert dead_mirror <= MAX_DEAD_MIRROR_SHARE * limit, (name, job, dead_mirror, limit)
    assert checked >= 3


@pytest.mark.architecture
def test_helper_fails_on_partial_outage_and_installs_without_recommends():
    text = HELPER.read_text()
    assert re.search(r"update\b.*--error-on=any|--error-on=any.*\bupdate\b", text)
    assert "--no-install-recommends" in text
    assert "apt-mirrors" not in re.sub(r"(?m)^\s*#.*$", "", text)  # no rewriting of the runner's mirror list
    assert HELPER.stat().st_mode & stat.S_IXUSR


def _fake_bin(tmp_path: Path, *, installed: set[str], update_exit: int = 0) -> tuple[Path, Path]:
    """PATH with a recording apt-get and a dpkg-query that knows ``installed``."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    (bin_dir / "apt-get").write_text(
        '#!/bin/bash\necho "apt-get $*" >> "$APT_FAKE_LOG"\n'
        f'case " $* " in *" update "*) exit {update_exit};; esac\nexit 0\n'
    )
    known = "|".join(sorted(installed)) or "__none__"
    (bin_dir / "dpkg-query").write_text(
        f'#!/bin/bash\ncase "${{@: -1}}" in {known}) printf "install ok installed"; exit 0;; esac\nexit 1\n'
    )
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    return bin_dir, log


def _run_helper(tmp_path: Path, args: list[str], *, installed: set[str], update_exit: int = 0):
    bin_dir, log = _fake_bin(tmp_path, installed=installed, update_exit=update_exit)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "APT_FAKE_LOG": str(log),
        "APT_INSTALL_SUDO": "",
        "APT_CONF_DIR": str(tmp_path / "apt.conf.d"),
        "HOME": str(tmp_path),
    }
    result = subprocess.run(
        ["bash", str(HELPER), *args], env=env, capture_output=True, text=True, check=False, cwd=tmp_path
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return result, calls


@pytest.mark.architecture
def test_helper_skips_apt_when_all_packages_are_present(tmp_path):
    both = {"gettext", "libharfbuzz-subset0"}
    result, calls = _run_helper(tmp_path, sorted(both), installed=both)
    assert result.returncode == 0, result.stderr
    assert calls == []


@pytest.mark.architecture
def test_helper_updates_then_installs_only_missing_packages_with_bounded_options(tmp_path):
    result, calls = _run_helper(tmp_path, ["gettext", "libharfbuzz-subset0"], installed={"gettext"})
    assert result.returncode == 0, result.stderr
    assert len(calls) == 2
    update, install = calls
    for call in calls:
        for option in ("Acquire::Retries=1", "Acquire::http::Timeout=15", "Acquire::https::Timeout=15"):
            assert f"-o {option}" in call
    assert "update" in update and "--error-on=any" in update
    assert "install" in install and "--no-install-recommends" in install
    assert install.split()[-1] == "libharfbuzz-subset0" and "gettext" not in install


@pytest.mark.architecture
def test_helper_does_not_install_after_a_failed_update(tmp_path):
    result, calls = _run_helper(tmp_path, ["gettext"], installed=set(), update_exit=100)
    assert result.returncode != 0
    assert len(calls) == 1 and "update" in calls[0]


@pytest.mark.architecture
def test_helper_configure_only_writes_the_budget_for_apt_children(tmp_path):
    result, calls = _run_helper(tmp_path, ["--configure-only"], installed=set())
    assert result.returncode == 0, result.stderr
    assert calls == []
    (dropin,) = list((tmp_path / "apt.conf.d").iterdir())
    text = dropin.read_text()
    assert 'Acquire::Retries "1";' in text
    assert 'Acquire::http::Timeout "15";' in text and 'Acquire::https::Timeout "15";' in text
    assert os.access(HELPER, os.X_OK)
