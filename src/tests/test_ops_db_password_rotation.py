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


def _default_env_text() -> str:
    return "\n".join(
        (
            "POSTGRES_DB=anlaufstelle",
            "POSTGRES_USER=anlaufstelle",
            f"POSTGRES_PASSWORD={OLD_APP}",
            "POSTGRES_ADMIN_USER=anlaufstelle_admin",
            f"POSTGRES_ADMIN_PASSWORD={OLD_ADMIN}",
            f"POSTGRES_BOOTSTRAP_PASSWORD={OLD_BOOTSTRAP}",
            "",
        )
    )


def _fixture(tmp_path: Path, *, accept_old_login: bool = False, env_text: str | None = None) -> tuple[Path, Path, Path]:
    env_file = tmp_path / ".env"
    env_file.write_text(_default_env_text() if env_text is None else env_text, encoding="utf-8")
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

if [[ "$*" == *"exec -T db"* && "$*" == *"psql -h db"* ]]; then
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


def _run(
    tmp_path: Path,
    *,
    accept_old_login: bool = False,
    extra_compose_files: tuple[str, ...] = (),
    env_text: str | None = None,
    old_app: str = OLD_APP,
    old_admin: str = OLD_ADMIN,
    old_bootstrap: str = OLD_BOOTSTRAP,
    env_file_override: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    env_file, compose_file, state_dir = _fixture(tmp_path, accept_old_login=accept_old_login, env_text=env_text)
    for name in extra_compose_files:
        (tmp_path / name).write_text("services: {}\n", encoding="utf-8")
    env = {
        "PATH": f"{tmp_path / 'bin'}:{os.environ.get('PATH', '')}",
        "COMPOSE_FILE": str(compose_file),
        "ENV_FILE": env_file_override or str(env_file),
        "ROTATION_TEST_STATE": str(state_dir),
        "ROTATION_TEST_ACCEPT_OLD": "1" if accept_old_login else "0",
        "ROTATION_TEST_OLD_APP": old_app,
        "ROTATION_TEST_OLD_ADMIN": old_admin,
        "ROTATION_TEST_OLD_BOOTSTRAP": old_bootstrap,
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
        "up -d --pull never --force-recreate db",
        "run --rm -T --pull never --entrypoint=/app/docker-migrate.sh maintenance",
        "up -d --pull never web cron",
    )
    positions = [log.index(marker) for marker in sequence]
    assert positions == sorted(positions), log
    assert log.count(":old") == 3
    assert log.count(":new") == 6
    assert "up -d --pull never --force-recreate db" in log
    assert "run --rm -T --pull never --entrypoint=/app/docker-migrate.sh maintenance" in log
    assert "up -d --pull never web cron" in log
    for secret in (OLD_APP, OLD_ADMIN, OLD_BOOTSTRAP, NEW_APP, NEW_ADMIN, NEW_BOOTSTRAP):
        assert secret not in result.stdout
        assert secret not in result.stderr
        assert secret not in log


def _compose_calls(log: str) -> list[str]:
    return [line for line in log.splitlines() if " --env-file " in line]


@pytest.mark.parametrize(
    "extra_files",
    [
        ("compose.legal.yml", "compose.override.yml"),
        ("compose.legal.yml",),
        ("compose.override.yml",),
        (),
    ],
    ids=["legal+override", "legal-only", "override-only", "base-only"],
)
def test_every_compose_call_uses_the_full_file_list(tmp_path: Path, extra_files: tuple[str, ...]) -> None:
    result, _env_file, log_file = _run(tmp_path, extra_compose_files=extra_files)
    assert result.returncode == 0, result.stderr

    expected = f"-f {tmp_path / 'docker-compose.prod.yml'}"
    for name in extra_files:
        expected += f" -f {tmp_path / name}"
    expected += " --env-file "

    calls = _compose_calls(log_file.read_text(encoding="utf-8"))
    assert calls, "the script issued no compose calls"
    for line in calls:
        assert expected in line, line
    # Every recreate/start of a consumer carries the list, otherwise web loses the legal mount.
    recreates = [line for line in calls if " up -d " in line]
    assert len(recreates) == 2
    # The effective file list is printed so the operator can see what was recreated.
    listing = next((line for line in result.stdout.splitlines() if "Compose files:" in line), "")
    assert listing, result.stdout
    for name in ("docker-compose.prod.yml", *extra_files):
        assert str(tmp_path / name) in listing


def test_extra_compose_files_are_ignored_when_missing_next_to_the_base_file(tmp_path: Path) -> None:
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "compose.legal.yml").write_text("services: {}\n", encoding="utf-8")
    result, _env_file, log_file = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "compose.legal.yml" not in log_file.read_text(encoding="utf-8")


def test_login_proofs_use_scram_network_path_not_trusted_loopback() -> None:
    body = SCRIPT.read_text(encoding="utf-8")
    assert "psql -h db" in body
    assert "psql -h 127.0.0.1" not in body


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


def test_dev_deploy_syncs_rotation_script_to_the_host() -> None:
    deploy_path = REPO_ROOT / "dev-ops/deploy/deploy-dev.sh"
    if not deploy_path.exists():
        pytest.skip("dev-ops/ deploy/deploy-dev.sh nicht im Public-Snapshot")
    deploy = deploy_path.read_text(encoding="utf-8")
    assert "scripts/ops/rotate-db-passwords.sh" in deploy


# --- Characterization of how the rotation reads the old values from the env file (Refs #2571) ---

BASE_LINES = ("POSTGRES_DB=anlaufstelle", "POSTGRES_USER=anlaufstelle", "POSTGRES_ADMIN_USER=anlaufstelle_admin")


def _env_text(
    app: str = OLD_APP, admin: str = OLD_ADMIN, bootstrap: str = OLD_BOOTSTRAP, *, extra: tuple[str, ...] = ()
) -> str:
    """Env file text; the three password values are written verbatim (raw dotenv syntax)."""
    lines = (
        *BASE_LINES,
        f"POSTGRES_PASSWORD={app}",
        f"POSTGRES_ADMIN_PASSWORD={admin}",
        f"POSTGRES_BOOTSTRAP_PASSWORD={bootstrap}",
        *extra,
    )
    return "\n".join(lines) + "\n"


def _assert_rejected_before_any_change(
    result: subprocess.CompletedProcess[str], env_file: Path, log_file: Path, before: str
) -> None:
    """The run failed in the preflight: no consumer stopped, no SQL, env file untouched, no secret printed."""
    assert result.returncode != 0
    log = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
    assert " stop " not in log
    assert "sql:" not in log
    assert "up -d" not in log
    assert env_file.read_text(encoding="utf-8") == before
    for secret in (OLD_APP, OLD_ADMIN, OLD_BOOTSTRAP):
        assert secret not in result.stdout
        assert secret not in result.stderr


def _assert_rotated(result: subprocess.CompletedProcess[str], env_file: Path, log_file: Path) -> None:
    assert result.returncode == 0, result.stderr
    env_text = env_file.read_text(encoding="utf-8")
    assert f"\nPOSTGRES_PASSWORD={NEW_APP}\n" in env_text
    assert f"\nPOSTGRES_ADMIN_PASSWORD={NEW_ADMIN}\n" in env_text
    assert f"\nPOSTGRES_BOOTSTRAP_PASSWORD={NEW_BOOTSTRAP}\n" in env_text
    assert log_file.read_text(encoding="utf-8").count(":old") == 3


def test_double_and_single_quoted_old_passwords_are_unquoted(tmp_path: Path) -> None:
    text = _env_text(f'"{OLD_APP}"', f"'{OLD_ADMIN}'", OLD_BOOTSTRAP)
    result, env_file, log_file = _run(tmp_path, env_text=text)
    _assert_rotated(result, env_file, log_file)


def test_comments_blank_lines_and_foreign_keys_are_ignored_and_preserved(tmp_path: Path) -> None:
    text = _env_text(
        extra=(
            "",
            "# POSTGRES_PASSWORD=commented-out-duplicate",
            "#POSTGRES_ADMIN_PASSWORD=commented-out-duplicate",
            "POSTGRES_PASSWORD_NOTE=unrelated-value",
            "OTHER=1",
        )
    )
    result, env_file, log_file = _run(tmp_path, env_text=text)
    _assert_rotated(result, env_file, log_file)
    env_text = env_file.read_text(encoding="utf-8")
    for kept in (
        "# POSTGRES_PASSWORD=commented-out-duplicate\n",
        "#POSTGRES_ADMIN_PASSWORD=commented-out-duplicate\n",
        "POSTGRES_PASSWORD_NOTE=unrelated-value\n",
        "OTHER=1\n",
        "POSTGRES_DB=anlaufstelle\n",
    ):
        assert kept in env_text


def test_equals_sign_inside_the_old_password_is_part_of_the_value(tmp_path: Path) -> None:
    old_app = "old-app-password=with=equals=="
    result, env_file, log_file = _run(tmp_path, env_text=_env_text(app=old_app), old_app=old_app)
    _assert_rotated(result, env_file, log_file)


@pytest.mark.parametrize(
    "payload",
    ["old-app-password-$(touch {marker})", "old-app-password-`touch {marker}`", "old-app-password-$HOME-${{PATH}}"],
    ids=["command-substitution", "backticks", "variable-expansion"],
)
def test_shell_syntax_in_an_old_password_stays_data(tmp_path: Path, payload: str) -> None:
    marker = tmp_path / "executed"
    old_app = payload.format(marker=marker)
    result, env_file, log_file = _run(tmp_path, env_text=_env_text(app=old_app), old_app=old_app)
    _assert_rotated(result, env_file, log_file)
    assert not marker.exists(), "an env value was executed as shell code"


_REQUIRED_VALUE_MUTATIONS = {
    "missing": lambda text: text.replace(f"POSTGRES_PASSWORD={OLD_APP}\n", ""),
    "duplicate-password": lambda text: text + f"POSTGRES_PASSWORD={OLD_APP}\n",
    "missing-bootstrap": lambda text: text.replace(f"POSTGRES_BOOTSTRAP_PASSWORD={OLD_BOOTSTRAP}\n", ""),
}
_NOT_CAUGHT_IN_PREFLIGHT = {
    "empty": lambda text: text.replace(f"POSTGRES_PASSWORD={OLD_APP}", "POSTGRES_PASSWORD="),
    "empty-double-quotes": lambda text: text.replace(f"POSTGRES_PASSWORD={OLD_APP}", 'POSTGRES_PASSWORD=""'),
    "empty-single-quotes": lambda text: text.replace(
        f"POSTGRES_ADMIN_PASSWORD={OLD_ADMIN}", "POSTGRES_ADMIN_PASSWORD=''"
    ),
    "duplicate-user": lambda text: text.replace(
        "POSTGRES_USER=anlaufstelle\n", "POSTGRES_USER=anlaufstelle\nPOSTGRES_USER=other\n"
    ),
}


@pytest.mark.parametrize("name", sorted(_REQUIRED_VALUE_MUTATIONS))
def test_missing_or_duplicate_password_aborts_before_any_change(tmp_path: Path, name: str) -> None:
    text = _REQUIRED_VALUE_MUTATIONS[name](_env_text())
    result, env_file, log_file = _run(tmp_path, env_text=text)
    _assert_rejected_before_any_change(result, env_file, log_file, text)
    assert "FEHLER" in result.stderr


@pytest.mark.parametrize("name", sorted(_NOT_CAUGHT_IN_PREFLIGHT))
def test_current_gap_empty_or_duplicate_value_is_not_stopped_in_preflight(tmp_path: Path, name: str) -> None:
    # Characterization of the present behaviour: the reader reports the problem inside a command
    # substitution, where errexit does not apply, so the run continues up to the consumer stop.
    text = _NOT_CAUGHT_IN_PREFLIGHT[name](_env_text())
    _result, _env_file, log_file = _run(tmp_path, env_text=text)
    assert " stop web cron" in log_file.read_text(encoding="utf-8")


def test_missing_env_file_aborts_before_any_change(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.env"
    result, _env_file, log_file = _run(tmp_path, env_file_override=str(missing))
    assert result.returncode != 0
    assert "Env-Datei fehlt" in result.stderr
    assert not log_file.exists()
    assert not missing.exists()


# Current behaviour for dotenv forms the shared reader (scripts/ops/lib/env-file.sh) handles differently.


def test_current_quoted_value_with_inline_comment_keeps_quotes_and_comment(tmp_path: Path) -> None:
    text = _env_text(app=f'"{OLD_APP}" # note')
    result, env_file, log_file = _run(tmp_path, env_text=text)
    # The negative login check receives the raw text, so it cannot recognize the old password.
    assert result.returncode != 0
    assert "sql:rotate" in log_file.read_text(encoding="utf-8")
    assert env_file.read_text(encoding="utf-8") == text
    assert "Rollback" in result.stderr


@pytest.mark.parametrize("prefix", ["export ", "  ", "  export  "], ids=["export", "indent", "indent-export"])
def test_current_export_prefix_or_indent_is_not_recognized(tmp_path: Path, prefix: str) -> None:
    text = _env_text().replace("POSTGRES_PASSWORD=", f"{prefix}POSTGRES_PASSWORD=")
    result, env_file, log_file = _run(tmp_path, env_text=text)
    _assert_rejected_before_any_change(result, env_file, log_file, text)


def test_current_whitespace_around_equals_is_not_recognized(tmp_path: Path) -> None:
    text = _env_text().replace("POSTGRES_PASSWORD=", "POSTGRES_PASSWORD = ")
    result, env_file, log_file = _run(tmp_path, env_text=text)
    _assert_rejected_before_any_change(result, env_file, log_file, text)


def test_current_backslash_escapes_in_double_quotes_stay_literal(tmp_path: Path) -> None:
    old_app = r"old-app-password-\$HOME-\"x"
    result, env_file, log_file = _run(tmp_path, env_text=_env_text(app=f'"{old_app}"'), old_app=old_app)
    _assert_rotated(result, env_file, log_file)


def test_current_unterminated_quote_is_kept_as_part_of_the_value(tmp_path: Path) -> None:
    old_app = '"old-app-password-never-closed'
    result, env_file, log_file = _run(tmp_path, env_text=_env_text(app=old_app), old_app=old_app)
    _assert_rotated(result, env_file, log_file)
