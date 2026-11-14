"""Child processes started by the browser-test helpers must not be able to hang a run.

No browser and no server: the helpers are driven with a stand-in interpreter that
never ends, so the tests finish in a second or two.
"""

import ast
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from tests.e2e import _helpers

HANGING_INTERPRETER = textwrap.dedent(
    """\
    #!/bin/sh
    echo "child stderr marker: waiting on a lock" >&2
    exec sleep 600
    """
)

E2E_DIR = Path(__file__).resolve().parent / "e2e"


@pytest.fixture
def hanging_interpreter(tmp_path, monkeypatch):
    """Stand-in for the interpreter that prints one line to stderr and then never ends."""
    script = tmp_path / "python-that-hangs"
    script.write_text(HANGING_INTERPRETER)
    script.chmod(0o755)
    monkeypatch.setattr(_helpers, "python_bin", lambda: str(script))
    return script


def test_django_shell_fails_with_child_stderr_instead_of_hanging(hanging_interpreter):
    started = time.monotonic()

    with pytest.raises(_helpers.ChildProcessTimeout) as excinfo:
        _helpers.django_shell({}, "print('never reached')", timeout=1)

    assert time.monotonic() - started < 20
    assert "child stderr marker: waiting on a lock" in str(excinfo.value)
    assert "within 1 s" in str(excinfo.value)  # the limit that was exceeded is named


def test_timed_out_child_is_killed(tmp_path):
    pid_file = tmp_path / "pid"

    with pytest.raises(_helpers.ChildProcessTimeout):
        _helpers.run_child(
            ["sh", "-c", f"echo $$ > {pid_file}; exec sleep 600"],
            timeout=1,
            label="sleeper",
        )

    pid = int(pid_file.read_text())
    assert not Path(f"/proc/{pid}").exists()


def test_run_child_returns_the_completed_process():
    result = _helpers.run_child(["sh", "-c", "echo out; echo err >&2"], timeout=30, label="echoer")

    assert (result.returncode, result.stdout.strip(), result.stderr.strip()) == (0, "out", "err")


def test_run_child_keeps_raising_for_a_failing_child_when_checked():
    with pytest.raises(subprocess.CalledProcessError):
        _helpers.run_child(["sh", "-c", "exit 3"], timeout=30, label="failer")


def test_run_child_without_check_hands_back_the_exit_code():
    assert _helpers.run_child(["sh", "-c", "exit 3"], timeout=30, label="failer", check=False).returncode == 3


def test_django_shell_still_returns_the_output_lines(tmp_path, monkeypatch):
    script = tmp_path / "python-that-answers"
    script.write_text('#!/bin/sh\nprintf "first\\n\\nsecond\\n"\n')
    script.chmod(0o755)
    monkeypatch.setattr(_helpers, "python_bin", lambda: str(script))

    assert _helpers.django_shell({}, "ignored") == ["first", "second"]


BLOCKING_CALLS = {"run", "check_output", "check_call", "call"}

LONG_RUNNING_PROCESS_FILES = {
    "conftest.py": "the gunicorn servers of the session fixtures",
    "test_encryption_key_ring_start.py": "a gunicorn started per test with its own key ring",
    "test_upload_virus_feedback.py": "a gunicorn with the virus scanner enabled",
}
"""Files that may start a process which outlives the call (``subprocess.Popen``).

Such a process ends in a fixture teardown, so every file listed here must stop it
through ``stop_child`` (terminate, wait with a limit, kill) instead of an unbounded wait.
"""


def _child_process_findings(source, filename):
    """Calls in ``source`` that can block a run without a limit, as ``file:line: reason``."""
    findings = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            findings.append(f"{filename}:{node.lineno}: import names from subprocess (use the module attribute)")
        elif isinstance(node, ast.Import) and any(a.name == "subprocess" and a.asname for a in node.names):
            findings.append(f"{filename}:{node.lineno}: subprocess imported under an alias")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
        ):
            name = node.func.attr
            if name in BLOCKING_CALLS and "timeout" not in {keyword.arg for keyword in node.keywords}:
                findings.append(f"{filename}:{node.lineno}: subprocess.{name} without timeout")
            elif name == "Popen" and filename not in LONG_RUNNING_PROCESS_FILES:
                findings.append(f"{filename}:{node.lineno}: subprocess.Popen outside the long-running allowlist")
    if filename in LONG_RUNNING_PROCESS_FILES and "stop_child(" not in source:
        findings.append(f"{filename}: starts a long-running process but never calls stop_child")
    return findings


def test_every_child_process_call_of_the_e2e_files_is_bounded():
    findings = []
    for path in sorted(E2E_DIR.glob("*.py")):
        findings += _child_process_findings(path.read_text(), path.name)
    assert findings == []


def test_long_running_allowlist_has_no_stale_entries():
    for name in LONG_RUNNING_PROCESS_FILES:
        assert "subprocess.Popen(" in (E2E_DIR / name).read_text(), name


def test_guard_flags_an_unbounded_call_and_accepts_a_bounded_one():
    unbounded = "import subprocess\nsubprocess.run(['x'], capture_output=True)\n"
    bounded = "import subprocess\nsubprocess.run(['x'], capture_output=True, timeout=5)\n"

    assert _child_process_findings(unbounded, "test_x.py") == ["test_x.py:2: subprocess.run without timeout"]
    assert _child_process_findings(bounded, "test_x.py") == []


def test_guard_flags_popen_outside_the_allowlist_and_import_forms():
    popen = "import subprocess\nsubprocess.Popen(['x'])\n"
    from_import = "from subprocess import run\n"
    aliased = "import subprocess as sp\n"

    assert len(_child_process_findings(popen, "test_x.py")) == 1
    assert len(_child_process_findings(from_import, "test_x.py")) == 1
    assert len(_child_process_findings(aliased, "test_x.py")) == 1


def test_guard_requires_stop_child_where_popen_is_allowed():
    name = next(iter(LONG_RUNNING_PROCESS_FILES))
    popen = "import subprocess\nsubprocess.Popen(['x'])\n"

    assert _child_process_findings(popen, name) == [f"{name}: starts a long-running process but never calls stop_child"]
    assert _child_process_findings(popen + "stop_child(p)\n", name) == []


def test_stop_child_ends_a_process_that_ignores_sigterm():
    ready = (
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('up', flush=True); time.sleep(600)"
    )
    with subprocess.Popen([sys.executable, "-c", ready], stdout=subprocess.PIPE, text=True) as process:
        assert process.stdout.readline().strip() == "up"
        started = time.monotonic()

        _helpers.stop_child(process, timeout=1)

        assert process.returncode == -signal.SIGKILL
        assert time.monotonic() - started < 15


def test_stop_child_accepts_a_process_that_already_ended():
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()

    _helpers.stop_child(process, timeout=1)

    assert process.returncode == 0
