import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCK_FILES = ("requirements.txt", "requirements-dev.txt")
INPUT_FILES = ("requirements.in", "requirements-dev.in")


def _prepare_workspace(tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    for name in (*LOCK_FILES, *INPUT_FILES):
        shutil.copy2(REPO_ROOT / name, tmp_path / name)

    original_locks = {name: (tmp_path / name).read_bytes() for name in LOCK_FILES}
    fake_python = tmp_path / "fake-python"
    fake_python.write_text(
        f"""#!{sys.executable}
import os
import sys
from pathlib import Path

output_arg = next(arg for arg in sys.argv if arg.startswith("--output-file="))
output_path = Path(output_arg.split("=", 1)[1])
mode = os.environ["FAKE_COMPILE_MODE"]
if mode == "failure":
    raise SystemExit(1)
if mode == "drift":
    output_path.write_bytes(output_path.read_bytes() + b"# generated drift\\n")
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    return fake_python, original_locks


def _run_deps_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> subprocess.CompletedProcess[str]:
    fake_python, _ = _prepare_workspace(tmp_path)
    monkeypatch.setenv("FAKE_COMPILE_MODE", mode)
    return subprocess.run(
        [
            "make",
            "-s",
            "-f",
            str(REPO_ROOT / "Makefile"),
            "-C",
            str(tmp_path),
            "deps-check",
            f"PYTHON={fake_python}",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=60,
    )


def _assert_lock_files_restored(tmp_path: Path, original_locks: dict[str, bytes]) -> None:
    for name, original_content in original_locks.items():
        assert (tmp_path / name).read_bytes() == original_content
        assert not (tmp_path / f"{name}.bak").exists()


def test_compile_failure_is_reported_and_lock_files_are_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, original_locks = _prepare_workspace(tmp_path)
    result = _run_deps_check(tmp_path, monkeypatch, "failure")

    assert result.returncode != 0
    assert "requirements.in" in result.stderr
    assert "exit code 1" in result.stderr
    _assert_lock_files_restored(tmp_path, original_locks)


def test_identical_compilation_succeeds_and_lock_files_are_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, original_locks = _prepare_workspace(tmp_path)
    result = _run_deps_check(tmp_path, monkeypatch, "identical")

    assert result.returncode == 0, result.stderr
    _assert_lock_files_restored(tmp_path, original_locks)


def test_drift_fails_and_lock_files_are_restored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, original_locks = _prepare_workspace(tmp_path)
    result = _run_deps_check(tmp_path, monkeypatch, "drift")

    assert result.returncode != 0
    assert "Lock-Files sind nicht aktuell" in result.stdout
    _assert_lock_files_restored(tmp_path, original_locks)
