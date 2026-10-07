"""Guards for reading ``.env*`` files in host scripts: values are data, never shell code (Refs #2408)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
OPS_DIR = REPO_ROOT / "scripts" / "ops"
ENV_FILE_LIB = OPS_DIR / "lib" / "env-file.sh"

# ``source``/``.`` of a dotenv file: literal names (.env, .env.dev, ...) or the variables that hold them.
SOURCING_ENV_FILE = re.compile(
    r"(?:^|[;&|(\s])(?:source|\.)\s+[\"']?(?:\S*\.env(?:\.\w+)?\b|\$\{?(?:ENV_FILE|env_file)\b)"
)


def env_file_sourcing_lines(paths: list[Path]) -> list[str]:
    """Return ``file:line`` for every non-comment line that sources a dotenv file into a shell."""
    hits: list[str] = []
    for path in paths:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            if SOURCING_ENV_FILE.search(line):
                hits.append(f"{path}:{number}")
    return hits


def _tracked_script_paths() -> list[Path]:
    try:
        listed = subprocess.run(
            ["git", "ls-files", "Makefile", "*.sh"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("not a git checkout (release snapshot)")
    return [REPO_ROOT / name for name in listed]


def test_env_file_sourcing_scanner_flags_sourcing_forms(tmp_path: Path) -> None:
    probe = tmp_path / "probe.sh"
    probe.write_text(
        "# . ./.env.dev is only mentioned here\n"
        "set -a && . ./.env.dev && set +a\n"
        'source "${PROJECT_DIR}/.env"\n'
        '    source "$ENV_FILE"\n'
        '        source "$env_file"\n'
        'source "${SCRIPT_DIR}/lib/env-file.sh"\n'
        "docker compose --env-file .env.dev ps\n",
        encoding="utf-8",
    )
    assert env_file_sourcing_lines([probe]) == [f"{probe}:{n}" for n in (2, 3, 4, 5)]


def test_no_script_sources_a_dotenv_file_into_a_shell() -> None:
    # An unquoted value with a space, ``&``, ``;`` or ``(`` breaks or executes in the shell, and a sourced
    # value overrides what Compose reads itself. Scripts read the file with scripts/ops/lib/env-file.sh.
    paths = _tracked_script_paths()
    assert paths, "git ls-files returned no scripts"
    assert env_file_sourcing_lines(paths) == []


def _read_env(tmp_path: Path, content: str, names: list[str]) -> subprocess.CompletedProcess[str]:
    env_file = tmp_path / ".env"
    env_file.write_text(content, encoding="utf-8")
    script = f'source "{ENV_FILE_LIB}"\nload_env_file "{env_file}"\n' + "".join(
        f"printf '%s=<%s>\\n' {name} \"${{{name}-UNSET}}\"\n" for name in names
    )
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={**os.environ, "HOME": "/home/never-expanded"},
        timeout=30,
    )


def test_load_env_file_reads_values_as_data_not_shell_code(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    content = (
        "PLAIN=abc123\n"
        "SPACED=with some spaces\n"
        "AMPERSAND=a&b\n"
        f"SEMICOLON=x;touch {marker}\n"
        f"SUBSHELL=$(touch {marker})\n"
        f"BACKTICK=`touch {marker}`\n"
        "PAREN=(open\n"
        "DOLLAR=pa$HOME\n"
        "HASH=ab#cd\n"
        "COMMENTED=value # trailing comment\n"
        'DOUBLE="quoted # kept" ignored\n'
        "SINGLE='$HOME stays literal'\n"
        'ESCAPED="say \\"hi\\" \\$HOME"\n'
        'UNTERMINATED="never closed\n'
        "export EXPORTED=yes\n"
        "TWICE=first\n"
        "TWICE=second\n"
    )
    names = [
        "PLAIN",
        "SPACED",
        "AMPERSAND",
        "SEMICOLON",
        "SUBSHELL",
        "BACKTICK",
        "PAREN",
        "DOLLAR",
        "HASH",
        "COMMENTED",
        "DOUBLE",
        "SINGLE",
        "ESCAPED",
        "UNTERMINATED",
        "EXPORTED",
        "TWICE",
    ]
    result = _read_env(tmp_path, content, names)

    assert result.returncode == 0, result.stderr
    assert not marker.exists(), "a value was executed as shell code"
    assert result.stdout.splitlines() == [
        "PLAIN=<abc123>",
        "SPACED=<with some spaces>",
        "AMPERSAND=<a&b>",
        f"SEMICOLON=<x;touch {marker}>",
        f"SUBSHELL=<$(touch {marker})>",
        f"BACKTICK=<`touch {marker}`>",
        "PAREN=<(open>",
        "DOLLAR=<pa$HOME>",
        "HASH=<ab#cd>",
        "COMMENTED=<value>",
        "DOUBLE=<quoted # kept>",
        "SINGLE=<$HOME stays literal>",
        'ESCAPED=<say "hi" $HOME>',
        "UNTERMINATED=<>",
        "EXPORTED=<yes>",
        "TWICE=<second>",
    ]


def test_load_env_file_exports_values_to_child_processes(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("SECRET=with space & more\n", encoding="utf-8")
    script = f'source "{ENV_FILE_LIB}"\nload_env_file "{env_file}"\nbash -c \'printf "%s" "$SECRET"\'\n'
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
    assert result.stdout == "with space & more"


_FAKE_DOCKER = """#!/usr/bin/env bash
printf 'PGPASSWORD=<%s>\\n' "${PGPASSWORD-UNSET}" >> "$DOCKER_LOG"
if [[ "$*" == *"relrowsecurity"* ]]; then
    printf '20\\n'
elif [[ "$*" == *"FROM pg_trigger"* ]]; then
    printf '1\\n'
elif [[ "$*" == *"SELECT COUNT(*)"* ]]; then
    printf '1\\n'
elif [[ "$*" == *" pg_dump "* || "$*" == *" tar -C "* ]]; then
    printf 'FAKE-CONTAINER-OUTPUT\\n'
elif [[ "$*" == *" psql "* ]]; then
    cat >/dev/null
fi
exit 0
"""


def test_backup_script_reads_special_character_values_from_dotenv(tmp_path: Path) -> None:
    root = tmp_path / "root"
    ops = root / "scripts" / "ops"
    ops.mkdir(parents=True)
    shutil.copy(OPS_DIR / "backup.sh", ops / "backup.sh")
    shutil.copy(OPS_DIR / "_backup_common.sh", ops / "_backup_common.sh")
    shutil.copytree(OPS_DIR / "lib", ops / "lib")
    (root / "docker-compose.prod.yml").write_text("# stub\n", encoding="utf-8")
    marker = tmp_path / "executed"
    (root / ".env").write_text(
        "POSTGRES_USER=appuser\n"
        "POSTGRES_DB=anlaufstelle\n"
        "POSTGRES_ADMIN_USER=testadmin\n"
        f"POSTGRES_ADMIN_PASSWORD=ad min&pw;touch {marker}\n"
        "BACKUP_ENCRYPTION_KEY=not-a-placeholder-0123456789abcdef\n"
        f"BACKUP_HOST_DIR={tmp_path / 'host'}\n",
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "docker").write_text(_FAKE_DOCKER, encoding="utf-8")
    (fake_bin / "docker").chmod(0o755)
    docker_log = tmp_path / "docker.log"
    env = {k: v for k, v in os.environ.items() if k not in {"BACKUP_DIR", "BACKUP_HOST_DIR", "DJANGO_AUDIT_HWM_PATH"}}
    env.update(PATH=f"{fake_bin}:{env['PATH']}", DOCKER_LOG=str(docker_log))

    result = subprocess.run(
        ["bash", str(ops / "backup.sh")],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(root),
        timeout=60,
    )

    assert not marker.exists(), "a .env value was executed as shell code"
    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert f"PGPASSWORD=<ad min&pw;touch {marker}>" in docker_log.read_text(encoding="utf-8")
