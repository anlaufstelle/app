"""Child processes started by the browser-test helpers must not be able to hang a run.

No browser and no server: the helpers are driven with a stand-in interpreter that
never ends, so the tests finish in a second or two.
"""

import ast
import re
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
through ``stop_child`` or ``stop_children`` (terminate, wait with a limit, kill) instead of an unbounded wait.
"""


BOUNDED_HELPERS = {"run_child", "django_shell"}


def _timeout_is_none(call):
    return any(
        keyword.arg == "timeout" and isinstance(keyword.value, ast.Constant) and keyword.value.value is None
        for keyword in call.keywords
    )


def _blocking_call_findings(call, filename, name):
    """Findings for one blocking ``subprocess`` call: no limit, a limit of ``None``, or options that could hide it.

    ``**options`` next to an explicit ``timeout=`` is harmless (a second ``timeout`` raises ``TypeError``).
    """
    where = f"{filename}:{call.lineno}: {name}"
    if _timeout_is_none(call):
        return [f"{where} with timeout=None"]
    if "timeout" in {keyword.arg for keyword in call.keywords}:
        return []
    splat = next((keyword.value for keyword in call.keywords if keyword.arg is None), None)
    if splat is not None:
        shown = f"**{splat.id}" if isinstance(splat, ast.Name) else "**options"
        return [f"{where} with {shown} (a timeout cannot be verified)"]
    return [f"{where} without timeout"]


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
            if name in BLOCKING_CALLS:
                findings += _blocking_call_findings(node, filename, f"subprocess.{name}")
            elif name == "Popen" and filename not in LONG_RUNNING_PROCESS_FILES:
                findings.append(f"{filename}:{node.lineno}: subprocess.Popen outside the long-running allowlist")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in BOUNDED_HELPERS:
            # The helpers have a default limit; only an explicit ``timeout=None`` removes it.
            if _timeout_is_none(node):
                findings.append(f"{filename}:{node.lineno}: {node.func.id} with timeout=None")
    if filename in LONG_RUNNING_PROCESS_FILES and not re.search(r"\bstop_child(?:ren)?\(", source):
        findings.append(f"{filename}: starts a long-running process but never calls stop_child or stop_children")
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


def test_guard_flags_a_timeout_that_is_none_and_options_that_could_hide_it():
    none = "import subprocess\nsubprocess.run(['x'], timeout=None)\n"
    splat = "import subprocess\nsubprocess.check_output(['x'], **options)\n"
    helper_none = "run_child(['x'], timeout=None, label='x')\n"
    shell_none = "django_shell(env, 'script', timeout=None)\n"
    splat_with_timeout = "import subprocess\nsubprocess.run(['x'], timeout=5, **options)\n"
    helper_bounded = "run_child(['x'], timeout=30, label='x')\ndjango_shell(env, 'script')\n"

    assert _child_process_findings(none, "test_x.py") == ["test_x.py:2: subprocess.run with timeout=None"]
    assert _child_process_findings(splat, "test_x.py") == [
        "test_x.py:2: subprocess.check_output with **options (a timeout cannot be verified)"
    ]
    assert _child_process_findings(helper_none, "test_x.py") == ["test_x.py:1: run_child with timeout=None"]
    assert _child_process_findings(shell_none, "test_x.py") == ["test_x.py:1: django_shell with timeout=None"]
    assert _child_process_findings(splat_with_timeout, "test_x.py") == []
    assert _child_process_findings(helper_bounded, "test_x.py") == []


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

    assert _child_process_findings(popen, name) == [
        f"{name}: starts a long-running process but never calls stop_child or stop_children"
    ]
    assert _child_process_findings(popen + "stop_child(p)\n", name) == []
    assert _child_process_findings(popen + "stop_children([p])\n", name) == []


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


class _StuckChild:
    """A child that survives SIGTERM and SIGKILL, for example in uninterruptible sleep."""

    def poll(self):
        return None

    def send_signal(self, _signal):
        pass

    def kill(self):
        pass

    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired("stuck-child", timeout)


def _sleeper():
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])


def test_stop_children_stops_every_child_although_one_cannot_be_stopped():
    before, after = _sleeper(), _sleeper()
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            _helpers.stop_children([before, _StuckChild(), after], timeout=1)

        assert before.returncode is not None
        assert after.returncode is not None
    finally:
        for process in (before, after):
            if process.poll() is None:
                process.kill()
            process.wait()


def test_stop_children_without_trouble_stops_all_and_raises_nothing():
    first, second = _sleeper(), _sleeper()

    _helpers.stop_children([first, second], timeout=5)

    assert (first.returncode, second.returncode) == (-signal.SIGTERM, -signal.SIGTERM)


def test_stop_child_accepts_a_process_that_already_ended():
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()

    _helpers.stop_child(process, timeout=1)

    assert process.returncode == 0
