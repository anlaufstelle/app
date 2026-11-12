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

# Environment facts behind the budget (apt 2.7.14 on noble; the sources are cited in the helper header).
# Observed in the failing runs: one failed name resolution takes about 20 s.
RESOLVE_ATTEMPT_SECONDS = 20
# Safety factor on that figure: it was read from one incident log and never measured on a runner.
RESOLVE_STRESS_FACTOR = 2
# noble, noble-updates, noble-backports, noble-security: one InRelease each. Every later index file
# follows the mirror that delivered its InRelease (Acquire::SameMirrorForAllIndexes), so a dead first
# mirror costs index files nothing, provided the helper pins that option.
INRELEASE_FILES = 4
# Each requested package plus one dependency that is missing on the runner image.
DEB_FILES_PER_PACKAGE = 2
# ``playwright install --with-deps chromium`` / ``install-deps chromium`` (Playwright 1.63.0, nativeDeps
# ubuntu24.04-x64) name 33 packages for chromium plus tools; allow 7 more for dependencies pulled in
# (xvfb, xfonts-*). How many are really missing on the runner image is not known offline.
PLAYWRIGHT_MAX_DEB_FILES = 40
# No apt or Playwright-deps step may run longer than this; the budget below has to fit inside it.
MAX_STEP_MINUTES = 20
# Left for the working fallback mirror to deliver the lists and the .deb files.
RESERVE_SECONDS = 120

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


def _dropin_text() -> str:
    match = re.search(r"<<'CONF'\n(.*?)\nCONF\n", HELPER.read_text(), re.S)
    assert match, "the helper writes no apt.conf.d drop-in"
    return match.group(1)


def dead_mirror_seconds(*, retries: int, timeout: int, files: int, resolve: int = RESOLVE_ATTEMPT_SECONDS) -> int:
    """Time a dead first mirror costs for ``files`` files before apt moves on to the next mirror.

    Every file is tried ``retries + 1`` times on the dead mirror (per-file counter, apt's
    ``HandleFailure``). One attempt ends at the first of two limits: the name resolution (libc
    resolver, ``resolve`` seconds, apt's ``Timeout`` does not apply to it) or apt's own network
    timeout (connect/stall). The slower one bounds the attempt, so the retry count multiplies the
    resolution time, and ``Timeout`` only matters once it exceeds it. Between two attempts apt waits
    ``min(2 ** n, 30)`` seconds (``Acquire::Retries::Delay`` is on by default).
    """
    delays = sum(min(2**attempt, 30) for attempt in range(retries))
    return files * ((retries + 1) * max(resolve, timeout) + delays)


def _helper_calls():
    """Yield (workflow, job, step, packages) for each workflow step that installs through the helper."""
    for name, job, _steps_of_job, _index, step in _steps():
        run = step.get("run", "")
        if HELPER_CALL not in run or "--configure-only" in run:
            continue
        packages = run.split(HELPER_CALL, 1)[1].split()
        yield name, job, step, packages


@pytest.mark.architecture
def test_workflows_install_packages_only_through_the_bounded_helper():
    calls = 0
    for name, job, _steps_of_job, _index, step in _steps():
        run = step.get("run", "")
        assert "apt-get" not in run and "apt install" not in run, (name, job, step)
        assert "apt-mirrors" not in run, (name, job, "do not rewrite the runner's mirror list")
        if PLAYWRIGHT_APT.search(run) or HELPER_CALL in run:
            assert 0 < step.get("timeout-minutes", 0) <= MAX_STEP_MINUTES, (name, job, step)
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
    assert dead_mirror_seconds(retries=1, timeout=15, files=4) == 4 * (2 * 20 + 1)  # 1 s wait between attempts
    assert dead_mirror_seconds(retries=0, timeout=15, files=4) == 4 * 20
    assert dead_mirror_seconds(retries=1, timeout=60, files=1) == 2 * 60 + 1  # a long timeout takes over


@pytest.mark.architecture
def test_dead_first_mirror_leaves_room_for_the_fallback_mirror_within_the_step_limit():
    """The budget is computed from the helper's real options and each workflow step's real limit."""
    retries = _helper_option("Acquire::Retries")
    timeout = max(_helper_option("Acquire::http::Timeout"), _helper_option("Acquire::https::Timeout"))
    checked = 0
    for name, job, step, packages in _helper_calls():
        checked += 1
        files = INRELEASE_FILES + DEB_FILES_PER_PACKAGE * len(packages)
        limit = step["timeout-minutes"] * 60
        for resolve in (RESOLVE_ATTEMPT_SECONDS, RESOLVE_ATTEMPT_SECONDS * RESOLVE_STRESS_FACTOR):
            dead = dead_mirror_seconds(retries=retries, timeout=timeout, files=files, resolve=resolve)
            assert dead + RESERVE_SECONDS <= limit, (name, job, resolve, dead, limit)
    assert checked >= 3


@pytest.mark.architecture
def test_playwright_system_deps_fit_the_dead_mirror_budget_within_their_step_limit():
    """Playwright runs its own ``apt-get update`` and ``install`` (no -o options, no skip when present)."""
    dropin = _dropin_text()
    retries = int(re.search(r'Acquire::Retries "(\d+)";', dropin).group(1))
    timeout = max(int(m) for m in re.findall(r'Acquire::https?::Timeout "(\d+)";', dropin))
    dead = dead_mirror_seconds(retries=retries, timeout=timeout, files=INRELEASE_FILES + PLAYWRIGHT_MAX_DEB_FILES)
    seen = 0
    for name, job, _steps_of_job, _index, step in _steps():
        if not PLAYWRIGHT_APT.search(step.get("run", "")):
            continue
        seen += 1
        assert dead + RESERVE_SECONDS <= step["timeout-minutes"] * 60, (name, job, dead, step["timeout-minutes"])
    assert seen >= 2


@pytest.mark.architecture
def test_index_files_stay_on_the_mirror_that_delivered_the_release_file():
    """The budget above counts no index files: they follow their InRelease, as long as this stays on."""
    assert "-o Acquire::SameMirrorForAllIndexes=true" in HELPER.read_text()
    assert 'Acquire::SameMirrorForAllIndexes "true";' in _dropin_text()


@pytest.mark.architecture
def test_dropin_carries_the_same_budget_as_the_helper_options():
    """Apt children we cannot parametrise (Playwright) are budgeted from the drop-in, so it must match."""
    dropin = _dropin_text()
    for key in ("Acquire::Retries", "Acquire::http::Timeout", "Acquire::https::Timeout"):
        assert f'{key} "{_helper_option(key)}";' in dropin, key


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
        for option in (
            "Acquire::Retries=0",
            "Acquire::http::Timeout=15",
            "Acquire::https::Timeout=15",
            "Acquire::SameMirrorForAllIndexes=true",
        ):
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
    assert 'Acquire::Retries "0";' in text
    assert 'Acquire::http::Timeout "15";' in text and 'Acquire::https::Timeout "15";' in text
    assert os.access(HELPER, os.X_OK)
