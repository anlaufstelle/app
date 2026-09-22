"""Nur vollstaendige Backup-Saetze gelten als letztes erfolgreiches Backup (#2291).

Die Faelle fahren das ausgelieferte ``scripts/ops/backup.sh`` mit Docker-Stubs
und werten das entstandene Verzeichnis anschliessend mit den echten
Python-Funktionen (`last_backup_info`, Compliance-Check) aus.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from django.test import override_settings

from core.services.compliance._types import ComplianceStatus
from core.services.system import health as system_health

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
OPS_DIR = REPO_ROOT / "scripts" / "ops"
BACKUP_KEY = "backup-marker-key-0123456789abcdef"

DOCKER_STUB = """#!/usr/bin/env bash
printf 'docker %s\\n' "$*" >> "$ACTION_LOG"
mode="${TEST_FAIL_MODE:-none}"
if [[ "$*" == *"SELECT COUNT(*) FROM core_"* ]]; then
    if [[ "$mode" == "counts" ]]; then
        printf 'FATAL: konnte nicht mit Datenbank verbinden\\n' >&2
        exit 1
    fi
    printf '3\\n'
elif [[ "$*" == *" pg_dump "* ]]; then
    if [[ "$mode" == "dump" ]]; then
        printf 'pg_dump: Fehler: Verbindung verloren\\n' >&2
        exit 1
    fi
    printf -- '-- dump\\nSELECT 1;\\n'
elif [[ "$*" == *" tar -C "* ]]; then
    if [[ "$mode" == "media" ]]; then
        printf 'tar: /data/media: Kein Zugriff\\n' >&2
        exit 1
    fi
    tar -C "$MEDIA_SOURCE" -cf - media
elif [[ "$*" == *" exec -T cron cat "* ]]; then
    printf '%s\\n' "${TEST_HWM_JSON:-}"
fi
exit 0
"""


def _sandbox(tmp_path: Path) -> tuple[Path, Path]:
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
        "POSTGRES_USER=appuser\n"
        "POSTGRES_ADMIN_USER=adminuser\n"
        "POSTGRES_ADMIN_PASSWORD=secret\n"
        "POSTGRES_DB=anlaufstelle\n"
        f"BACKUP_ENCRYPTION_KEY={BACKUP_KEY}\n",
        encoding="utf-8",
    )

    media_source = tmp_path / "media-source"
    (media_source / "media" / "1").mkdir(parents=True)
    (media_source / "media" / "1" / "alpha.bin").write_bytes(b"anhang")

    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "docker"
    stub.write_text(DOCKER_STUB, encoding="utf-8")
    stub.chmod(0o755)
    return root, bindir


def _run_backup(tmp_path: Path, root: Path, bindir: Path, **overrides) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{bindir}:{env['PATH']}",
            "ACTION_LOG": str(tmp_path / "actions.log"),
            "MEDIA_SOURCE": str(tmp_path / "media-source"),
        }
    )
    env.pop("DJANGO_AUDIT_HWM_PATH", None)
    env.update(overrides)
    return subprocess.run(
        ["bash", str(root / "scripts" / "ops" / "backup.sh")],
        capture_output=True,
        text=True,
        env=env,
        cwd=root,
        timeout=120,
        stdin=subprocess.DEVNULL,
    )


def _backup_dir(root: Path) -> Path:
    return root / "backups"


@pytest.mark.parametrize("failure", ["counts", "dump", "media"])
def test_failed_backup_never_becomes_the_last_successful_backup(tmp_path: Path, failure: str) -> None:
    root, bindir = _sandbox(tmp_path)

    result = _run_backup(tmp_path, root, bindir, TEST_FAIL_MODE=failure)

    assert result.returncode != 0, f"STDOUT:\n{result.stdout}"
    assert system_health.last_backup_info(_backup_dir(root)) is None
    with override_settings(BACKUP_DIR=str(_backup_dir(root))):
        from core.services.compliance.backup import _backup_checks

        check = _backup_checks()[0]
    assert check.status is ComplianceStatus.UNKNOWN


def test_successful_backup_publishes_a_complete_set(tmp_path: Path) -> None:
    root, bindir = _sandbox(tmp_path)

    result = _run_backup(tmp_path, root, bindir)

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    info = system_health.last_backup_info(_backup_dir(root))
    assert info is not None
    assert info["path"].endswith(".manifest")
    assert info["is_stale"] is False
    with override_settings(BACKUP_DIR=str(_backup_dir(root))):
        from core.services.compliance.backup import _backup_checks

        check = _backup_checks()[0]
    assert check.status is ComplianceStatus.OK


def test_temporary_artifacts_of_a_running_backup_are_ignored(tmp_path: Path) -> None:
    """Ein abgebrochener Lauf hinterlaesst nur Arbeitsdateien — kein Satz."""
    root, bindir = _sandbox(tmp_path)
    _run_backup(tmp_path, root, bindir, TEST_FAIL_MODE="dump")
    daily = _backup_dir(root) / "daily"

    published = sorted(p.name for p in daily.iterdir() if p.is_file())

    assert published == [], f"Unerwartet veroeffentlichte Artefakte: {published}"
    assert system_health.last_backup_info(_backup_dir(root)) is None


def test_arbitrary_fresh_file_does_not_mark_a_backup(tmp_path: Path) -> None:
    daily = tmp_path / "backups" / "daily"
    daily.mkdir(parents=True)
    (daily / "notizen.txt").write_text("frisch", encoding="utf-8")
    (daily / "anlaufstelle_2026-09-12_020000.manifest").write_text("", encoding="utf-8")

    assert system_health.last_backup_info(tmp_path / "backups") is None


def test_manifest_without_its_artifacts_is_not_a_complete_set(tmp_path: Path) -> None:
    daily = tmp_path / "backups" / "daily"
    daily.mkdir(parents=True)
    manifest = daily / "anlaufstelle_2026-09-12_020000.manifest"
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
        + "backup_timestamp=2026-09-12_020000\n"
        + f"db_sha256={'a' * 64}\n"
        + f"media_sha256={'b' * 64}\n",
        encoding="utf-8",
    )
    manifest.with_suffix(".manifest.hmac").write_text("x" * 64, encoding="utf-8")

    assert system_health.last_backup_info(tmp_path / "backups") is None

    # Dump und Sidecar ergaenzen — solange das gebundene Medienarchiv fehlt,
    # bleibt der Satz unvollstaendig.
    dump = daily / "anlaufstelle_2026-09-12_020000.sql.gz.enc"
    dump.write_bytes(b"x")
    dump.with_name(f"{dump.name}.hmac").write_text("y" * 64, encoding="utf-8")
    assert system_health.last_backup_info(tmp_path / "backups") is None

    media = daily / "anlaufstelle_2026-09-12_020000_media.tar.gz.enc"
    media.write_bytes(b"z")
    media.with_name(f"{media.name}.hmac").write_text("w" * 64, encoding="utf-8")
    info = system_health.last_backup_info(tmp_path / "backups")
    assert info is not None
    assert Path(info["path"]).name == manifest.name
