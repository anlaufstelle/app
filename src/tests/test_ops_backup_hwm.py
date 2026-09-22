"""Funktionale Shell-Nachweise fuer das Audit-HWM-Backup (Refs #2022)."""

from __future__ import annotations

import gzip
import hashlib
import hmac
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
OPS_DIR = REPO_ROOT / "scripts" / "ops"
BACKUP_SCRIPT = OPS_DIR / "backup.sh"
RESTORE_DRILL = OPS_DIR / "restore-drill.sh"
# Interpreter fuer den Docker-Shim: lokal das venv, auf CI-Runnern (ohne .venv)
# der laufende Interpreter — sonst meldet der Drill Schritt 2b faelschlich als
# Signaturfehler (Stage-CI v0.96.0, Test #133).
_VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
PYTHON = _VENV_PYTHON if _VENV_PYTHON.exists() else Path(sys.executable)

BACKUP_KEY = "not-a-placeholder-0123456789abcdef"
AUDIT_KEY = "audit-hwm-test-key-with-enough-entropy"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _backup_sandbox(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "backup-root"
    ops = root / "scripts" / "ops"
    ops.mkdir(parents=True)
    for name in ("backup.sh", "_backup_common.sh"):
        shutil.copy(OPS_DIR / name, ops / name)
    # Gemeinsame Satzpruefung (Refs #2292): backup.sh sourct scripts/ops/lib/.
    shutil.copytree(OPS_DIR / "lib", ops / "lib")
    (root / "docker-compose.prod.yml").write_text("services: {}\n", encoding="utf-8")
    (root / ".env").write_text(
        "POSTGRES_USER=appuser\n"
        "POSTGRES_DB=anlaufstelle\n"
        "POSTGRES_ADMIN_USER=admin\n"
        "POSTGRES_ADMIN_PASSWORD=admin-password\n"
        f"BACKUP_ENCRYPTION_KEY={BACKUP_KEY}\n"
        "DJANGO_AUDIT_HWM_PATH=/data/audit_hwm.json\n"
        "BACKUP_OFFSITE_TARGET=backup@example:/archive\n",
        encoding="utf-8",
    )

    bindir = tmp_path / "backup-bin"
    bindir.mkdir()
    docker_log = tmp_path / "backup-docker.log"
    scp_log = tmp_path / "backup-scp.log"
    _write_executable(
        bindir / "docker",
        """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$DOCKER_LOG"
if [[ "$*" == *" exec -T cron cat /data/audit_hwm.json"* ]]; then
    printf '%s\\n' "$TEST_HWM_JSON"
elif [[ "$*" == *"SELECT COUNT(*) FROM core_"* ]]; then
    printf '1\\n'
elif [[ "$*" == *" exec "* ]]; then
    printf 'FAKE-CONTAINER-OUTPUT\\n'
fi
exit 0
""",
    )
    _write_executable(
        bindir / "scp",
        """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$SCP_LOG"
exit "${TEST_OFFSITE_FAIL:-0}"
""",
    )
    for command in ("rclone", "aws"):
        _write_executable(
            bindir / command,
            """#!/usr/bin/env bash
printf '%s %s\\n' "${0##*/}" "$*" >> "$SCP_LOG"
exit "${TEST_OFFSITE_FAIL:-0}"
""",
        )
    docker_log.write_text("", encoding="utf-8")
    scp_log.write_text("", encoding="utf-8")
    return root, docker_log, scp_log


def test_backup_exports_hwm_into_daily_and_scp_offsite(tmp_path: Path) -> None:
    root, docker_log, scp_log = _backup_sandbox(tmp_path)
    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{tmp_path / 'backup-bin'}:{env['PATH']}",
            "DOCKER_LOG": str(docker_log),
            "SCP_LOG": str(scp_log),
            "TEST_HWM_JSON": '{"version":1,"chains":{},"sig":"signed"}',
        }
    )

    result = subprocess.run(
        ["bash", str(root / "scripts" / "ops" / "backup.sh")],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    hwm_files = list((root / "backups" / "daily").glob("audit_hwm-*.json"))
    assert len(hwm_files) == 1
    assert '"sig":"signed"' in hwm_files[0].read_text(encoding="utf-8")
    assert "exec -T cron cat /data/audit_hwm.json" in docker_log.read_text(encoding="utf-8")
    assert str(hwm_files[0].parent) in scp_log.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "target",
    ("backup@example:/archive", "rclone:remote:archive", "s3://bucket/archive"),
)
def test_offsite_transfer_failure_prevents_rotation_until_success(tmp_path: Path, target: str) -> None:
    root, docker_log, transfer_log = _backup_sandbox(tmp_path)
    env_file = root / ".env"
    env_file.write_text(
        env_file.read_text(encoding="utf-8").replace(
            "BACKUP_OFFSITE_TARGET=backup@example:/archive",
            f"BACKUP_OFFSITE_TARGET={target}",
        ),
        encoding="utf-8",
    )
    aged_files = []
    old_timestamp = 1_600_000_000
    for tier in ("daily", "weekly", "monthly"):
        aged = root / "backups" / tier / f"aged-{tier}.sql.gz.enc"
        aged.parent.mkdir(parents=True, exist_ok=True)
        aged.write_bytes(b"aged")
        os.utime(aged, (old_timestamp, old_timestamp))
        aged_files.append(aged)

    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{tmp_path / 'backup-bin'}:{env['PATH']}",
            "DOCKER_LOG": str(docker_log),
            "SCP_LOG": str(transfer_log),
            "TEST_HWM_JSON": '{"version":1,"chains":{},"sig":"signed"}',
            "TEST_OFFSITE_FAIL": "1",
        }
    )
    failed = subprocess.run(
        ["bash", str(root / "scripts" / "ops" / "backup.sh")],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )

    assert failed.returncode == 0, failed.stderr
    assert "Rotation ausgesetzt" in failed.stdout
    assert all(path.exists() for path in aged_files)

    env["TEST_OFFSITE_FAIL"] = "0"
    succeeded = subprocess.run(
        ["bash", str(root / "scripts" / "ops" / "backup.sh")],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )

    assert succeeded.returncode == 0, succeeded.stderr
    assert "Rotation abgeschlossen" in succeeded.stdout
    assert all(not path.exists() for path in aged_files)


def _write_prod_backup(
    backup_dir: Path,
    timestamp: str = "2026-09-02_020000",
    *,
    audit_count: int = 1,
) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_file = backup_dir / f"anlaufstelle_{timestamp}.sql.gz.enc"
    encrypted = subprocess.run(
        ["openssl", "enc", "-aes-256-cbc", "-salt", "-pbkdf2", "-pass", f"pass:{BACKUP_KEY}"],
        input=gzip.compress(b"SELECT 1;\n"),
        capture_output=True,
        check=True,
        timeout=60,
    ).stdout
    backup_file.write_bytes(encrypted)
    derived = hashlib.sha256(f"anlaufstelle-backup-hmac-v1:{BACKUP_KEY}".encode()).hexdigest()
    signature = hmac.new(derived.encode(), encrypted, hashlib.sha256).hexdigest()
    backup_file.with_name(f"{backup_file.name}.hmac").write_text(f"{signature}\n", encoding="utf-8")
    manifest = backup_dir / f"anlaufstelle_{timestamp}.manifest"
    tables = ("core_facility", "core_client", "core_event", "core_workitem", "core_auditlog", "core_attachment")
    if "MANIFEST_TIMESTAMP" in RESTORE_DRILL.read_text(encoding="utf-8"):
        payload = "".join(f"table_{table}={audit_count if table == 'core_auditlog' else 1}\n" for table in tables)
        payload += f"backup_timestamp={timestamp}\n"
        payload += f"db_sha256={hashlib.sha256(backup_file.read_bytes()).hexdigest()}\n"
    else:
        payload = "".join(f"{table}=1\n" for table in tables)
    manifest.write_text(payload, encoding="utf-8")
    manifest_signature = hmac.new(derived.encode(), manifest.read_bytes(), hashlib.sha256).hexdigest()
    manifest.with_name(f"{manifest.name}.hmac").write_text(f"{manifest_signature}\n", encoding="utf-8")


def _restore_sandbox(
    tmp_path: Path,
    *,
    hwm_payload: str | None,
    hwm_configured: bool = True,
    chain_error: str = "",
    timestamp: str | None = None,
    add_newer_backup: bool = False,
    foreign_manifest: bool = False,
    sql_error: bool = False,
    audit_count: int = 1,
) -> tuple[Path, Path, subprocess.CompletedProcess]:
    compose_dir = tmp_path / "restore-root"
    backup_dir = compose_dir / "backups" / "daily"
    _write_prod_backup(backup_dir, audit_count=audit_count)
    if add_newer_backup:
        _write_prod_backup(backup_dir, "2026-09-11_020000")
    if foreign_manifest:
        manifest = backup_dir / "anlaufstelle_2026-09-02_020000.manifest"
        payload = manifest.read_text(encoding="utf-8")
        if "backup_timestamp=" in payload:
            payload = payload.replace(
                "backup_timestamp=2026-09-02_020000",
                "backup_timestamp=2026-08-01_020000",
            )
        manifest.write_text(payload, encoding="utf-8")
        derived = hashlib.sha256(f"anlaufstelle-backup-hmac-v1:{BACKUP_KEY}".encode()).hexdigest()
        signature = hmac.new(derived.encode(), manifest.read_bytes(), hashlib.sha256).hexdigest()
        manifest.with_name(f"{manifest.name}.hmac").write_text(f"{signature}\n", encoding="utf-8")
    if hwm_payload is not None:
        (backup_dir / "audit_hwm-2026-09-02_020000.json").write_text(hwm_payload, encoding="utf-8")
    env_lines = [
        f"BACKUP_ENCRYPTION_KEY={BACKUP_KEY}",
        "POSTGRES_DB=anlaufstelle",
        f"DJANGO_AUDIT_HASH_KEY={AUDIT_KEY}",
        f"BACKUP_HOST_DIR={backup_dir.parent}",
    ]
    if hwm_configured:
        env_lines.append("DJANGO_AUDIT_HWM_PATH=/data/audit_hwm.json")
    (compose_dir / ".env").write_text("\n".join(env_lines) + "\n", encoding="utf-8")

    bindir = tmp_path / "restore-bin"
    bindir.mkdir()
    docker_log = tmp_path / "restore-docker.log"
    _write_executable(
        bindir / "docker",
        """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$DOCKER_LOG"
if [[ "$*" == *" --entrypoint sh maintenance "* && "$*" == *"verify_audit_chain"* ]]; then
    payload=$(cat)
    hwm_check=$(mktemp)
    printf '%s' "$payload" > "$hwm_check"
    if ! PYTHONPATH="$REAL_SRC" DJANGO_SETTINGS_MODULE=anlaufstelle.settings.test \
        DJANGO_AUDIT_HASH_KEY="$DJANGO_AUDIT_HASH_KEY" "$PYTHON_BIN" -c \
        'from core.services.audit.hwm import HighWaterMarkStore; import sys; HighWaterMarkStore.load(sys.argv[1])' \
        "$hwm_check"; then
        rm -f "$hwm_check"
        exit 1
    fi
    rm -f "$hwm_check"
    if [[ "${TEST_CHAIN_ERROR:-}" == "role" ]]; then
        printf 'CommandError: verify_audit_chain laeuft als RLS-gefilterte App-Rolle\\n' >&2
        exit 1
    fi
    if [[ "${TEST_CHAIN_ERROR:-}" == "mismatch" ]]; then
        printf 'CommandError: tail truncation\\n' >&2
        exit 1
    fi
    printf '1' > "$CHAIN_RAN_FILE"
    printf 'Alle Audit-Ketten intakt.\\n'
    exit 0
fi
if [[ " $* " == *" cp "* ]]; then
    while (($#)); do
        if [[ "$1" == "cp" ]]; then
            /bin/cp "$2" "${3#web:}"
            exit $?
        fi
        shift
    done
fi
if [[ "$*" == *" exec -T web sh -c cat > "* ]]; then
    command="${@: -1}"
    target="${command#*\\'}"
    target="${target%\\'}"
    cat > "$target"
    exit $?
fi
if [[ "$*" == *" exec -T web rm -f "* ]]; then
    /bin/rm -f "${@: -1}"
    exit 0
fi
if [[ "$*" == *" python manage.py shell -c "* ]]; then
    code="${@: -1}"
    PYTHONPATH="$REAL_SRC" DJANGO_SETTINGS_MODULE=anlaufstelle.settings.test \
        DJANGO_AUDIT_HASH_KEY="$DJANGO_AUDIT_HASH_KEY" "$PYTHON_BIN" -c "$code"
    exit $?
fi
if [[ "$*" == *"relrowsecurity"* ]]; then
    printf '20\\n'
elif [[ "$*" == *"FROM pg_trigger"* ]]; then
    printf '1\\n'
elif [[ "$*" == *"UPDATE core_auditlog"* ]]; then
    printf 'auditlog is immutable\\n' >&2
    exit 1
elif [[ "$*" == *"SELECT COUNT(*) FROM core_auditlog"* && -s "$CHAIN_RAN_FILE" ]]; then
    printf '%s\\n' "$((TEST_AUDIT_COUNT + 1))"
elif [[ "$*" == *"SELECT COUNT(*) FROM core_auditlog"* ]]; then
    printf '%s\\n' "$TEST_AUDIT_COUNT"
elif [[ "$*" == *"SELECT COUNT(*)"* ]]; then
    printf '1\\n'
elif [[ "$*" == *" exec -T db psql "* ]]; then
    cat >/dev/null
    if [[ "${TEST_SQL_ERROR:-0}" == "1" ]]; then
        printf 'ERROR: COPY core_eventhistory fehlgeschlagen\\n' >&2
        if [[ " $* " == *" ON_ERROR_STOP=1 "* ]]; then
            exit 1
        fi
    fi
fi
exit 0
""",
    )
    docker_log.write_text("", encoding="utf-8")
    env = dict(os.environ)
    if not hwm_configured:
        env.pop("DJANGO_AUDIT_HWM_PATH", None)
    env.update(
        {
            "PATH": f"{bindir}:{env['PATH']}",
            "COMPOSE_DIR": str(compose_dir),
            "ENV_FILE": str(compose_dir / ".env"),
            "DOCKER_LOG": str(docker_log),
            "REAL_SRC": str(REPO_ROOT / "src"),
            "PYTHON_BIN": str(PYTHON),
            "DJANGO_AUDIT_HASH_KEY": AUDIT_KEY,
            "TEST_CHAIN_ERROR": chain_error,
            "TEST_SQL_ERROR": "1" if sql_error else "0",
            "TEST_AUDIT_COUNT": str(audit_count),
            "CHAIN_RAN_FILE": str(tmp_path / "chain-ran"),
        }
    )
    args = ["bash", str(RESTORE_DRILL), "--prod"]
    if timestamp is not None:
        args.extend(["--timestamp", timestamp])
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    return backup_dir, docker_log, result


def test_restore_drill_reports_fail_for_tampered_hwm_and_continues(tmp_path: Path) -> None:
    _, docker_log, result = _restore_sandbox(
        tmp_path,
        hwm_payload='{"version":1,"chains":{},"sig":"manipulated"}',
    )

    assert result.returncode == 1, result.stdout
    assert "FAIL - Schritt 2b: Audit-HWM-Signaturpruefung fehlgeschlagen" in result.stdout
    assert "Schritt 3: Tabellen-Stichproben erfolgreich" in result.stdout
    assert "HighWaterMarkStore.load" in docker_log.read_text(encoding="utf-8")


def test_restore_drill_reports_fail_for_missing_hwm_and_continues(tmp_path: Path) -> None:
    _, _, result = _restore_sandbox(tmp_path, hwm_payload=None)

    assert result.returncode == 1, result.stdout
    assert "FAIL - Schritt 2b: Kein Audit-HWM-Backup" in result.stdout
    assert "Schritt 3: Tabellen-Stichproben erfolgreich" in result.stdout


def test_restore_drill_skips_hwm_when_not_configured_and_marks_restore(tmp_path: Path) -> None:
    _, docker_log, result = _restore_sandbox(
        tmp_path,
        hwm_payload=None,
        hwm_configured=False,
    )

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "SKIP - Schritt 2b: Audit-Anker NICHT geprueft (keine Anker-Konfiguration)" in result.stdout
    assert "Schritt 7: Restore-Test im AuditLog dokumentiert" in result.stdout
    assert "mark_restore_verified" in docker_log.read_text(encoding="utf-8")


def test_restore_drill_accepts_valid_hwm_signature(tmp_path: Path) -> None:
    signature = hmac.new(AUDIT_KEY.encode(), b"{}", hashlib.sha256).hexdigest()
    _, _, result = _restore_sandbox(
        tmp_path,
        hwm_payload=f'{{"version":1,"chains":{{}},"sig":"{signature}"}}',
    )

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "OK   - Schritt 2b: Audit-HWM-Signatur verifiziert" in result.stdout
    assert "Schritt 7: Restore-Test im AuditLog dokumentiert" in result.stdout


def test_restore_drill_logs_real_role_error_and_blocks_marker(tmp_path: Path) -> None:
    signature = hmac.new(AUDIT_KEY.encode(), b"{}", hashlib.sha256).hexdigest()
    _, docker_log, result = _restore_sandbox(
        tmp_path,
        hwm_payload=f'{{"version":1,"chains":{{}},"sig":"{signature}"}}',
        chain_error="role",
    )

    assert result.returncode == 1
    assert "RLS-gefilterte App-Rolle" in result.stdout
    assert "Rollen-/Verbindungsfehler" in result.stdout
    assert "mark_restore_verified" not in docker_log.read_text(encoding="utf-8")


def test_restore_drill_logs_chain_mismatch_and_blocks_marker(tmp_path: Path) -> None:
    signature = hmac.new(AUDIT_KEY.encode(), b"{}", hashlib.sha256).hexdigest()
    _, docker_log, result = _restore_sandbox(
        tmp_path,
        hwm_payload=f'{{"version":1,"chains":{{}},"sig":"{signature}"}}',
        chain_error="mismatch",
    )

    assert result.returncode == 1
    assert "CommandError: tail truncation" in result.stdout
    assert "Audit-HWM passt nicht zur restaurierten Kette" in result.stdout
    assert "mark_restore_verified" not in docker_log.read_text(encoding="utf-8")


def test_restore_drill_counts_before_successful_chain_command(tmp_path: Path) -> None:
    signature = hmac.new(AUDIT_KEY.encode(), b"{}", hashlib.sha256).hexdigest()
    _, docker_log, result = _restore_sandbox(
        tmp_path,
        hwm_payload=f'{{"version":1,"chains":{{}},"sig":"{signature}"}}',
    )

    assert result.returncode == 0, result.stdout
    actions = docker_log.read_text(encoding="utf-8")
    last_count = actions.rindex("SELECT COUNT(*) FROM core_attachment")
    chain = actions.index("verify_audit_chain")
    assert last_count < chain


def test_empty_audit_stays_open_after_successful_writing_chain_check(tmp_path: Path) -> None:
    signature = hmac.new(AUDIT_KEY.encode(), b"{}", hashlib.sha256).hexdigest()
    _, docker_log, result = _restore_sandbox(
        tmp_path,
        hwm_payload=f'{{"version":1,"chains":{{}},"sig":"{signature}"}}',
        audit_count=0,
    )

    assert result.returncode == 1, result.stdout
    assert "NICHT PRUEFBAR - Schritt 5: core_auditlog ist leer" in result.stdout
    assert "DRILL OFFEN" in result.stdout
    actions = docker_log.read_text(encoding="utf-8")
    assert "verify_audit_chain" in actions
    assert "mark_restore_verified" not in actions


def test_restore_drill_rejects_foreign_validly_signed_manifest(tmp_path: Path) -> None:
    _, docker_log, result = _restore_sandbox(
        tmp_path,
        hwm_payload=None,
        hwm_configured=False,
        foreign_manifest=True,
    )

    assert result.returncode == 1
    assert "Manifest-Satzkennung passt nicht" in result.stdout
    assert "mark_restore_verified" not in docker_log.read_text(encoding="utf-8")


def test_restore_drill_timestamp_selects_requested_not_latest_set(tmp_path: Path) -> None:
    _, docker_log, result = _restore_sandbox(
        tmp_path,
        hwm_payload=None,
        hwm_configured=False,
        timestamp="2026-09-02_020000",
        add_newer_backup=True,
    )

    assert result.returncode == 0, result.stdout
    assert "explizit gewaehlter Restore-Satz: 2026-09-02_020000" in result.stdout
    assert "Drill gegen DB-Backup:" in result.stdout
    assert "anlaufstelle_2026-09-02_020000.sql.gz.enc" in result.stdout
    assert "anlaufstelle_2026-09-11_020000.sql.gz.enc" not in result.stdout
    assert "mark_restore_verified" in docker_log.read_text(encoding="utf-8")


def test_prod_sql_error_requires_on_error_stop_and_blocks_marker(tmp_path: Path) -> None:
    _, docker_log, result = _restore_sandbox(
        tmp_path,
        hwm_payload=None,
        hwm_configured=False,
        sql_error=True,
    )

    assert result.returncode == 1
    assert "ERROR: COPY core_eventhistory fehlgeschlagen" in result.stdout
    assert "DB-Restore fehlgeschlagen" in result.stdout
    assert "mark_restore_verified" not in docker_log.read_text(encoding="utf-8")


def test_dev_count_mismatch_keeps_marker_blocked(tmp_path: Path) -> None:
    root = tmp_path / "dev-root"
    ops = root / "scripts" / "ops"
    ops.mkdir(parents=True)
    shutil.copy(RESTORE_DRILL, ops / "restore-drill.sh")
    shutil.copy(OPS_DIR / "_backup_common.sh", ops / "_backup_common.sh")
    (root / "docker-compose.dev.yml").write_text("services: {}\n", encoding="utf-8")
    backup_dir = tmp_path / "dev-backups"
    backup_dir.mkdir()
    backup = backup_dir / "dump-2026-09-02_020000.pgc.enc"
    encrypted = subprocess.run(
        ["openssl", "enc", "-aes-256-cbc", "-salt", "-pbkdf2", "-pass", f"pass:{BACKUP_KEY}"],
        input=b"custom-dump",
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout
    backup.write_bytes(encrypted)
    (root / ".env.dev").write_text(
        f"BACKUP_ENCRYPTION_KEY={BACKUP_KEY}\nPOSTGRES_DB=anlaufstelle\n",
        encoding="utf-8",
    )
    bindir = tmp_path / "dev-bin"
    bindir.mkdir()
    docker_log = tmp_path / "dev-docker.log"
    docker_log.write_text("", encoding="utf-8")
    _write_executable(
        bindir / "docker",
        """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$DOCKER_LOG"
if [[ "$*" == *" pg_restore "* ]]; then cat >/dev/null; exit 0; fi
if [[ "$*" == *"relrowsecurity"* ]]; then printf '20\\n'; exit 0; fi
if [[ "$*" == *"FROM pg_trigger"* ]]; then printf '1\\n'; exit 0; fi
if [[ "$*" == *"UPDATE core_auditlog"* ]]; then printf 'immutable\\n' >&2; exit 1; fi
if [[ "$*" == *"SELECT COUNT(*) FROM core_client"* ]]; then
    if [[ "$*" == *" -d anlaufstelle "* ]]; then printf '1\\n'; else printf '0\\n'; fi
    exit 0
fi
if [[ "$*" == *"SELECT COUNT(*)"* ]]; then printf '1\\n'; exit 0; fi
exit 0
""",
    )
    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{bindir}:{env['PATH']}",
            "COMPOSE_DIR": str(root),
            "ENV_FILE": str(root / ".env.dev"),
            "DEV_BACKUP_DIR": str(backup_dir),
            "DOCKER_LOG": str(docker_log),
        }
    )

    result = subprocess.run(
        ["bash", str(ops / "restore-drill.sh"), "--dev"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert result.returncode == 1, result.stdout
    assert "core_client: Restore=0, Live=1" in result.stdout
    assert "mark_restore_verified" not in docker_log.read_text(encoding="utf-8")
