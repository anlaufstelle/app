"""Subprozess-Nachweise fuer Medien-HMAC-Reihenfolge und Restore-Drill (#2195)."""

from __future__ import annotations

import gzip
import hashlib
import hmac
import io
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
from django.test import override_settings

from core.services.audit.hwm import HighWaterMarkStore

REPO_ROOT = Path(__file__).resolve().parents[2]
OPS_DIR = REPO_ROOT / "scripts" / "ops"
BACKUP_KEY = "restore-media-order-key-0123456789abcdef"
AUDIT_KEY = "restore-hwm-key-0123456789abcdef"
REAL_OPENSSL = shutil.which("openssl")
pytestmark = [
    pytest.mark.architecture,
    pytest.mark.skipif(REAL_OPENSSL is None, reason="openssl nicht installiert"),
]


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _encrypt(payload: bytes, destination: Path) -> None:
    assert REAL_OPENSSL is not None
    encrypted = subprocess.run(
        [
            REAL_OPENSSL,
            "enc",
            "-aes-256-cbc",
            "-salt",
            "-pbkdf2",
            "-pass",
            f"pass:{BACKUP_KEY}",
        ],
        input=payload,
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout
    destination.write_bytes(encrypted)
    derived = hashlib.sha256(f"anlaufstelle-backup-hmac-v1:{BACKUP_KEY}".encode()).hexdigest()
    signature = hmac.new(derived.encode(), encrypted, hashlib.sha256).hexdigest()
    destination.with_name(f"{destination.name}.hmac").write_text(f"{signature}\n", encoding="utf-8")


def _media_payload(entries: tuple[tuple[str, bytes], ...] | None = None) -> bytes:
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as archive:
        archive_entries = entries or (("media/alpha.txt", b"vier"), ("media/nested/beta.txt", b"fuenf"))
        for name, content in archive_entries:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return gzip.compress(payload.getvalue())


def _base_env(root: Path, bindir: Path, action_log: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("DJANGO_AUDIT_HWM_PATH", None)
    env.update(
        {
            "PATH": f"{bindir}:{env['PATH']}",
            "ACTION_LOG": str(action_log),
            "REAL_OPENSSL": str(REAL_OPENSSL),
            "BACKUP_ENCRYPTION_KEY": BACKUP_KEY,
            "POSTGRES_USER": "appuser",
            "POSTGRES_DB": "anlaufstelle",
            "COMPOSE_DIR": str(root),
            "ENV_FILE": str(root / ".env"),
            "BACKUP_HOST_DIR": str(root / "backups"),
        }
    )
    return env


def _make_sandbox(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    root = tmp_path / "root"
    ops = root / "scripts" / "ops"
    ops.mkdir(parents=True)
    for name in ("restore.sh", "restore-drill.sh", "_backup_common.sh"):
        shutil.copy(OPS_DIR / name, ops / name)
        (ops / name).chmod(0o755)
    # Gemeinsame Satzpruefung (Refs #2290): restore.sh sourct scripts/ops/lib/.
    shutil.copytree(OPS_DIR / "lib", ops / "lib")
    (root / "docker-compose.prod.yml").write_text("services: {}\n", encoding="utf-8")
    (root / ".env").write_text(
        "POSTGRES_USER=appuser\n"
        "POSTGRES_DB=anlaufstelle\n"
        f"BACKUP_ENCRYPTION_KEY={BACKUP_KEY}\n"
        f"BACKUP_HOST_DIR={root / 'backups'}\n",
        encoding="utf-8",
    )

    daily = root / "backups" / "daily"
    daily.mkdir(parents=True)
    db_backup = daily / "anlaufstelle_2026-09-10_020000.sql.gz.enc"
    media_backup = daily / "anlaufstelle_2026-09-10_020000_media.tar.gz.enc"
    _encrypt(gzip.compress(b"SELECT 1;\n"), db_backup)
    _encrypt(_media_payload(), media_backup)
    manifest = daily / "anlaufstelle_2026-09-10_020000.manifest"
    manifest.write_text(
        "table_core_facility=1\n"
        "table_core_client=1\n"
        "table_core_event=1\n"
        "table_core_workitem=1\n"
        "table_core_auditlog=1\n"
        "table_core_attachment=1\n"
        "backup_timestamp=2026-09-10_020000\n"
        f"db_sha256={hashlib.sha256(db_backup.read_bytes()).hexdigest()}\n"
        f"media_sha256={hashlib.sha256(media_backup.read_bytes()).hexdigest()}\n",
        encoding="utf-8",
    )
    derived = hashlib.sha256(f"anlaufstelle-backup-hmac-v1:{BACKUP_KEY}".encode()).hexdigest()
    signature = hmac.new(derived.encode(), manifest.read_bytes(), hashlib.sha256).hexdigest()
    manifest.with_name(f"{manifest.name}.hmac").write_text(f"{signature}\n", encoding="utf-8")

    bindir = tmp_path / "bin"
    bindir.mkdir()
    action_log = tmp_path / "actions.log"
    action_log.write_text("", encoding="utf-8")
    _write_executable(
        bindir / "openssl",
        """#!/usr/bin/env bash
if [[ "${1:-}" == "dgst" && " $* " == *" -hmac "* ]]; then
    printf 'hmac %s\\n' "$*" >> "$ACTION_LOG"
fi
exec "$REAL_OPENSSL" "$@"
""",
    )
    _write_executable(
        bindir / "docker",
        """#!/usr/bin/env bash
printf 'docker %s\\n' "$*" >> "$ACTION_LOG"
if [[ "$*" == *" exec -T web sh -c "* ]]; then
    if [[ "${TEST_WEB_MEDIA_ROOT_ERROR:-0}" == "1" ]]; then
        printf 'MEDIA_ROOT konnte nicht gelesen werden\\n' >&2
        exit 1
    fi
    MEDIA_ROOT="${TEST_WEB_MEDIA_ROOT-/data/media}" sh -c "${@: -1}"
elif [[ "$*" == *" exec -T db psql "* ]]; then
    if [[ "$*" == *"relrowsecurity"* ]]; then
        printf '20\\n'
    elif [[ "$*" == *"FROM pg_trigger"* ]]; then
        printf '1\\n'
    elif [[ "$*" == *"UPDATE core_auditlog"* ]]; then
        printf 'auditlog is immutable\\n' >&2
        exit 1
    elif [[ "$*" == *"SELECT COUNT(*)"* ]]; then
        printf '1\\n'
    else
        cat >/dev/null
        if [[ "${TEST_PSQL_FAIL:-0}" == "1" ]]; then
            printf 'ERROR: kaputte SQL-Zeile\\n' >&2
            if [[ " $* " == *" ON_ERROR_STOP=1 "* ]]; then
                exit 1
            fi
            exit 0
        fi
    fi
elif [[ "$*" == *" python manage.py mark_restore_verified "* ]]; then
    exit 0
elif [[ "$*" == *" tar -C "*" -xf -"* ]]; then
    cat >/dev/null
fi
exit 0
""",
    )
    return root, bindir, action_log, db_backup, media_backup


def _rewrite_manifest_media_hash(manifest: Path, value: str | None) -> None:
    lines = [line for line in manifest.read_text(encoding="utf-8").splitlines() if not line.startswith("media_sha256=")]
    if value is not None:
        lines.append(f"media_sha256={value}")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    derived = hashlib.sha256(f"anlaufstelle-backup-hmac-v1:{BACKUP_KEY}".encode()).hexdigest()
    signature = hmac.new(derived.encode(), manifest.read_bytes(), hashlib.sha256).hexdigest()
    manifest.with_name(f"{manifest.name}.hmac").write_text(f"{signature}\n", encoding="utf-8")


def _run_restore(
    root: Path, env: dict[str, str], db_backup: Path, media_backup: Path | None
) -> subprocess.CompletedProcess[str]:
    args = ["bash", str(root / "scripts" / "ops" / "restore.sh"), str(db_backup)]
    if media_backup is not None:
        args.append(str(media_backup))
    return subprocess.run(
        args,
        input="ja\n",
        capture_output=True,
        text=True,
        env=env,
        cwd=root,
        timeout=30,
    )


def test_tampered_media_sidecar_stops_before_database_restore_or_media_delete(tmp_path: Path) -> None:
    root, bindir, action_log, db_backup, media_backup = _make_sandbox(tmp_path)
    media_backup.with_name(f"{media_backup.name}.hmac").write_text("0" * 64 + "\n", encoding="utf-8")

    result = _run_restore(root, _base_env(root, bindir, action_log), db_backup, media_backup)

    assert result.returncode != 0
    actions = action_log.read_text(encoding="utf-8")
    assert " psql " not in actions
    assert "rm -rf" not in actions


def test_valid_sidecars_are_checked_before_destructive_actions(tmp_path: Path) -> None:
    root, bindir, action_log, db_backup, media_backup = _make_sandbox(tmp_path)

    result = _run_restore(root, _base_env(root, bindir, action_log), db_backup, media_backup)

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    actions = action_log.read_text(encoding="utf-8").splitlines()
    hmac_positions = [index for index, line in enumerate(actions) if line.startswith("hmac ")]
    # Refs #2318: die erste psql-Zeile ist seit der Zielvorbereitung im Skript das
    # Beenden der Verbindungen; der Guard meint den Import selbst.
    psql_pos = next(index for index, line in enumerate(actions) if "--single-transaction" in line)
    media_delete_pos = next(index for index, line in enumerate(actions) if "rm -rf" in line)
    assert len(hmac_positions) == 5
    assert hmac_positions[0] < hmac_positions[1] < hmac_positions[2] < hmac_positions[3] < psql_pos
    assert psql_pos < hmac_positions[4] < media_delete_pos


def test_media_root_mismatch_stops_before_confirmation_or_destructive_actions(tmp_path: Path) -> None:
    root, bindir, action_log, db_backup, media_backup = _make_sandbox(tmp_path)
    env = _base_env(root, bindir, action_log)
    env["MEDIA_ROOT_CONTAINER"] = "/data/media"
    env["TEST_WEB_MEDIA_ROOT"] = "/data/media-abweichend"

    result = _run_restore(root, env, db_backup, media_backup)

    assert result.returncode != 0
    assert "MEDIA_ROOT='/data/media-abweichend', MEDIA_ROOT_CONTAINER='/data/media'." in result.stderr
    assert ".env" in result.stderr
    assert "WARNUNG:" not in result.stdout
    actions = action_log.read_text(encoding="utf-8")
    assert " psql " not in actions
    assert " mv " not in actions
    assert " tar " not in actions


def test_media_root_with_trailing_newline_is_not_treated_as_equal(tmp_path: Path) -> None:
    """Ein abschliessender Zeilenumbruch im Container-MEDIA_ROOT ist eine Abweichung.

    Die Kommandosubstitution in der Shell entfernt abschliessende Zeilenumbrueche;
    ohne Schutz wuerde ``/data/media\n`` faelschlich als ``/data/media`` gelten und
    der Restore die Medien am App-Pfad vorbei ablegen (Refs
    https://github.com/anlaufstelle/app/issues/2024).
    """
    root, bindir, action_log, db_backup, media_backup = _make_sandbox(tmp_path)
    env = _base_env(root, bindir, action_log)
    env["MEDIA_ROOT_CONTAINER"] = "/data/media"
    env["TEST_WEB_MEDIA_ROOT"] = "/data/media\n"

    result = _run_restore(root, env, db_backup, media_backup)

    assert result.returncode != 0
    assert "MEDIA_ROOT_CONTAINER='/data/media'." in result.stderr
    assert "WARNUNG:" not in result.stdout
    actions = action_log.read_text(encoding="utf-8")
    assert " psql " not in actions
    assert " mv " not in actions
    assert " tar " not in actions


def test_matching_media_roots_pass_precheck_before_confirmation(tmp_path: Path) -> None:
    root, bindir, action_log, db_backup, media_backup = _make_sandbox(tmp_path)
    env = _base_env(root, bindir, action_log)
    env["MEDIA_ROOT_CONTAINER"] = "/data/media"
    env["TEST_WEB_MEDIA_ROOT"] = "/data/media"

    result = _run_restore(root, env, db_backup, media_backup)

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    actions = action_log.read_text(encoding="utf-8").splitlines()
    precheck_pos = next(index for index, line in enumerate(actions) if " exec -T web sh -c " in line)
    psql_pos = next(index for index, line in enumerate(actions) if " psql " in line)
    assert precheck_pos < psql_pos


@pytest.mark.parametrize("failure_mode", ["empty", "error"])
def test_unavailable_web_media_root_stops_before_destructive_actions(tmp_path: Path, failure_mode: str) -> None:
    root, bindir, action_log, db_backup, media_backup = _make_sandbox(tmp_path)
    env = _base_env(root, bindir, action_log)
    env["MEDIA_ROOT_CONTAINER"] = "/data/media"
    if failure_mode == "empty":
        env["TEST_WEB_MEDIA_ROOT"] = ""
    else:
        env["TEST_WEB_MEDIA_ROOT_ERROR"] = "1"

    result = _run_restore(root, env, db_backup, media_backup)

    assert result.returncode != 0
    assert "MEDIA_ROOT" in result.stderr
    assert "/data/media" in result.stderr
    assert ".env" in result.stderr
    assert "WARNUNG:" not in result.stdout
    actions = action_log.read_text(encoding="utf-8")
    assert " psql " not in actions
    assert " mv " not in actions
    assert " tar " not in actions


def test_database_restore_error_stops_before_hwm_or_media_changes(tmp_path: Path) -> None:
    root, bindir, action_log, db_backup, media_backup = _make_sandbox(tmp_path)
    env = _base_env(root, bindir, action_log)
    env["TEST_PSQL_FAIL"] = "1"

    result = _run_restore(root, env, db_backup, media_backup)

    assert result.returncode != 0
    assert "DB-Wiederherstellung abgeschlossen." not in result.stdout
    actions = action_log.read_text(encoding="utf-8")
    assert " tar -C /data/media.restore." not in actions
    assert " mv /data/media.restore." not in actions


def test_restore_drill_extracts_and_reports_matching_media_archive(tmp_path: Path) -> None:
    root, bindir, action_log, _, _ = _make_sandbox(tmp_path)
    env = _base_env(root, bindir, action_log)
    media_tmp = root / "media-tmp"
    media_tmp.mkdir()
    env["TMPDIR"] = str(media_tmp)

    result = subprocess.run(
        ["bash", str(root / "scripts" / "ops" / "restore-drill.sh"), "--prod"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=env,
        cwd=root,
        timeout=30,
    )

    # Refs #2316: ohne Anker-Konfiguration ist Schritt 2b ein SKIP -> Exit-Code 2.
    assert result.returncode == 2, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "anlaufstelle_2026-09-10_020000_media.tar.gz.enc: 2 Dateien, 9 Bytes, HMAC ok" in result.stdout
    assert list(media_tmp.iterdir()) == []


def test_restore_drill_rejects_manifest_media_when_archive_is_missing(tmp_path: Path) -> None:
    root, bindir, action_log, _, media_backup = _make_sandbox(tmp_path)
    media_backup.unlink()
    media_backup.with_name(f"{media_backup.name}.hmac").unlink()

    result = subprocess.run(
        ["bash", str(root / "scripts" / "ops" / "restore-drill.sh"), "--prod"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=_base_env(root, bindir, action_log),
        cwd=root,
        timeout=30,
    )

    assert result.returncode == 1
    assert "Satz unvollstaendig: Medien-Archiv fehlt" in result.stdout
    assert "mark_restore_verified" not in action_log.read_text(encoding="utf-8")


def test_restore_drill_rejects_manifest_media_when_sidecar_is_missing(tmp_path: Path) -> None:
    root, bindir, action_log, _, media_backup = _make_sandbox(tmp_path)
    media_backup.with_name(f"{media_backup.name}.hmac").unlink()

    result = subprocess.run(
        ["bash", str(root / "scripts" / "ops" / "restore-drill.sh"), "--prod"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=_base_env(root, bindir, action_log),
        cwd=root,
        timeout=30,
    )

    assert result.returncode == 1
    assert "Satz unvollstaendig: Medien-Sidecar fehlt" in result.stdout
    assert "mark_restore_verified" not in action_log.read_text(encoding="utf-8")


def test_restore_drill_allows_database_only_manifest_without_media_hash(tmp_path: Path) -> None:
    root, bindir, action_log, _, media_backup = _make_sandbox(tmp_path)
    manifest = media_backup.with_name("anlaufstelle_2026-09-10_020000.manifest")
    _rewrite_manifest_media_hash(manifest, None)
    media_backup.unlink()
    media_backup.with_name(f"{media_backup.name}.hmac").unlink()

    result = subprocess.run(
        ["bash", str(root / "scripts" / "ops" / "restore-drill.sh"), "--prod"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=_base_env(root, bindir, action_log),
        cwd=root,
        timeout=30,
    )

    # Refs #2316: ohne Anker-Konfiguration ist Schritt 2b ein SKIP -> Exit-Code 2.
    assert result.returncode == 2, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "DB-only-Backup zulaessig" in result.stdout
    # Refs #2341: Eine Luecke im Lauf schreibt keinen Compliance-Nachweis mehr;
    # geprueft wird hier, dass der DB-only-Satz nicht am Medien-Hash scheitert.
    assert "mark_restore_verified" not in action_log.read_text(encoding="utf-8")


def test_restore_drill_rejects_media_with_wrong_manifest_hash(tmp_path: Path) -> None:
    root, bindir, action_log, _, media_backup = _make_sandbox(tmp_path)
    manifest = media_backup.with_name("anlaufstelle_2026-09-10_020000.manifest")
    _rewrite_manifest_media_hash(manifest, "0" * 64)

    result = subprocess.run(
        ["bash", str(root / "scripts" / "ops" / "restore-drill.sh"), "--prod"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=_base_env(root, bindir, action_log),
        cwd=root,
        timeout=30,
    )

    assert result.returncode == 1
    assert "Manifest-Medien-Hash passt nicht" in result.stdout
    assert "mark_restore_verified" not in action_log.read_text(encoding="utf-8")


def test_restore_rejects_manifest_media_when_archive_argument_is_missing(tmp_path: Path) -> None:
    root, bindir, action_log, db_backup, _ = _make_sandbox(tmp_path)

    result = _run_restore(root, _base_env(root, bindir, action_log), db_backup, None)

    assert result.returncode == 1
    assert "Satz unvollstaendig: Medien-Archiv fehlt" in result.stderr
    assert " psql " not in action_log.read_text(encoding="utf-8")


def test_restore_allows_database_only_manifest_without_media_hash(tmp_path: Path) -> None:
    root, bindir, action_log, db_backup, media_backup = _make_sandbox(tmp_path)
    manifest = db_backup.with_name("anlaufstelle_2026-09-10_020000.manifest")
    _rewrite_manifest_media_hash(manifest, None)

    result = _run_restore(root, _base_env(root, bindir, action_log), db_backup, None)

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    actions = action_log.read_text(encoding="utf-8")
    assert " psql " in actions
    assert " exec -T web sh -c " not in actions


def test_restore_rejects_media_with_wrong_manifest_hash_before_database_import(tmp_path: Path) -> None:
    root, bindir, action_log, db_backup, media_backup = _make_sandbox(tmp_path)
    manifest = db_backup.with_name("anlaufstelle_2026-09-10_020000.manifest")
    _rewrite_manifest_media_hash(manifest, "0" * 64)

    result = _run_restore(root, _base_env(root, bindir, action_log), db_backup, media_backup)

    assert result.returncode == 1
    assert "Manifest-Medien-Hash passt nicht" in result.stderr
    assert " psql " not in action_log.read_text(encoding="utf-8")


def test_restore_drill_fails_before_extract_when_tmpdir_space_is_too_small(tmp_path: Path) -> None:
    root, bindir, action_log, _, _ = _make_sandbox(tmp_path)
    media_tmp = root / "media-tmp"
    media_tmp.mkdir()
    _write_executable(
        bindir / "df",
        """#!/usr/bin/env bash
printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\n'
printf 'stub 1 1 0 100%% %s\\n' "${@: -1}"
""",
    )
    env = _base_env(root, bindir, action_log)
    env["TMPDIR"] = str(media_tmp)

    result = subprocess.run(
        ["bash", str(root / "scripts" / "ops" / "restore-drill.sh"), "--prod"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=env,
        cwd=root,
        timeout=30,
    )

    assert result.returncode == 1
    assert "Nicht genug freier Platz" in result.stdout
    assert "TMPDIR" in result.stdout
    assert "Medien: 2 Dateien" not in result.stdout


def _install_real_media_docker(bindir: Path) -> None:
    _write_executable(
        bindir / "docker",
        """#!/usr/bin/env bash
printf 'docker %s\\n' "$*" >> "$ACTION_LOG"
if [[ "$*" == *" exec -T web sh -c "* ]]; then
    MEDIA_ROOT="${TEST_WEB_MEDIA_ROOT-/data/media}" sh -c "${@: -1}"
    exit 0
fi
if [[ "$*" == *" exec -T db psql "* ]]; then
    if [[ "$*" == *"SELECT facility_id, storage_filename"* ]]; then
        printf '%s\\n' "$ATTACHMENT_ROWS"
    elif [[ "$*" == *"SELECT storage_filename"* ]]; then
        printf '%s\\n' "${ATTACHMENT_ROWS#*|}"
    elif [[ "$*" == *"FROM pg_database"* ]]; then
        printf '1\\n'
    elif [[ "$*" == *"SELECT COUNT(*)"* ]]; then
        printf '1\\n'
    else
        cat >/dev/null
    fi
    exit 0
fi
if [[ "$*" == *" run --rm --entrypoint sh -T maintenance "* ]]; then
    while [[ "$1" != "-c" ]]; do shift; done
    script="$2"
    shift 2
    mapped=()
    for arg in "$@"; do
        mapped+=("${arg//$CONTAINER_MEDIA_ROOT/$HOST_MEDIA_ROOT}")
    done
    /bin/sh -c "$script" "${mapped[@]}"
    exit $?
fi
if [[ "$*" == *" exec -T web "* ]]; then
    while [[ "$1" != "web" ]]; do shift; done
    shift
    mapped=()
    for arg in "$@"; do
        mapped+=("${arg//$CONTAINER_MEDIA_ROOT/$HOST_MEDIA_ROOT}")
    done
    "${mapped[@]}"
    exit $?
fi
exit 0
""",
    )


@pytest.mark.parametrize(
    ("entries", "expected_success"),
    [
        ((("media/facility-123/document.enc", b"ciphertext"),), True),
        ((("media/document.enc", b"wrong-root"),), False),
    ],
)
def test_restore_checks_real_archive_at_facility_path(
    tmp_path: Path,
    entries: tuple[tuple[str, bytes], ...],
    expected_success: bool,
) -> None:
    root, bindir, action_log, db_backup, media_backup = _make_sandbox(tmp_path)
    _encrypt(_media_payload(entries), media_backup)
    manifest = db_backup.with_name("anlaufstelle_2026-09-10_020000.manifest")
    _rewrite_manifest_media_hash(manifest, hashlib.sha256(media_backup.read_bytes()).hexdigest())
    _install_real_media_docker(bindir)
    host_media = tmp_path / "container" / "media"
    host_media.mkdir(parents=True)
    (host_media / "old.enc").write_bytes(b"old")
    env = _base_env(root, bindir, action_log)
    env.update(
        {
            "ATTACHMENT_ROWS": "facility-123|document.enc",
            "HOST_MEDIA_ROOT": str(host_media),
            "CONTAINER_MEDIA_ROOT": "/data/media",
        }
    )

    result = _run_restore(root, env, db_backup, media_backup)

    assert (result.returncode == 0) is expected_success, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    if expected_success:
        assert (host_media / "facility-123" / "document.enc").read_bytes() == b"ciphertext"
        assert not (host_media / "old.enc").exists()
    else:
        assert (host_media / "old.enc").read_bytes() == b"old"
        assert "facility-123/document.enc" in result.stderr


def _install_hwm_restore_docker(bindir: Path) -> None:
    _write_executable(
        bindir / "python",
        """#!/usr/bin/env bash
if [[ "${1:-}" == "manage.py" && "${2:-}" == "shell" && "${3:-}" == "-c" ]]; then
    exec "$PYTHON_BIN" -c "$4"
fi
exec "$PYTHON_BIN" "$@"
""",
    )
    _write_executable(
        bindir / "docker",
        """#!/usr/bin/env bash
printf 'docker %s\\n' "$*" >> "$ACTION_LOG"
if [[ "$*" == *" run --rm --entrypoint sh -T maintenance "* ]]; then
    payload=$(cat)
    count=0
    [[ -f "$HWM_CALL_COUNT" ]] && count=$(cat "$HWM_CALL_COUNT")
    count=$((count + 1))
    printf '%s' "$count" > "$HWM_CALL_COUNT"
    while [[ "$1" != "-c" ]]; do shift; done
    script="$2"
    shift 2
    runner="$1"; mode="$2"
    shift 3
    if [[ "${TEST_HWM_STREAM_FAIL:-0}" == "1" && "$count" -eq 2 ]]; then
        printf 'parti' | /bin/sh -c "$script" "$runner" "$mode" "$HOST_HWM_TARGET" "$@"
        exit $?
    fi
    printf '%s' "$payload" | /bin/sh -c "$script" "$runner" "$mode" "$HOST_HWM_TARGET" "$@"
    exit $?
fi
if [[ "$*" == *" exec -T db psql "* ]]; then
    if [[ "$*" == *"SELECT COUNT(*)"* ]]; then
        printf '1\\n'
        exit 0
    fi
    cat >/dev/null
    exit 0
fi
exit 0
""",
    )


def _run_hwm_restore(tmp_path: Path, payload: bytes, *, stream_fails: bool = False):
    root, bindir, action_log, db_backup, _ = _make_sandbox(tmp_path)
    manifest = db_backup.with_name("anlaufstelle_2026-09-10_020000.manifest")
    _rewrite_manifest_media_hash(manifest, None)
    hwm_source = db_backup.parent / "audit_hwm-2026-09-10_020000.json"
    hwm_source.write_bytes(payload)
    hwm_target = tmp_path / "audit_hwm.json"
    hwm_target.write_bytes(b"old-anchor")
    with (root / ".env").open("a", encoding="utf-8") as env_file:
        env_file.write("DJANGO_AUDIT_HWM_PATH=/data/audit_hwm.json\n")
    _install_hwm_restore_docker(bindir)
    env = _base_env(root, bindir, action_log)
    env.update(
        {
            "DJANGO_AUDIT_HWM_PATH": "/data/audit_hwm.json",
            "HOST_HWM_TARGET": str(hwm_target),
            "HWM_CALL_COUNT": str(tmp_path / "hwm-calls"),
            "PYTHON_BIN": str(Path(os.sys.executable)),
            "PYTHONPATH": str(REPO_ROOT / "src"),
            "DJANGO_SETTINGS_MODULE": "anlaufstelle.settings.test",
            "DJANGO_AUDIT_HASH_KEY": AUDIT_KEY,
            "TEST_HWM_STREAM_FAIL": "1" if stream_fails else "0",
        }
    )
    result = subprocess.run(
        ["bash", str(root / "scripts" / "ops" / "restore.sh"), str(db_backup)],
        input="ja\n",
        capture_output=True,
        text=True,
        env=env,
        cwd=root,
        timeout=30,
    )
    return result, hwm_target, action_log


def _signed_hwm_payload(tmp_path: Path) -> bytes:
    payload_path = tmp_path / "signed-hwm.json"
    with override_settings(AUDIT_HASH_KEY=AUDIT_KEY):
        HighWaterMarkStore(path=str(payload_path), chains={}).save()
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    assert payload["sig"] != "signed"
    return payload_path.read_bytes()


def test_empty_hwm_source_fails_without_changing_anchor(tmp_path: Path) -> None:
    result, hwm_target, action_log = _run_hwm_restore(tmp_path, b"")

    assert result.returncode != 0
    assert hwm_target.read_bytes() == b"old-anchor"
    assert " psql " not in action_log.read_text(encoding="utf-8")


def test_invalid_hwm_json_fails_without_changing_anchor(tmp_path: Path) -> None:
    result, hwm_target, action_log = _run_hwm_restore(tmp_path, b'{"version":1,"chains":{}}')

    assert result.returncode != 0
    assert hwm_target.read_bytes() == b"old-anchor"
    assert " psql " not in action_log.read_text(encoding="utf-8")


def test_interrupted_hwm_stream_preserves_old_anchor(tmp_path: Path) -> None:
    payload = _signed_hwm_payload(tmp_path)
    result, hwm_target, _ = _run_hwm_restore(tmp_path, payload, stream_fails=True)

    assert result.returncode != 0
    assert hwm_target.read_bytes() == b"old-anchor"


def test_invalid_hwm_signature_preserves_old_anchor(tmp_path: Path) -> None:
    payload = b'{"version":1,"chains":{},"sig":"signed"}'
    result, hwm_target, action_log = _run_hwm_restore(tmp_path, payload)

    assert result.returncode != 0
    assert "Signatur ungueltig" in result.stderr
    assert hwm_target.read_bytes() == b"old-anchor"
    assert " psql " not in action_log.read_text(encoding="utf-8")


def test_valid_hwm_stream_atomically_replaces_anchor(tmp_path: Path) -> None:
    payload = _signed_hwm_payload(tmp_path)
    result, hwm_target, _ = _run_hwm_restore(tmp_path, payload)

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert hwm_target.read_bytes() == payload
    assert "RESTORE-SATZ WIEDERHERGESTELLT: 2026-09-10_020000" in result.stdout
