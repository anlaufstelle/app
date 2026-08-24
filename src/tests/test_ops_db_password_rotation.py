"""Integration guards for the DB-role password rotation (Refs #1964)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts/ops/rotate-db-passwords.sh"

OLD_APP = "old-app-password-abcdefghijklmnopqrstuvwxyz"
OLD_ADMIN = "old-admin-password-abcdefghijklmnopqrstuvwxyz"
OLD_BOOTSTRAP = "old-bootstrap-password-abcdefghijklmnopqrstuvwxyz"
NEW_APP = "new-app-password-ABCDEFGHIJKLMNOPQRSTUVWXYZ"
NEW_ADMIN = "new-admin-password-ABCDEFGHIJKLMNOPQRSTUVWXYZ"
NEW_BOOTSTRAP = "new-bootstrap-password-ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _fixture(tmp_path: Path, *, accept_old_login: bool = False) -> tuple[Path, Path, Path]:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            (
                "POSTGRES_DB=anlaufstelle",
                "POSTGRES_USER=anlaufstelle",
                f"POSTGRES_PASSWORD={OLD_APP}",
                "POSTGRES_ADMIN_USER=anlaufstelle_admin",
                f"POSTGRES_ADMIN_PASSWORD={OLD_ADMIN}",
                f"POSTGRES_BOOTSTRAP_PASSWORD={OLD_BOOTSTRAP}",
                "",
            )
        ),
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    compose_file = tmp_path / "docker-compose.prod.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    _write_executable(
        bin_dir / "openssl",
        """#!/usr/bin/env bash
set -euo pipefail
count_file="$ROTATION_TEST_STATE/openssl-count"
count=0
[[ ! -f "$count_file" ]] || count="$(<"$count_file")"
count=$((count + 1))
printf '%s' "$count" > "$count_file"
case "$count" in
  1) printf '%s\n' "$ROTATION_TEST_NEW_APP" ;;
  2) printf '%s\n' "$ROTATION_TEST_NEW_ADMIN" ;;
  3) printf '%s\n' "$ROTATION_TEST_NEW_BOOTSTRAP" ;;
  *) exit 9 ;;
esac
""",
    )
    _write_executable(
        bin_dir / "docker",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$ROTATION_TEST_STATE/docker.log"

case "$*" in
  *"config --services"*) printf 'db\nweb\ncron\nmaintenance\n'; exit 0 ;;
  *"ps -q web"*) printf 'web-container-id\n'; exit 0 ;;
  *"ps -q db"*) printf 'db-container-id\n'; exit 0 ;;
  "inspect -f {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} web-container-id")
    printf 'healthy\n'; exit 0 ;;
  "inspect -f {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} db-container-id")
    printf 'healthy\n'; exit 0 ;;
esac

if [[ "$*" == *"exec -T db"* && "$*" == *"psql -h 127.0.0.1"* ]]; then
  IFS= read -r supplied_password
  user="${*: -2:1}"
  case "$supplied_password" in
    "$ROTATION_TEST_OLD_APP"|"$ROTATION_TEST_OLD_ADMIN"|"$ROTATION_TEST_OLD_BOOTSTRAP")
      printf 'auth:%s:old\n' "$user" >> "$ROTATION_TEST_STATE/docker.log"
      [[ "${ROTATION_TEST_ACCEPT_OLD:-0}" == "1" ]] && exit 0
      printf 'psql: error: password authentication failed for user "%s"\n' "$user" >&2
      exit 1
      ;;
    "$ROTATION_TEST_NEW_APP"|"$ROTATION_TEST_NEW_ADMIN"|"$ROTATION_TEST_NEW_BOOTSTRAP")
      printf 'auth:%s:new\n' "$user" >> "$ROTATION_TEST_STATE/docker.log"
      exit 0
      ;;
    *) exit 8 ;;
  esac
fi

# Rotation/Rollback SQL comes through stdin and must never appear in argv/logs.
if [[ "$*" == *"exec -T db psql"* ]]; then
  input="$(cat)"
  if [[ "$input" == *"old-app-password"* ]]; then
    printf 'sql:rollback\n' >> "$ROTATION_TEST_STATE/docker.log"
  else
    printf 'sql:rotate\n' >> "$ROTATION_TEST_STATE/docker.log"
  fi
fi
exit 0
""",
    )

    if accept_old_login:
        (state_dir / "accept-old").write_text("1", encoding="utf-8")
    return env_file, compose_file, state_dir


def _run(tmp_path: Path, *, accept_old_login: bool = False) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    env_file, compose_file, state_dir = _fixture(tmp_path, accept_old_login=accept_old_login)
    env = {
        "PATH": f"{tmp_path / 'bin'}:{os.environ.get('PATH', '')}",
        "COMPOSE_FILE": str(compose_file),
        "ENV_FILE": str(env_file),
        "ROTATION_TEST_STATE": str(state_dir),
        "ROTATION_TEST_ACCEPT_OLD": "1" if accept_old_login else "0",
        "ROTATION_TEST_OLD_APP": OLD_APP,
        "ROTATION_TEST_OLD_ADMIN": OLD_ADMIN,
        "ROTATION_TEST_OLD_BOOTSTRAP": OLD_BOOTSTRAP,
        "ROTATION_TEST_NEW_APP": NEW_APP,
        "ROTATION_TEST_NEW_ADMIN": NEW_ADMIN,
        "ROTATION_TEST_NEW_BOOTSTRAP": NEW_BOOTSTRAP,
    }
    return (
        subprocess.run(["/bin/bash", str(SCRIPT)], env=env, capture_output=True, text=True, cwd=tmp_path),
        env_file,
        state_dir / "docker.log",
    )


def test_rotation_is_atomic_and_exercises_every_consumer(tmp_path: Path) -> None:
    result, env_file, log_file = _run(tmp_path)
    assert result.returncode == 0, result.stderr

    env_text = env_file.read_text(encoding="utf-8")
    assert f"POSTGRES_PASSWORD={NEW_APP}" in env_text
    assert f"POSTGRES_ADMIN_PASSWORD={NEW_ADMIN}" in env_text
    assert f"POSTGRES_BOOTSTRAP_PASSWORD={NEW_BOOTSTRAP}" in env_text
    assert env_file.stat().st_mode & 0o777 == 0o600

    log = log_file.read_text(encoding="utf-8")
    sequence = (
        "stop web cron",
        "sql:rotate",
        "up -d --force-recreate db",
        "run --rm -T --entrypoint=/app/docker-migrate.sh maintenance",
        "up -d web cron",
    )
    positions = [log.index(marker) for marker in sequence]
    assert positions == sorted(positions), log
    assert log.count(":old") == 3
    assert log.count(":new") == 6
    for secret in (OLD_APP, OLD_ADMIN, OLD_BOOTSTRAP, NEW_APP, NEW_ADMIN, NEW_BOOTSTRAP):
        assert secret not in result.stdout
        assert secret not in result.stderr
        assert secret not in log


def test_failed_negative_login_test_rolls_db_and_env_back(tmp_path: Path) -> None:
    result, env_file, log_file = _run(tmp_path, accept_old_login=True)
    assert result.returncode != 0
    env_text = env_file.read_text(encoding="utf-8")
    assert f"POSTGRES_PASSWORD={OLD_APP}" in env_text
    assert f"POSTGRES_ADMIN_PASSWORD={OLD_ADMIN}" in env_text
    assert f"POSTGRES_BOOTSTRAP_PASSWORD={OLD_BOOTSTRAP}" in env_text
    log = log_file.read_text(encoding="utf-8")
    assert "sql:rotate" in log
    assert "sql:rollback" in log
    assert "Rollback" in result.stderr


def test_postgres_init_stays_init_only_and_points_to_rotation_path() -> None:
    body = (REPO_ROOT / "deploy/postgres-init/01-app-role.sh").read_text(encoding="utf-8")
    assert "rotate-db-passwords.sh" in body
    code = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))
    assert "ALTER ROLE" not in code.upper()


def test_runbook_covers_all_consumers_and_negative_login_proof() -> None:
    runbook = (REPO_ROOT / "docs/ops-runbook.md").read_text(encoding="utf-8")
    section_start = runbook.find("DB-Passwortrotation")
    assert section_start != -1
    section = runbook[section_start : section_start + 9000]
    assert "scripts/ops/rotate-db-passwords.sh" in section
    for consumer in ("`web`", "`cron`", "`maintenance`", "docker-migrate.sh", "backup.sh", "restore"):
        assert consumer in section
    assert "ALTER ROLE" in section
    assert "alter Login" in section and "abgewiesen" in section
    assert "NOLOGIN" in section and "nicht" in section


def test_disaster_recovery_requires_escrow_update_after_rotation() -> None:
    dr = (REPO_ROOT / "docs/disaster-recovery.md").read_text(encoding="utf-8")
    assert "rotate-db-passwords.sh" in dr
    assert "POSTGRES_PASSWORD" in dr
    assert "POSTGRES_ADMIN_PASSWORD" in dr
    assert "POSTGRES_BOOTSTRAP_PASSWORD" in dr
    assert "Eskrow" in dr and "Rotation" in dr
