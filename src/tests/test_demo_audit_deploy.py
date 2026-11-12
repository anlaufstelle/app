"""Execute the demo audit installation with isolated command doubles."""

import fcntl
import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture


@pytest.fixture
def audit_deploy(tmp_path: Path):
    path = Path("dev-ops/deploy/deploy-demo.sh")
    if not path.exists():
        pytest.skip("dev-ops/deploy/deploy-demo.sh is stripped from the public snapshot")
    text = path.read_text(encoding="utf-8")
    section = text[text.index("# 6) Install the audit-verification timer") : text.index("\n$COMPOSE ps")]
    script = tmp_path / "install.sh"
    script.write_text("set -euo pipefail\n" + section + "\necho DEPLOY_CONTINUED\n", encoding="utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    doubles = {
        "sudo": """#!/bin/bash
case "$1" in
  systemctl) exec "$@" ;;
  tee) exec /usr/bin/tee "$STUB_ROOT/${2##*/}" ;;
  *) echo "Unexpected sudo command: $*" >&2; exit 99 ;;
esac
""",
        "systemctl": """#!/usr/bin/env python3
import fcntl
import json
import os
import re
import shlex
import sys
from pathlib import Path
root = Path(os.environ["STUB_ROOT"])
state_path = root / "state.json"
state = json.loads(state_path.read_text())
args = sys.argv[1:]
with (root / "calls.log").open("a") as log:
    log.write(" ".join(args) + "\\n")
if args[0] == "show":
    if os.environ["AUDIT_SCENARIO"] == "unreadable":
        sys.exit(1)
    if "--value" in args:
        print(state[args[args.index("-p") + 1]])
    else:
        for key, value in state.items():
            print(f"{key}={value}")
    sys.exit(0)
if args[0] in ("daemon-reload", "enable"):
    sys.exit(0)
if args == ["start", "anlaufstelle-audit-verify.service"]:
    scenario = os.environ["AUDIT_SCENARIO"]
    if scenario in ("start_failure", "no_run", "unloaded"):
        if scenario == "unloaded":
            state["LoadState"] = "not-found"
            state_path.write_text(json.dumps(state))
        print("Start request did not reach the manager", file=sys.stderr)
        sys.exit(0 if scenario == "no_run" else 1)
    start = int(state["ExecMainStartTimestampMonotonic"]) + 100
    state.update(ExecMainStartTimestampMonotonic=str(start),
                 ExecMainExitTimestampMonotonic=str(start + 10))
    if scenario == "running":
        state.update(ActiveState="activating", ExecMainExitTimestampMonotonic="0")
        state_path.write_text(json.dumps(state))
        sys.exit(0)
    if scenario == "no_end":
        state["ExecMainExitTimestampMonotonic"] = "0"
    red = scenario == "red"
    state.update(Result="exit-code" if red else "success",
                 ExecMainStatus="1" if red else "0",
                 ActiveState="failed" if red else "inactive")
    state_path.write_text(json.dumps(state))
    sys.exit(1 if red else 0)
print(f"Unexpected systemctl command: {args}", file=sys.stderr)
sys.exit(99)
""",
    }
    for name, content in doubles.items():
        stub = bindir / name
        stub.write_text(content, encoding="utf-8")
        stub.chmod(0o755)
    state = {
        "LoadState": "loaded",
        "ActiveState": "inactive",
        "Result": "success",
        "ExecMainStatus": "0",
        "ExecMainStartTimestampMonotonic": "100",
        "ExecMainExitTimestampMonotonic": "110",
    }
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")

    def run(scenario: str = "green") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(script)],
            env={
                **os.environ,
                "PATH": f"{bindir}:/usr/bin:/bin",
                "STUB_ROOT": str(tmp_path),
                "AUDIT_SCENARIO": scenario,
            },
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )

    return run


def test_start_failure_cannot_reuse_previous_success(audit_deploy) -> None:
    result = audit_deploy("start_failure")
    assert result.returncode != 0, result.stdout + result.stderr
    assert "DEPLOY_CONTINUED" not in result.stdout
    assert "ERROR" in result.stderr
    assert "no new completed audit verification run" in result.stderr


def test_completed_green_run_continues(audit_deploy) -> None:
    result = audit_deploy()
    assert result.returncode == 0, result.stderr
    assert "completed successfully" in result.stdout
    assert "DEPLOY_CONTINUED" in result.stdout
    assert "WARNING" not in result.stderr


def test_completed_red_run_warns_and_continues(audit_deploy, tmp_path: Path) -> None:
    result = audit_deploy("red")
    assert result.returncode == 0, result.stderr
    assert "DEPLOY_CONTINUED" in result.stdout
    assert "WARNING" in result.stderr
    assert "journalctl -u anlaufstelle-audit-verify.service --no-pager" in result.stderr
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["Result"] == "exit-code"
    assert state["ExecMainStatus"] == "1"
    assert state["ActiveState"] == "failed"


@pytest.mark.parametrize("scenario", ["start_failure", "no_run"])
def test_never_started_service_cannot_report_default_success(audit_deploy, tmp_path: Path, scenario: str) -> None:
    state_path = tmp_path / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update(ExecMainStartTimestampMonotonic="0", ExecMainExitTimestampMonotonic="0")
    state_path.write_text(json.dumps(state), encoding="utf-8")
    result = audit_deploy(scenario)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "DEPLOY_CONTINUED" not in result.stdout
    assert "no new completed audit verification run" in result.stderr


@pytest.mark.parametrize("scenario", ["no_run", "unloaded", "running", "no_end", "unreadable"])
def test_missing_or_incomplete_run_aborts_deploy(audit_deploy, scenario: str) -> None:
    result = audit_deploy(scenario)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "DEPLOY_CONTINUED" not in result.stdout
    assert "ERROR" in result.stderr


def test_repeated_installation_overwrites_same_units_and_runs_again(audit_deploy, tmp_path: Path) -> None:
    first = audit_deploy()
    assert first.returncode == 0, first.stderr
    units = {path.name: path.read_bytes() for path in tmp_path.glob("*.service")}
    units.update({path.name: path.read_bytes() for path in tmp_path.glob("*.timer")})
    assert set(units) == {"anlaufstelle-audit-verify.service", "anlaufstelle-audit-verify.timer"}
    first_state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    second = audit_deploy()
    assert second.returncode == 0, second.stderr
    assert "DEPLOY_CONTINUED" in second.stdout
    assert {path.name: path.read_bytes() for path in tmp_path.glob("anlaufstelle-*")} == units
    second_state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert int(second_state["ExecMainStartTimestampMonotonic"]) > int(first_state["ExecMainStartTimestampMonotonic"])
    calls = (tmp_path / "calls.log").read_text(encoding="utf-8").splitlines()
    assert calls.count("daemon-reload") == 2
    assert calls.count("enable --now anlaufstelle-audit-verify.timer") == 2
    assert calls.count("start anlaufstelle-audit-verify.service") == 2


@pytest.mark.parametrize("blocked_service", ["audit", "reseed"])
def test_reseed_and_audit_share_a_bounded_exclusive_lock(audit_deploy, tmp_path: Path, blocked_service: str) -> None:
    result = audit_deploy()
    assert result.returncode == 0, result.stderr
    audit = (tmp_path / "anlaufstelle-audit-verify.service").read_text(encoding="utf-8")
    assert "After=anlaufstelle-demo-reseed.service\n" in audit
    deploy = Path("dev-ops/deploy/deploy-demo.sh").read_text(encoding="utf-8")
    match = re.search(r"anlaufstelle-demo-reseed\.service >[^\n]*<<'UNIT'\n(.*?)^UNIT$", deploy, re.M | re.S)
    assert match
    units = {"audit": audit, "reseed": match.group(1)}
    commands = {
        name: shlex.split(
            next(line.removeprefix("ExecStart=") for line in unit.splitlines() if line.startswith("ExecStart="))
        )
        for name, unit in units.items()
    }
    lock = "/run/lock/anlaufstelle-demo-maintenance.lock"
    for command in commands.values():
        assert command[:4] == ["/usr/bin/flock", "--wait", "300", lock]
    marker = tmp_path / "executed"
    payload = tmp_path / "payload.sh"
    payload.write_text('#!/bin/sh\nprintf executed > "$MARKER_PATH"\n', encoding="utf-8")
    payload.chmod(0o755)
    command = commands[blocked_service][:4] + [str(payload)]
    command[3] = str(tmp_path / "maintenance.lock")
    with Path(command[3]).open("w") as held_lock:
        fcntl.flock(held_lock, fcntl.LOCK_EX)
        with subprocess.Popen(command, env={**os.environ, "MARKER_PATH": str(marker)}) as process:
            try:
                with pytest.raises(subprocess.TimeoutExpired):
                    process.wait(timeout=0.2)
                assert not marker.exists()
            finally:
                fcntl.flock(held_lock, fcntl.LOCK_UN)
                process.wait(timeout=5)
            assert process.returncode == 0
    assert marker.read_text(encoding="utf-8") == "executed"
