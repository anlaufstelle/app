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
if [[ "$*" == *"HWM_CHECK="* ]]; then
    args=("$@")
    for index in "${!args[@]}"; do
        if [[ "${args[$index]}" == "-c" ]]; then
            args[$((index + 1))]="anchor-operation"
            break
        fi
    done
    printf 'docker %s\\n' "${args[*]}" >> "$ACTION_LOG"
    cat >/dev/null
    exit 0
fi
printf 'docker %s\\n' "$*" >> "$ACTION_LOG"
if [[ "$*" == *" run --rm --entrypoint sh -T maintenance -c "* && "$*" == *"MEDIA_ROOT"* ]]; then
    MEDIA_ROOT="${TEST_WEB_MEDIA_ROOT-/data/media}" sh -c "${@: -1}"
elif [[ "$*" == *" exec -T db psql "* ]]; then
    if [[ "$*" == *'RENAME TO "anlaufstelle_vor_restore_'* && "${TEST_ABORT_IN_SWITCH:-0}" == "1" ]]; then
        # Abbruch (SIGTERM) genau zwischen den beiden Umbenennungen.
        kill -TERM "$PPID"
        exit 0
    elif [[ "$*" == *"FROM pg_database"* ]]; then
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
elif [[ "$*" == *" run --rm --entrypoint sh -T maintenance "* ]]; then
    cat >/dev/null
    if [[ "${TEST_MEDIA_ACTIVATION_FAIL:-0}" == "1" && "$*" == *'mv "$stage" "$live"'* ]]; then
        printf 'mv: Zugriff verweigert\n' >&2
        exit 1
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
        "MEDIA_ROOT_CONTAINER=/data/media\n"
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
    anchor = manifest.with_name(f"audit_hwm-{manifest.stem.removeprefix('anlaufstelle_')}.json")
    payload = b'{"version":1,"chains":{},"sig":"signed"}'
    anchor.write_bytes(payload)
    manifest.write_text(manifest.read_text() + f"hwm_sha256={hashlib.sha256(payload).hexdigest()}\n")

    _sign(manifest)

    bindir = tmp_path / "bin"
    bindir.mkdir()
    action_log = tmp_path / "actions.log"
    action_log.write_text("", encoding="utf-8")
    _write_executable(bindir / "docker", DOCKER_STUB)
    return root, bindir, action_log, db_backup, media_backup


def _run(
    root: Path,
    bindir: Path,
    action_log: Path,
    db_backup: Path,
    media_backup: Path | None,
    extra_args: tuple[str, ...] = (),
    **overrides,
):
    env = dict(os.environ)
    env.pop("DJANGO_AUDIT_HWM_PATH", None)
    env.update({"PATH": f"{bindir}:{env['PATH']}", "ACTION_LOG": str(action_log), "RESTORE_CONFIRM": "ja"})
    env.update(overrides)
    args = ["bash", str(root / "scripts" / "ops" / "restore.sh"), *extra_args]
    args.append(str(db_backup))
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
    # Anchor preflight also uses maintenance; compare the actual media activation.
    assert _index(lines, f'RENAME TO "{TARGET_DB}";') < _index(lines, 'mv "$stage" "$live"')


def test_fresh_target_without_existing_database_needs_no_backup_copy(tmp_path: Path) -> None:
    root, bindir, action_log, db_backup, media_backup = _sandbox(tmp_path)

    result = _run(root, bindir, action_log, db_backup, media_backup, TEST_TARGET_EXISTS="")

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    lines = _lines(action_log)
    assert not [line for line in lines if f'ALTER DATABASE "{TARGET_DB}" RENAME TO' in line]
    assert [line for line in lines if f'RENAME TO "{TARGET_DB}";' in line]


def _remove_manifest(daily: Path) -> None:
    """A set without a signed manifest, e.g. an artefact copied without it."""
    (daily / f"anlaufstelle_{TIMESTAMP}.manifest").unlink()
    (daily / f"anlaufstelle_{TIMESTAMP}.manifest.hmac").unlink()


def test_set_without_manifest_is_refused_with_a_clear_message(tmp_path: Path) -> None:
    root, bindir, action_log, db_backup, media_backup = _sandbox(tmp_path)
    _remove_manifest(db_backup.parent)

    result = _run(root, bindir, action_log, db_backup, media_backup)

    assert result.returncode != 0
    assert "the mandatory manifest of the backup set is missing" in result.stderr
    assert "--allow-missing-manifest" not in result.stdout + result.stderr
    assert _lines(action_log) == []


def test_the_manifest_bypass_option_no_longer_exists(tmp_path: Path) -> None:
    root, bindir, action_log, db_backup, media_backup = _sandbox(tmp_path)

    with_manifest = _run(root, bindir, action_log, db_backup, media_backup, ("--allow-missing-manifest",))
    _remove_manifest(db_backup.parent)
    without_manifest = _run(root, bindir, action_log, db_backup, media_backup, ("--allow-missing-manifest",))

    for result in (with_manifest, without_manifest):
        assert result.returncode != 0
        assert "unknown option: --allow-missing-manifest" in result.stderr
        assert "allowed: --yes" in result.stderr
    assert _lines(action_log) == []


def test_usage_does_not_advertise_a_manifest_bypass(tmp_path: Path) -> None:
    root, bindir, action_log, db_backup, media_backup = _sandbox(tmp_path)

    result = subprocess.run(
        ["bash", str(root / "scripts" / "ops" / "restore.sh")],
        capture_output=True,
        text=True,
        cwd=root,
        timeout=30,
        stdin=subprocess.DEVNULL,
    )

    assert result.returncode != 0
    assert "Usage:" in result.stdout
    assert "manifest" not in result.stdout.lower()


def test_media_archive_without_manifest_binding_is_refused(tmp_path: Path) -> None:
    """DB-only-Satz + beliebiges Medienarchiv darf nicht durchlaufen (Satzbindung)."""
    root, bindir, action_log, db_backup, media_backup = _sandbox(tmp_path)
    manifest = db_backup.with_name(f"anlaufstelle_{TIMESTAMP}.manifest")
    lines = [line for line in manifest.read_text(encoding="utf-8").splitlines() if not line.startswith("media_sha256=")]
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _sign(manifest)

    result = _run(root, bindir, action_log, db_backup, media_backup)

    assert result.returncode != 0
    assert "bindet kein Medien-Archiv" in result.stderr
    assert _lines(action_log) == []


def test_abort_between_the_two_renames_keeps_the_verified_state(tmp_path: Path) -> None:
    root, bindir, action_log, db_backup, media_backup = _sandbox(tmp_path)

    result = _run(root, bindir, action_log, db_backup, media_backup, TEST_ABORT_IN_SWITCH="1")

    assert result.returncode != 0
    lines = _lines(action_log)
    assert [line for line in lines if f'ALTER DATABASE "{TARGET_DB}" RENAME TO "{TARGET_DB}_vor_restore_' in line]
    # Der verifizierte Wiederherstellungsstand wird NICHT verworfen ...
    drops = [index for index, line in enumerate(lines) if f'DROP DATABASE IF EXISTS "{TARGET_DB}_restore_' in line]
    switch = _index(lines, f'RENAME TO "{TARGET_DB}_vor_restore_')
    assert all(index < switch for index in drops)
    # ... und der bisherige Stand kommt unter den Zielnamen zurueck.
    rollback = [line for line in lines[switch + 1 :] if f'RENAME TO "{TARGET_DB}";' in line]
    assert rollback, lines[switch:]
    assert "Umschaltung" in result.stderr
    assert f"{TARGET_DB}_restore_" in result.stderr


ANCHOR_PATH = "/data/audit_hwm.json"


def test_set_with_manifest_still_requires_its_anchor(tmp_path: Path) -> None:
    """With a manifest and a configured anchor path the anchor is mandatory (fail-closed)."""
    root, bindir, action_log, db_backup, media_backup = _sandbox(tmp_path)

    # The standard fixture is complete; remove only its anchor to reproduce the failure.
    db_backup.with_name(f"audit_hwm-{TIMESTAMP}.json").unlink()

    result = _run(root, bindir, action_log, db_backup, media_backup, DJANGO_AUDIT_HWM_PATH=ANCHOR_PATH)

    assert result.returncode != 0
    assert "Audit-HWM fehlt" in result.stderr
    assert _lines(action_log) == []


def test_failed_media_activation_preserves_the_staging_for_repair(tmp_path: Path) -> None:
    root, bindir, action_log, db_backup, media_backup = _sandbox(tmp_path)

    result = _run(root, bindir, action_log, db_backup, media_backup, TEST_MEDIA_ACTIVATION_FAIL="1")

    assert result.returncode != 0
    lines = _lines(action_log)
    swap = _index(lines, 'mv "$stage" "$live"')
    cleanup = [line for line in lines[swap + 1 :] if "--entrypoint rm -T maintenance" in line and ".restore." in line]
    assert not cleanup, lines[swap:]
    assert "staging=/data/media.restore." in result.stderr


def _docker_calls(action_log: Path) -> list[str]:
    """First line of every logged call (embedded ``sh -c`` scripts span several lines)."""
    return [line for line in _lines(action_log) if line.startswith("docker ")]


def _compose_flags(root: Path, *names: str) -> str:
    return " ".join(f"-f {root.resolve() / name}" for name in names)


def test_every_compose_call_uses_the_full_file_list(tmp_path: Path) -> None:
    """Once any -f is given Compose skips compose.override.yml, so the list must be complete."""
    root, bindir, action_log, db_backup, media_backup = _sandbox(tmp_path)
    (root / "compose.legal.yml").write_text("services: {}\n", encoding="utf-8")
    (root / "compose.override.yml").write_text("services: {}\n", encoding="utf-8")
    expected = _compose_flags(root, "docker-compose.prod.yml", "compose.legal.yml", "compose.override.yml")

    result = _run(root, bindir, action_log, db_backup, media_backup)

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    calls = _docker_calls(action_log)
    assert calls
    for line in calls:
        assert line.startswith(f"docker compose {expected} "), line


def test_compose_calls_use_only_the_base_file_when_no_extra_file_exists(tmp_path: Path) -> None:
    root, bindir, action_log, db_backup, media_backup = _sandbox(tmp_path)
    expected = _compose_flags(root, "docker-compose.prod.yml")

    result = _run(root, bindir, action_log, db_backup, media_backup)

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    calls = _docker_calls(action_log)
    assert calls
    for line in calls:
        assert line.startswith(f"docker compose {expected} "), line
