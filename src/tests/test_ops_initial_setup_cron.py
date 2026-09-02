"""Funktionaler Host-Cron-Check des Erstsetup-Skripts (Refs #2021)."""

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "initial-setup.sh"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _run_setup(tmp_path: Path, *, current_crontab: str = "", root_crontab: str = ""):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    sudo_log = tmp_path / "sudo.log"

    _write_executable(
        bindir / "docker",
        """#!/bin/sh
if [ "$1" = "inspect" ]; then
    printf 'healthy\\n'
elif [ "$1" = "compose" ] && [ "$2" = "version" ]; then
    exit 0
elif [ "$1" = "compose" ]; then
    case "$*" in
        *" ps -q db"|*" ps -q web") printf 'container-id\\n' ;;
    esac
fi
exit 0
""",
    )
    _write_executable(
        bindir / "crontab",
        """#!/bin/sh
printf '%s' "${TEST_CURRENT_CRONTAB:-}"
exit 0
""",
    )
    _write_executable(
        bindir / "sudo",
        f"""#!/bin/sh
printf '%s\\n' "$*" >> "{sudo_log}"
printf '%s' "${{TEST_ROOT_CRONTAB:-}}"
exit 0
""",
    )

    compose = tmp_path / "docker-compose.probe.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    env_file = tmp_path / ".env.probe"
    env_file.write_text("POSTGRES_USER=anlaufstelle\n", encoding="utf-8")
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
            "TEST_CURRENT_CRONTAB": current_crontab,
            "TEST_ROOT_CRONTAB": root_crontab,
        },
        timeout=60,
    )
    return result, sudo_log


def test_missing_backup_cron_warns_without_failing(tmp_path: Path) -> None:
    result, sudo_log = _run_setup(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "WARN" in result.stderr
    assert "backup.sh" in result.stderr
    assert "docs/ops-runbook.md § 3.3" in result.stderr
    assert sudo_log.read_text(encoding="utf-8").strip() == "-n crontab -l -u root"


def test_current_user_backup_cron_suppresses_warning(tmp_path: Path) -> None:
    result, _ = _run_setup(
        tmp_path,
        current_crontab="0 2 * * * /opt/anlaufstelle/scripts/ops/backup.sh\n",
    )

    assert result.returncode == 0, result.stderr
    assert "kein Host-Cron-Eintrag" not in result.stderr


def test_root_backup_cron_suppresses_warning(tmp_path: Path) -> None:
    result, sudo_log = _run_setup(
        tmp_path,
        root_crontab="0 2 * * * /opt/anlaufstelle/scripts/ops/backup.sh\n",
    )

    assert result.returncode == 0, result.stderr
    assert "kein Host-Cron-Eintrag" not in result.stderr
    assert sudo_log.read_text(encoding="utf-8").strip() == "-n crontab -l -u root"
