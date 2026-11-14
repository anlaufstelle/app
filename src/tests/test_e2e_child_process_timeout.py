"""Child processes started by the browser-test helpers must not be able to hang a run.

No browser and no server: the helpers are driven with a stand-in interpreter that
never ends, so the tests finish in a second or two.
"""

import ast
import subprocess
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
    assert "1" in str(excinfo.value)  # the limit that was exceeded is named


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


def _calls_without_timeout(path):
    """``subprocess.run``-style calls in ``path`` that pass no ``timeout`` argument."""
    missing = []
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = node.func.value
        if (
            isinstance(owner, ast.Name)
            and owner.id == "subprocess"
            and node.func.attr
            in {
                "run",
                "check_output",
                "check_call",
                "call",
            }
        ):
            if "timeout" not in {keyword.arg for keyword in node.keywords}:
                missing.append(f"{path.name}:{node.lineno}")
    return missing


@pytest.mark.parametrize("name", ["_helpers.py", "conftest.py"])
def test_shared_e2e_modules_pass_a_timeout_to_every_blocking_child_call(name):
    assert _calls_without_timeout(E2E_DIR / name) == []
