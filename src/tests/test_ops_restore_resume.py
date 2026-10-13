"""A signed restore can be repeated while all writers remain stopped."""

import json

import pytest

from tests.test_ops_restore_populated_target import (
    ANCHOR_PATH,
    _run,
    _sandbox,
    _write_executable,
)

pytestmark = pytest.mark.architecture

STATEFUL_DOCKER = r"""#!/usr/bin/env python3
import json
import os
import pathlib
import sys

args = sys.argv[1:]
command = " ".join(args)
path = pathlib.Path(os.environ["ACTION_LOG"] + ".state")
state = json.loads(path.read_text()) if path.exists() else {
    "stopped": False, "db": "original", "media": "original", "anchor": "original"
}
with open(os.environ["ACTION_LOG"], "a") as log:
    log.write(command + "\n")
failure = os.environ.get("FAIL_STAGE", "")

def save():
    path.write_text(json.dumps(state))

if "stop web cron" in command:
    state["stopped"] = True
elif "exec -T web" in command and state["stopped"]:
    sys.exit("service web is not running")
if "printf" in command and "MEDIA_ROOT" in command:
    sys.stdout.write("/data/media@")
elif "HWM_CHECK=" in command:
    sys.stdin.read()
    if args[-4] == "install":
        if failure == "anchor":
            sys.exit("ERROR: anchor installation failed")
        state["anchor"] = "backup"
elif "psql" in args:
    if "--single-transaction" in args:
        sys.stdin.read()
        if failure == "import":
            sys.exit("ERROR: SQL import failed")
    elif "SELECT COUNT(*)" in command or "FROM pg_database" in command:
        print(1)
    elif "SELECT facility_id" in command:
        print("1|alpha.bin")
    elif 'RENAME TO "anlaufstelle";' in command:
        if failure == "database":
            sys.exit("ERROR: database activation failed")
        state["db"] = "backup"
elif 'mv "$stage" "$live"' in command:
    if failure == "media":
        sys.exit("ERROR: media activation failed")
    state["media"] = "backup"
elif "tar" in args:
    sys.stdin.buffer.read()
    if failure == "staging":
        sys.exit("ERROR: media staging failed")
save()
"""


@pytest.mark.parametrize("failure", ["anchor", "media", "import", "staging", "database"])
def test_repeat_signed_restore_without_starting_mixed_application(tmp_path, failure):
    root, bindir, log, db, media = _sandbox(tmp_path)
    _write_executable(bindir / "docker", STATEFUL_DOCKER)
    first = _run(root, bindir, log, db, media, DJANGO_AUDIT_HWM_PATH=ANCHOR_PATH, FAIL_STAGE=failure)
    assert first.returncode != 0, first.stdout + first.stderr
    assert "RESTORE-SATZ WIEDERHERGESTELLT" not in first.stdout
    state_path = log.with_name(log.name + ".state")
    state = json.loads(state_path.read_text())
    if failure in {"anchor", "media"}:
        assert state["stopped"]
        assert state["db"] == "backup"
        assert "phase=" in first.stderr
        assert "database=anlaufstelle" in first.stderr
        assert "media=/data/media" in first.stderr
        assert "anchor=/data/audit_hwm.json" in first.stderr
        assert "Repeat" in first.stderr
    log.write_text("")
    second = _run(root, bindir, log, db, media, DJANGO_AUDIT_HWM_PATH=ANCHOR_PATH)
    assert second.returncode == 0, second.stdout + second.stderr
    state = json.loads(state_path.read_text())
    assert state == {"stopped": True, "db": "backup", "media": "backup", "anchor": "backup"}
    assert "start web" not in log.read_text()
    assert "exec -T web" not in log.read_text()
