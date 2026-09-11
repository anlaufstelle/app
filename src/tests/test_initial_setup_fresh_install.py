"""Bash-Fixture fuer Fresh-Install-Invarianten des Erstsetup-Skripts."""

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "initial-setup.sh"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _run_setup(tmp_path: Path, *, deploy_output: str = "System check identified no issues (0 silenced)."):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    command_log = tmp_path / "commands.log"
    backup_dir = tmp_path / "operator-backups"
    _write_executable(
        bindir / "install",
        """#!/bin/bash
printf 'install %s\\n' "$*" >> "$TEST_COMMAND_LOG"
/usr/bin/mkdir -p "${!#}"
/usr/bin/chmod 2750 "${!#}"
""",
    )
    _write_executable(
        bindir / "docker",
        """#!/bin/bash
printf 'docker %s\\n' "$*" >> "$TEST_COMMAND_LOG"
if [[ "$1" == inspect ]]; then
    printf 'healthy\\n'
elif [[ "$1 $2" == 'compose version' ]]; then
    exit 0
elif [[ "$1" == compose ]]; then
    case "$*" in
        *' ps -q db'|*' ps -q web') printf 'container-id\\n' ;;
        *' exec -T web python manage.py check --deploy') printf '%s\\n' "$TEST_DEPLOY_OUTPUT" ;;
    esac
fi
exit 0
""",
    )
    _write_executable(bindir / "crontab", "#!/bin/sh\nexit 0\n")
    _write_executable(bindir / "sudo", "#!/bin/sh\nexit 0\n")
    compose = tmp_path / "docker-compose.probe.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    env_file = tmp_path / ".env.probe"
    env_file.write_text(f"BACKUP_HOST_DIR='{backup_dir}'\n", encoding="utf-8")
    result = subprocess.run(
        ["/bin/bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env={
            "PATH": f"{bindir}:/usr/bin:/bin",
            "COMPOSE_FILE": str(compose),
            "ENV_FILE": str(env_file),
            "SKIP_SUPER_ADMIN": "1",
            "WAIT_TIMEOUT": "0",
            "TEST_COMMAND_LOG": str(command_log),
            "TEST_DEPLOY_OUTPUT": deploy_output,
        },
        timeout=60,
    )
    return result, command_log.read_text(encoding="utf-8"), backup_dir


def test_backup_directory_is_prepared_before_first_up(tmp_path: Path) -> None:
    result, commands, backup_dir = _run_setup(tmp_path)
    assert result.returncode == 0, result.stderr
    assert commands.index("install ") < commands.index(" up -d db")
    assert "-m 2750" in commands
    assert backup_dir.stat().st_mode & 0o7777 == 0o2750
    assert backup_dir.stat().st_mode & 0o007 == 0


def test_role_check_runs_through_maintenance(tmp_path: Path) -> None:
    result, commands, _ = _run_setup(tmp_path)
    assert result.returncode == 0, result.stderr
    assert " run --rm maintenance check_db_roles" in commands
    assert "exec -T web python manage.py check_db_roles" not in commands
    assert "Ownership getrennt" not in result.stdout


def test_deploy_success_message_reflects_clean_output(tmp_path: Path) -> None:
    result, _, _ = _run_setup(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Keine Deployment-Warnungen." in result.stdout


def test_deploy_warning_count_replaces_false_success(tmp_path: Path) -> None:
    result, _, _ = _run_setup(tmp_path, deploy_output="System check identified 2 issues (0 silenced).")
    assert result.returncode == 0, result.stderr
    assert "Keine Deployment-Warnungen." not in result.stdout
    assert "2 Deployment-Warnungen" in result.stderr


def test_backup_cron_requires_the_resolved_project_script_path() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "/scripts/ops/backup\\.sh" in text
    assert "grep -Eq" in text
