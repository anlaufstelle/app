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
exit 0
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


def _write_prod_backup(backup_dir: Path) -> None:
    backup_dir.mkdir(parents=True)
    backup_file = backup_dir / "anlaufstelle_2026-09-02_020000.sql.gz.enc"
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
    manifest = backup_dir / "anlaufstelle_2026-09-02_020000.manifest"
    manifest.write_text(
        "core_facility=1\ncore_client=1\ncore_event=1\ncore_workitem=1\ncore_auditlog=1\ncore_attachment=1\n",
        encoding="utf-8",
    )
    manifest_signature = hmac.new(derived.encode(), manifest.read_bytes(), hashlib.sha256).hexdigest()
    manifest.with_name(f"{manifest.name}.hmac").write_text(f"{manifest_signature}\n", encoding="utf-8")


def _restore_sandbox(
    tmp_path: Path,
    *,
    hwm_payload: str | None,
    hwm_configured: bool = True,
) -> tuple[Path, Path, subprocess.CompletedProcess]:
    compose_dir = tmp_path / "restore-root"
    backup_dir = compose_dir / "backups" / "daily"
    _write_prod_backup(backup_dir)
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
elif [[ "$*" == *"SELECT COUNT(*)"* ]]; then
    printf '1\\n'
elif [[ "$*" == *" exec -T db psql "* ]]; then
    cat >/dev/null
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
        }
    )
    result = subprocess.run(
        ["bash", str(RESTORE_DRILL), "--prod"],
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
    assert "INFO - Schritt 2b: HWM nicht konfiguriert — uebersprungen" in result.stdout
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
