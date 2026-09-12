"""Ausfuehrbare Guards fuer das Erstsetup-Skript."""

import getpass
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "initial-setup.sh"
PROD_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"
STAGING_COMPOSE = REPO_ROOT / "docker-compose.staging.yml"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _caddy_environment(compose_path: Path) -> str:
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    environment = compose.get("services", {}).get("caddy", {}).get("environment", {})
    if isinstance(environment, list):
        names = [str(item).split("=", 1)[0] for item in environment]
    else:
        names = list(environment)
    return "\n".join(f"      {name}: probe" for name in names)


def _stat_with_app_group() -> str:
    return """#!/bin/bash
mode="$(/usr/bin/stat -c %a "${!#}")" || exit $?
printf '1000 %s\n' "$mode"
"""


def _run_setup(
    tmp_path: Path,
    *,
    compose_path: Path = PROD_COMPOSE,
    health_token: str = "probe-token",
    deploy_output: str = "System check identified no issues (0 silenced).",
    deploy_rc: int = 0,
    crontab: str = "",
    volume_names: str = "",
    backup_dir: Path | None = None,
    id_stub: str | None = None,
    stat_stub: str | None = None,
):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    command_log = tmp_path / "commands.log"
    command_log.touch()
    backup_dir = backup_dir or tmp_path / "operator-backups"
    _write_executable(
        bindir / "docker",
        """#!/bin/bash
printf 'docker %s\n' "$*" >> "$TEST_COMMAND_LOG"
if [[ "$1" == inspect ]]; then
    printf 'healthy\n'
elif [[ "$1 $2" == 'volume ls' ]]; then
    printf '%s\n' "$TEST_VOLUME_NAMES"
elif [[ "$1 $2" == 'compose version' ]]; then
    exit 0
elif [[ "$1" == compose ]]; then
    case "$*" in
        *' config --format json') printf '{"name":"%s"}\n' "$TEST_PROJECT_NAME" ;;
        *' config')
            printf 'name: %s\nservices:\n  caddy:\n    environment:\n%s\n' \
                "$TEST_PROJECT_NAME" "$TEST_CADDY_ENV"
            ;;
        *' ps -q db'|*' ps -q web') printf 'container-id\n' ;;
        *' exec -T web python manage.py check --deploy')
            printf '%s\n' "$TEST_DEPLOY_OUTPUT" >&2
            exit "$TEST_DEPLOY_RC"
            ;;
    esac
fi
exit 0
""",
    )
    _write_executable(
        bindir / "crontab",
        "#!/bin/bash\nprintf '%s\\n' \"$TEST_CRONTAB\"\n",
    )
    _write_executable(bindir / "sudo", "#!/bin/sh\nexit 0\n")
    _write_executable(
        bindir / "install",
        """#!/bin/bash
printf 'install %s\n' "$*" >> "$TEST_COMMAND_LOG"
# Das Testkonto laeuft in einem User-Namespace, in dem GID 1000 nicht gemappt
# ist. Nur die Zielgruppe wird deshalb auf die reale Testgruppe abgebildet;
# alle anderen Argumente einschliesslich Owner und Modus gehen unveraendert an
# die echte install-Binaerdatei.
args=("$@")
for ((index = 0; index < ${#args[@]}; index++)); do
    if [[ "${args[$index]}" == "-g" && "${args[$((index + 1))]}" == "1000" ]]; then
        args[$((index + 1))]="$(/usr/bin/id -g)"
    fi
done
exec /usr/bin/install "${args[@]}"
""",
    )
    if id_stub is None:
        owner = getpass.getuser()
        id_stub = f"""#!/bin/sh
case "$1" in
    -u) printf '1000\\n' ;;
    -un) printf '{owner}\\n' ;;
    -G) printf '0 1000\\n' ;;
esac
"""
    _write_executable(bindir / "id", id_stub)
    if stat_stub is not None:
        _write_executable(bindir / "stat", stat_stub)

    env_file = tmp_path / ".env.probe"
    env_file.write_text(
        f"BACKUP_HOST_DIR='{backup_dir}'\nDJANGO_HEALTH_DETAIL_TOKEN='{health_token}'\n",
        encoding="utf-8",
    )
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    project_name = compose.get("name", compose_path.parent.name)
    result = subprocess.run(
        ["/bin/bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env={
            "PATH": f"{bindir}:/usr/bin:/bin",
            "COMPOSE_FILE": str(compose_path),
            "ENV_FILE": str(env_file),
            "SKIP_SUPER_ADMIN": "1",
            "WAIT_TIMEOUT": "0",
            "TEST_CADDY_ENV": _caddy_environment(compose_path),
            "TEST_COMMAND_LOG": str(command_log),
            "TEST_CRONTAB": crontab,
            "TEST_DEPLOY_OUTPUT": deploy_output,
            "TEST_DEPLOY_RC": str(deploy_rc),
            "TEST_PROJECT_NAME": str(project_name),
            "TEST_VOLUME_NAMES": volume_names,
        },
        timeout=60,
    )
    return result, command_log.read_text(encoding="utf-8"), backup_dir


def test_prod_compose_accepts_an_empty_optional_health_token(tmp_path: Path) -> None:
    result, commands, _ = _run_setup(tmp_path, health_token="")
    assert result.returncode == 0, result.stderr
    assert " up -d db" in commands


def test_staging_compose_requires_the_caddy_health_token(tmp_path: Path) -> None:
    if not STAGING_COMPOSE.exists():
        pytest.skip("docker-compose.staging.yml fehlt in diesem Baum — Staging-Guard nicht anwendbar")
    result, commands, _ = _run_setup(tmp_path, compose_path=STAGING_COMPOSE, health_token="")
    assert result.returncode != 0
    assert "DJANGO_HEALTH_DETAIL_TOKEN fehlt oder ist leer" in result.stderr
    assert " up -d db" not in commands


def test_backup_directory_is_prepared_before_first_up(tmp_path: Path) -> None:
    result, commands, backup_dir = _run_setup(tmp_path)
    assert result.returncode == 0, result.stderr
    assert backup_dir.stat().st_mode & 0o7777 == 0o2750
    assert backup_dir.stat().st_uid == 1000
    assert backup_dir.stat().st_mode & 0o050 == 0o050
    assert backup_dir.stat().st_mode & 0o004 == 0
    assert f"install -d -m 2750 -o {getpass.getuser()} -g 1000" in commands
    assert commands.index(" up -d db") > commands.index("volume ls")


def test_existing_backup_directory_with_mode_2750_is_accepted_without_ownership_change(
    tmp_path: Path,
) -> None:
    backup_dir = tmp_path / "precreated-backups"
    backup_dir.mkdir(mode=0o750)
    backup_dir.chmod(0o2750)
    owner_before = (backup_dir.stat().st_uid, backup_dir.stat().st_gid)
    result, commands, _ = _run_setup(
        tmp_path,
        backup_dir=backup_dir,
        stat_stub=_stat_with_app_group(),
    )
    assert result.returncode == 0, result.stderr
    assert " up -d db" in commands
    assert "install " not in commands
    assert (backup_dir.stat().st_uid, backup_dir.stat().st_gid) == owner_before
    assert backup_dir.stat().st_mode & 0o7777 == 0o2750


def test_missing_backup_directory_without_group_privilege_fails_before_up(tmp_path: Path) -> None:
    result, commands, backup_dir = _run_setup(
        tmp_path,
        id_stub="""#!/bin/sh
case "$1" in
    -u) printf '2000\n' ;;
    -un) printf 'operator\n' ;;
    -G) printf '2000 2001\n' ;;
esac
""",
    )
    assert result.returncode != 0
    assert 'sudo install -d -m 2750 -o "operator" -g 1000' in result.stderr
    assert str(backup_dir) in result.stderr
    assert " up -d db" not in commands


def test_existing_backup_directory_with_wrong_group_prints_repair_command(tmp_path: Path) -> None:
    backup_dir = tmp_path / "wrong-backups"
    backup_dir.mkdir()
    backup_dir.chmod(0o2750)
    result, commands, _ = _run_setup(
        tmp_path,
        backup_dir=backup_dir,
    )
    assert result.returncode != 0
    assert f'sudo chgrp 1000 "{backup_dir}" && sudo chmod 2750 "{backup_dir}"' in result.stderr
    assert " up -d db" not in commands


@pytest.mark.parametrize("mode", [0o0750, 0o2770, 0o2753, 0o2755])
def test_existing_backup_directory_with_wrong_mode_stops_before_up(tmp_path: Path, mode: int) -> None:
    backup_dir = tmp_path / "wrong-mode-backups"
    backup_dir.mkdir()
    backup_dir.chmod(mode)
    result, commands, _ = _run_setup(
        tmp_path,
        backup_dir=backup_dir,
        stat_stub=_stat_with_app_group(),
    )
    assert result.returncode != 0
    assert f'sudo chgrp 1000 "{backup_dir}" && sudo chmod 2750 "{backup_dir}"' in result.stderr
    assert "Modus 2750" not in result.stdout
    assert " up -d db" not in commands


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


def test_deploy_failure_keeps_diagnosis_and_stops_followup_steps(tmp_path: Path) -> None:
    result, commands, _ = _run_setup(
        tmp_path,
        deploy_output="SETUP_DEPLOY_ERROR: rejected setting",
        deploy_rc=23,
    )
    assert result.returncode != 0
    assert "SETUP_DEPLOY_ERROR: rejected setting" in result.stdout + result.stderr
    assert "check_db_roles" not in commands
    assert "Erstinbetriebnahme abgeschlossen" not in result.stdout


def test_backup_cron_rejects_a_missing_target(tmp_path: Path) -> None:
    result, _, _ = _run_setup(tmp_path, crontab="0 2 * * * /definitely-missing/scripts/ops/backup.sh")
    assert result.returncode == 0, result.stderr
    assert "nicht gefunden" in result.stderr
    assert "Host-Cron-Eintrag fuer backup.sh gefunden" not in result.stdout


def test_backup_cron_rejects_a_foreign_installation(tmp_path: Path) -> None:
    foreign_script = tmp_path / "foreign" / "scripts" / "ops" / "backup.sh"
    foreign_script.parent.mkdir(parents=True)
    _write_executable(foreign_script, "#!/bin/sh\nexit 0\n")
    result, _, _ = _run_setup(tmp_path, crontab=f"0 2 * * * {foreign_script}")
    assert result.returncode == 0, result.stderr
    assert "fremde Installation" in result.stderr
    assert "Host-Cron-Eintrag fuer backup.sh gefunden" not in result.stdout


@pytest.mark.parametrize(
    "cron_entry",
    [
        f"0 2 * * * {REPO_ROOT}/scripts/ops/backup.sh >> /var/log/anlaufstelle-backup.log 2>&1",
        f"0 2 * * * cd {REPO_ROOT} && ./scripts/ops/backup.sh >> /var/log/anlaufstelle-backup.log 2>&1",
    ],
)
def test_backup_cron_accepts_this_installation(tmp_path: Path, cron_entry: str) -> None:
    result, _, _ = _run_setup(tmp_path, crontab=cron_entry)
    assert result.returncode == 0, result.stderr
    assert "Host-Cron-Eintrag fuer backup.sh gefunden" in result.stdout


def test_foreign_pgdata_volume_prints_upgrade_warning(tmp_path: Path) -> None:
    result, _, _ = _run_setup(tmp_path, volume_names="legacy-install_pgdata")
    assert result.returncode == 0, result.stderr
    assert "Bestandsinstallation: bisherigen Projektnamen" in result.stderr
    assert "docker volume ls | grep pgdata" in result.stderr


def test_setup_fixture_uses_the_real_production_compose() -> None:
    assert PROD_COMPOSE.is_file()
    assert PROD_COMPOSE != REPO_ROOT / "services-empty.yml"
