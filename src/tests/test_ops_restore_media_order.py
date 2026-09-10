"""Subprozess-Nachweise fuer Medien-HMAC-Reihenfolge und Restore-Drill (#2195)."""

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
BACKUP_KEY = "restore-media-order-key-0123456789abcdef"
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


def _media_payload() -> bytes:
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as archive:
        for name, content in (("media/alpha.txt", b"vier"), ("media/nested/beta.txt", b"fuenf")):
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
if [[ "$*" == *" exec -T db psql "* ]]; then
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


def _run_restore(
    root: Path, env: dict[str, str], db_backup: Path, media_backup: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(root / "scripts" / "ops" / "restore.sh"), str(db_backup), str(media_backup)],
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


def test_valid_sidecars_are_both_checked_before_destructive_actions(tmp_path: Path) -> None:
    root, bindir, action_log, db_backup, media_backup = _make_sandbox(tmp_path)

    result = _run_restore(root, _base_env(root, bindir, action_log), db_backup, media_backup)

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    actions = action_log.read_text(encoding="utf-8").splitlines()
    hmac_positions = [index for index, line in enumerate(actions) if line.startswith("hmac ")]
    psql_pos = next(index for index, line in enumerate(actions) if " psql " in line)
    media_delete_pos = next(index for index, line in enumerate(actions) if "rm -rf" in line)
    assert len(hmac_positions) == 4
    assert hmac_positions[0] < hmac_positions[1] < hmac_positions[2] < psql_pos
    assert psql_pos < hmac_positions[3] < media_delete_pos


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

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "Medien: 2 Dateien, 9 Bytes, HMAC ok" in result.stdout
    assert list(media_tmp.iterdir()) == []


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
