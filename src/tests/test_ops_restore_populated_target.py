"""Restore in eine bereits belegte, migrierte Zielinstallation (#2290).

Der Dump wird in eine frische Wiederherstellungs-Datenbank eingespielt, dort
gegen das signierte Manifest verifiziert und erst danach — unter Schreibruhe —
auf das Ziel umgeschaltet. Der vorhandene Zielbestand bleibt bis zur
Umschaltung unangetastet und danach als gesicherte Datenbank erhalten.
"""

from __future__ import annotations

import gzip
import hashlib
import hmac
import io
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
OPS_DIR = REPO_ROOT / "scripts" / "ops"
BACKUP_KEY = "restore-populated-key-0123456789abcdef"
TIMESTAMP = "2026-09-11_020000"
TARGET_DB = "anlaufstelle"
REAL_OPENSSL = shutil.which("openssl")

pytestmark = [pytest.mark.architecture, pytest.mark.skipif(REAL_OPENSSL is None, reason="openssl nicht installiert")]


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _sign(path: Path) -> None:
    derived = hashlib.sha256(f"anlaufstelle-backup-hmac-v1:{BACKUP_KEY}".encode()).hexdigest()
    signature = hmac.new(derived.encode(), path.read_bytes(), hashlib.sha256).hexdigest()
    path.with_name(f"{path.name}.hmac").write_text(f"{signature}\n", encoding="utf-8")


def _encrypt(payload: bytes, destination: Path) -> None:
    assert REAL_OPENSSL is not None
    encrypted = subprocess.run(
        [REAL_OPENSSL, "enc", "-aes-256-cbc", "-salt", "-pbkdf2", "-pass", f"pass:{BACKUP_KEY}"],
        input=payload,
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout
    destination.write_bytes(encrypted)
    _sign(destination)


def _media_payload() -> bytes:
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as archive:
        info = tarfile.TarInfo("media/1/alpha.bin")
        content = b"anhang"
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    return gzip.compress(payload.getvalue())


DOCKER_STUB = """#!/usr/bin/env bash
printf 'docker %s\\n' "$*" >> "$ACTION_LOG"
if [[ "$*" == *" exec -T web sh -c "* ]]; then
    MEDIA_ROOT="${TEST_WEB_MEDIA_ROOT-/data/media}" sh -c "${@: -1}"
elif [[ "$*" == *" exec -T db psql "* ]]; then
    if [[ "$*" == *"FROM pg_database"* ]]; then
        printf '%s\\n' "${TEST_TARGET_EXISTS-1}"
    elif [[ "$*" == *"SELECT facility_id, storage_filename"* ]]; then
        printf '1|alpha.bin\\n'
    elif [[ "$*" == *"SELECT COUNT(*)"* ]]; then
        printf '%s\\n' "${TEST_RESTORED_COUNT-1}"
    elif [[ "$*" == *"--single-transaction"* ]]; then
        cat >/dev/null
        if [[ "${TEST_IMPORT_FAIL:-0}" == "1" ]]; then
            printf 'ERROR: Relation core_event existiert bereits\\n' >&2
            exit 1
        fi
    else
        cat >/dev/null
    fi
elif [[ "$*" == *" run --rm "* || "$*" == *" exec -T web "* ]]; then
    cat >/dev/null
fi
exit 0
"""


def _sandbox(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    root = tmp_path / "root"
    ops = root / "scripts" / "ops"
    ops.mkdir(parents=True)
    for name in ("restore.sh", "_backup_common.sh"):
        shutil.copy(OPS_DIR / name, ops / name)
        (ops / name).chmod(0o755)
    if (OPS_DIR / "lib").is_dir():
        shutil.copytree(OPS_DIR / "lib", ops / "lib")
    (root / "docker-compose.prod.yml").write_text("services: {}\n", encoding="utf-8")
    (root / ".env").write_text(
        "POSTGRES_USER=appuser\n"
        "POSTGRES_ADMIN_USER=adminuser\n"
        f"POSTGRES_DB={TARGET_DB}\n"
        f"BACKUP_ENCRYPTION_KEY={BACKUP_KEY}\n",
        encoding="utf-8",
    )

    daily = root / "backups" / "daily"
    daily.mkdir(parents=True)
    db_backup = daily / f"anlaufstelle_{TIMESTAMP}.sql.gz.enc"
    media_backup = daily / f"anlaufstelle_{TIMESTAMP}_media.tar.gz.enc"
    _encrypt(gzip.compress(b"SELECT 1;\n"), db_backup)
    _encrypt(_media_payload(), media_backup)
    manifest = daily / f"anlaufstelle_{TIMESTAMP}.manifest"
    manifest.write_text(
        "".join(
            f"table_{tbl}=1\n"
            for tbl in (
                "core_facility",
                "core_client",
                "core_event",
                "core_workitem",
                "core_auditlog",
                "core_attachment",
            )
        )
        + f"backup_timestamp={TIMESTAMP}\n"
        + f"db_sha256={hashlib.sha256(db_backup.read_bytes()).hexdigest()}\n"
        + f"media_sha256={hashlib.sha256(media_backup.read_bytes()).hexdigest()}\n",
        encoding="utf-8",
    )
    _sign(manifest)

    bindir = tmp_path / "bin"
    bindir.mkdir()
    action_log = tmp_path / "actions.log"
    action_log.write_text("", encoding="utf-8")
    _write_executable(bindir / "docker", DOCKER_STUB)
    return root, bindir, action_log, db_backup, media_backup


def _run(root: Path, bindir: Path, action_log: Path, db_backup: Path, media_backup: Path | None, **overrides):
    env = dict(os.environ)
    env.pop("DJANGO_AUDIT_HWM_PATH", None)
    env.update({"PATH": f"{bindir}:{env['PATH']}", "ACTION_LOG": str(action_log), "RESTORE_CONFIRM": "ja"})
    env.update(overrides)
    args = ["bash", str(root / "scripts" / "ops" / "restore.sh"), str(db_backup)]
    if media_backup is not None:
        args.append(str(media_backup))
    return subprocess.run(args, capture_output=True, text=True, env=env, cwd=root, timeout=90, stdin=subprocess.DEVNULL)


def _lines(action_log: Path) -> list[str]:
    return action_log.read_text(encoding="utf-8").splitlines()


def _index(lines: list[str], needle: str) -> int:
    return next(index for index, line in enumerate(lines) if needle in line)


def test_populated_target_is_restored_via_recovery_database_and_switchover(tmp_path: Path) -> None:
    root, bindir, action_log, db_backup, media_backup = _sandbox(tmp_path)

    result = _run(root, bindir, action_log, db_backup, media_backup)

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    lines = _lines(action_log)
    create_pos = _index(lines, f'CREATE DATABASE "{TARGET_DB}_restore_')
    import_pos = _index(lines, "--single-transaction")
    stop_pos = _index(lines, "stop web cron")
    rename_old_pos = _index(lines, f'ALTER DATABASE "{TARGET_DB}" RENAME TO "{TARGET_DB}_vor_restore_')
    rename_new_pos = _index(lines, f'RENAME TO "{TARGET_DB}";')
    assert create_pos < import_pos < stop_pos < rename_old_pos < rename_new_pos
    # Das belegte Ziel wird nie geleert.
    assert not [line for line in lines if "dropdb" in line or f'DROP DATABASE IF EXISTS "{TARGET_DB}";' in line]
    assert "RESTORE-SATZ WIEDERHERGESTELLT" in result.stdout
    assert f"{TARGET_DB}_vor_restore_" in result.stdout


def test_import_error_leaves_target_and_old_database_untouched(tmp_path: Path) -> None:
    root, bindir, action_log, db_backup, media_backup = _sandbox(tmp_path)

    result = _run(root, bindir, action_log, db_backup, media_backup, TEST_IMPORT_FAIL="1")

    assert result.returncode != 0
    lines = _lines(action_log)
    assert not [line for line in lines if "RENAME TO" in line]
    assert not [line for line in lines if "dropdb" in line]
    # Die Wiederherstellungs-DB wird aufgeraeumt, das Ziel nicht angefasst.
    assert [line for line in lines if f'DROP DATABASE IF EXISTS "{TARGET_DB}_restore_' in line]


def test_incomplete_import_is_detected_against_manifest_before_switchover(tmp_path: Path) -> None:
    root, bindir, action_log, db_backup, media_backup = _sandbox(tmp_path)

    result = _run(root, bindir, action_log, db_backup, media_backup, TEST_RESTORED_COUNT="0")

    assert result.returncode != 0
    assert "Manifest" in result.stdout + result.stderr
    assert not [line for line in _lines(action_log) if "RENAME TO" in line]


def test_attachment_check_runs_against_the_recovery_database(tmp_path: Path) -> None:
    root, bindir, action_log, db_backup, media_backup = _sandbox(tmp_path)

    result = _run(root, bindir, action_log, db_backup, media_backup)

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    attachment_line = next(line for line in _lines(action_log) if "SELECT facility_id, storage_filename" in line)
    assert f"-d {TARGET_DB}_restore_" in attachment_line


def test_media_are_activated_after_the_database_switchover(tmp_path: Path) -> None:
    root, bindir, action_log, db_backup, media_backup = _sandbox(tmp_path)

    result = _run(root, bindir, action_log, db_backup, media_backup)

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    lines = _lines(action_log)
    assert _index(lines, f'RENAME TO "{TARGET_DB}";') < _index(lines, "maintenance")


def test_fresh_target_without_existing_database_needs_no_backup_copy(tmp_path: Path) -> None:
    root, bindir, action_log, db_backup, media_backup = _sandbox(tmp_path)

    result = _run(root, bindir, action_log, db_backup, media_backup, TEST_TARGET_EXISTS="")

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    lines = _lines(action_log)
    assert not [line for line in lines if f'ALTER DATABASE "{TARGET_DB}" RENAME TO' in line]
    assert [line for line in lines if f'RENAME TO "{TARGET_DB}";' in line]
