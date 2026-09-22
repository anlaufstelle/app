"""``backup.sh --verify`` prüft den signierten Satz, nicht Einzelartefakte (#2292).

Zwei Sätze im selben Verzeichnis: der Verify-Lauf darf nie das jüngste
Medienarchiv eines fremden Satzes akzeptieren.
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
BACKUP_KEY = "verify-set-binding-key-0123456789abcdef"
OLD_SET = "2026-09-10_020000"
NEW_SET = "2026-09-11_020000"
REAL_OPENSSL = shutil.which("openssl")

pytestmark = [pytest.mark.architecture, pytest.mark.skipif(REAL_OPENSSL is None, reason="openssl nicht installiert")]

DOCKER_STUB = """#!/usr/bin/env bash
printf 'docker %s\\n' "$*" >> "$ACTION_LOG"
if [[ "$*" == *" exec -T db psql "* ]]; then
    if [[ "$*" == *"SELECT COUNT(*)"* ]]; then
        printf '1\\n'
    else
        cat >/dev/null
    fi
fi
exit 0
"""


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


def _media_payload(name: str) -> bytes:
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as archive:
        info = tarfile.TarInfo(f"media/1/{name}")
        content = name.encode()
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    return gzip.compress(payload.getvalue())


def _write_manifest(daily: Path, timestamp: str, *, media: Path | None, media_hash: str | None = None) -> Path:
    dump = daily / f"anlaufstelle_{timestamp}.sql.gz.enc"
    lines = [
        f"table_{table}=1"
        for table in (
            "core_facility",
            "core_client",
            "core_event",
            "core_workitem",
            "core_auditlog",
            "core_attachment",
        )
    ]
    lines.append(f"backup_timestamp={timestamp}")
    lines.append(f"db_sha256={hashlib.sha256(dump.read_bytes()).hexdigest()}")
    if media is not None or media_hash is not None:
        digest = media_hash or hashlib.sha256(media.read_bytes()).hexdigest()  # type: ignore[union-attr]
        lines.append(f"media_sha256={digest}")
    manifest = daily / f"anlaufstelle_{timestamp}.manifest"
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _sign(manifest)
    return manifest


def _sandbox(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "root"
    ops = root / "scripts" / "ops"
    ops.mkdir(parents=True)
    for name in ("backup.sh", "_backup_common.sh"):
        shutil.copy(OPS_DIR / name, ops / name)
        (ops / name).chmod(0o755)
    if (OPS_DIR / "lib").is_dir():
        shutil.copytree(OPS_DIR / "lib", ops / "lib")
    (root / "docker-compose.prod.yml").write_text("services: {}\n", encoding="utf-8")
    (root / ".env").write_text(
        "POSTGRES_USER=appuser\nPOSTGRES_DB=anlaufstelle\n" + f"BACKUP_ENCRYPTION_KEY={BACKUP_KEY}\n",
        encoding="utf-8",
    )

    daily = root / "backups" / "daily"
    daily.mkdir(parents=True)
    # Satz A (aelter, vollstaendig) und Satz B (neuer, eigener Dump).
    for timestamp, media_name in ((OLD_SET, "alt.bin"), (NEW_SET, "neu.bin")):
        _encrypt(gzip.compress(f"-- {timestamp}\nSELECT 1;\n".encode()), daily / f"anlaufstelle_{timestamp}.sql.gz.enc")
        _encrypt(_media_payload(media_name), daily / f"anlaufstelle_{timestamp}_media.tar.gz.enc")
    _write_manifest(daily, OLD_SET, media=daily / f"anlaufstelle_{OLD_SET}_media.tar.gz.enc")
    _write_manifest(daily, NEW_SET, media=daily / f"anlaufstelle_{NEW_SET}_media.tar.gz.enc")

    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "docker"
    stub.write_text(DOCKER_STUB, encoding="utf-8")
    stub.chmod(0o755)
    return root, bindir, daily


def _verify(tmp_path: Path, root: Path, bindir: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.update({"PATH": f"{bindir}:{env['PATH']}", "ACTION_LOG": str(tmp_path / "actions.log")})
    env.pop("DJANGO_AUDIT_HWM_PATH", None)
    return subprocess.run(
        ["bash", str(root / "scripts" / "ops" / "backup.sh"), "--verify"],
        capture_output=True,
        text=True,
        env=env,
        cwd=root,
        timeout=120,
        stdin=subprocess.DEVNULL,
    )


def test_complete_set_passes_and_uses_its_own_media_archive(tmp_path: Path) -> None:
    root, bindir, daily = _sandbox(tmp_path)

    result = _verify(tmp_path, root, bindir)

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert f"anlaufstelle_{NEW_SET}_media.tar.gz.enc" in result.stdout
    assert f"anlaufstelle_{OLD_SET}_media.tar.gz.enc" not in result.stdout


def test_media_of_an_older_set_is_rejected(tmp_path: Path) -> None:
    """Medienbackup abgebrochen: neuer Dump, Medien vom Vortag."""
    root, bindir, daily = _sandbox(tmp_path)
    old_media = daily / f"anlaufstelle_{OLD_SET}_media.tar.gz.enc"
    new_media = daily / f"anlaufstelle_{NEW_SET}_media.tar.gz.enc"
    new_media.unlink()
    new_media.with_name(f"{new_media.name}.hmac").unlink()
    _write_manifest(daily, NEW_SET, media=old_media)

    result = _verify(tmp_path, root, bindir)

    assert result.returncode != 0
    assert f"anlaufstelle_{OLD_SET}_media.tar.gz.enc" not in result.stdout


def test_wrong_media_hash_in_manifest_is_rejected(tmp_path: Path) -> None:
    root, bindir, daily = _sandbox(tmp_path)
    _write_manifest(daily, NEW_SET, media=None, media_hash="0" * 64)

    result = _verify(tmp_path, root, bindir)

    assert result.returncode != 0
    assert "Medien-Hash" in result.stdout + result.stderr


def test_missing_manifest_is_rejected(tmp_path: Path) -> None:
    root, bindir, daily = _sandbox(tmp_path)
    for timestamp in (OLD_SET, NEW_SET):
        (daily / f"anlaufstelle_{timestamp}.manifest").unlink()
        (daily / f"anlaufstelle_{timestamp}.manifest.hmac").unlink()

    result = _verify(tmp_path, root, bindir)

    assert result.returncode != 0
    assert "Manifest" in result.stdout + result.stderr


def test_tampered_manifest_signature_is_rejected(tmp_path: Path) -> None:
    root, bindir, daily = _sandbox(tmp_path)
    (daily / f"anlaufstelle_{NEW_SET}.manifest.hmac").write_text("0" * 64 + "\n", encoding="utf-8")

    result = _verify(tmp_path, root, bindir)

    assert result.returncode != 0


def test_database_only_set_without_media_binding_passes(tmp_path: Path) -> None:
    root, bindir, daily = _sandbox(tmp_path)
    media = daily / f"anlaufstelle_{NEW_SET}_media.tar.gz.enc"
    media.unlink()
    media.with_name(f"{media.name}.hmac").unlink()
    _write_manifest(daily, NEW_SET, media=None)

    result = _verify(tmp_path, root, bindir)

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert f"anlaufstelle_{OLD_SET}_media.tar.gz.enc" not in result.stdout
