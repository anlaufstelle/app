"""Der Drill muss denselben Satzbegriff pruefen wie Verify/Restore (Refs #2292).

``backup.sh --verify`` und ``restore.sh`` gehen seit der gemeinsamen
Satzbibliothek ``scripts/ops/lib/backup-set.sh`` durch dieselbe Pruefung.
``restore-drill.sh`` hatte in Schritt 0 noch eine eigene, mitgewachsene Kopie
dieser Logik — und die war an mehreren Stellen schwaecher. Ein Satz, den der
Prueflauf ablehnt, wurde vom Drill als „Zeilenzahl-Manifest authentifiziert"
durchgewunken; der Drill haette also einen Restore-Weg als geprobt
protokolliert, den der echte Restore verweigert.

Der Guard formuliert genau diese Invariante: **Was Verify ablehnt, akzeptiert
auch der Drill nicht.** Geprueft wird sie fuer jeden bekannten Abweichungsfall
gegen dieselbe Bibliotheksfunktion, die der Prueflauf benutzt.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
OPS_DIR = REPO_ROOT / "scripts" / "ops"

BACKUP_KEY = "not-a-placeholder-0123456789abcdef"
TIMESTAMP = "2026-09-02_020000"
TABLES = ("core_facility", "core_client", "core_event", "core_workitem", "core_auditlog", "core_attachment")


def _sign(payload: bytes) -> str:
    derived = hashlib.sha256(f"anlaufstelle-backup-hmac-v1:{BACKUP_KEY}".encode()).hexdigest()
    return hmac.new(derived.encode(), payload, hashlib.sha256).hexdigest()


def _write_signed(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    path.with_name(f"{path.name}.hmac").write_text(f"{_sign(payload)}\n", encoding="utf-8")


def _build_set(
    daily: Path,
    *,
    bind_media: bool,
    write_media: bool,
    bind_hwm: bool,
    write_hwm: bool,
    empty_key_line: bool = False,
) -> Path:
    """Legt einen Prod-Backup-Satz mit waehlbaren Abweichungen an."""
    daily.mkdir(parents=True, exist_ok=True)
    db_file = daily / f"anlaufstelle_{TIMESTAMP}.sql.gz.enc"
    _write_signed(db_file, b"encrypted-dump")

    media_file = daily / f"anlaufstelle_{TIMESTAMP}_media.tar.gz.enc"
    media_payload = b"encrypted-media"
    if write_media:
        _write_signed(media_file, media_payload)

    hwm_file = daily / f"audit_hwm-{TIMESTAMP}.json"
    hwm_payload = b'{"version":1,"chains":{},"sig":"signed"}'
    if write_hwm:
        hwm_file.write_bytes(hwm_payload)

    lines = [f"table_{table}=1" for table in TABLES]
    lines.append(f"backup_timestamp={TIMESTAMP}")
    lines.append(f"db_sha256={hashlib.sha256(db_file.read_bytes()).hexdigest()}")
    if bind_media:
        lines.append(f"media_sha256={hashlib.sha256(media_payload).hexdigest()}")
    if bind_hwm:
        lines.append(f"hwm_sha256={hashlib.sha256(hwm_payload).hexdigest()}")
    if empty_key_line:
        lines.append("=1")
    _write_signed(daily / f"anlaufstelle_{TIMESTAMP}.manifest", ("\n".join(lines) + "\n").encode("utf-8"))
    return db_file


def _verify_rejects(ops: Path, db_file: Path) -> subprocess.CompletedProcess[str]:
    """Faehrt exakt die Satzpruefung, die ``backup.sh --verify`` benutzt."""
    media = db_file.with_name(db_file.name.replace(".sql.gz.enc", "_media.tar.gz.enc"))
    hwm = db_file.parent / f"audit_hwm-{TIMESTAMP}.json"
    script = f"""
set -uo pipefail
BACKUP_ENCRYPTION_KEY={BACKUP_KEY}
source "{ops}/_backup_common.sh"
source "{ops}/lib/backup-set.sh"
backup_set_load "{db_file.with_suffix("").with_suffix("").with_suffix("")}.manifest" || exit 1
media_arg=""
[[ -f "{media}" ]] && media_arg="{media}"
backup_set_verify_artifacts "{db_file}" "$media_arg" "{hwm}" || exit 1
"""
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)


def _run_drill(tmp_path: Path, compose_dir: Path) -> subprocess.CompletedProcess[str]:
    bindir = tmp_path / "drill-bin"
    bindir.mkdir(exist_ok=True)
    docker = bindir / "docker"
    # Schritt 0 darf den Stack gar nicht erst erreichen; ein Aufruf danach
    # scheitert absichtlich und macht ein Durchrutschen sichtbar.
    docker.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    docker.chmod(0o755)
    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{bindir}:{env['PATH']}",
            "COMPOSE_DIR": str(compose_dir),
            "ENV_FILE": str(compose_dir / ".env"),
        }
    )
    env.pop("DJANGO_AUDIT_HWM_PATH", None)
    return subprocess.run(
        ["bash", str(compose_dir / "scripts" / "ops" / "restore-drill.sh"), "--prod"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def _sandbox(tmp_path: Path, **set_options: bool) -> tuple[Path, Path, Path]:
    compose_dir = tmp_path / "drill-root"
    ops = compose_dir / "scripts" / "ops"
    ops.mkdir(parents=True)
    for name in ("restore-drill.sh", "_backup_common.sh"):
        shutil.copy(OPS_DIR / name, ops / name)
    shutil.copytree(OPS_DIR / "lib", ops / "lib")
    (compose_dir / "docker-compose.prod.yml").write_text("services: {}\n", encoding="utf-8")
    (compose_dir / ".env").write_text(
        f"BACKUP_ENCRYPTION_KEY={BACKUP_KEY}\nPOSTGRES_DB=anlaufstelle\nBACKUP_HOST_DIR={compose_dir / 'backups'}\n",
        encoding="utf-8",
    )
    db_file = _build_set(compose_dir / "backups" / "daily", **set_options)
    return compose_dir, ops, db_file


@pytest.mark.parametrize(
    ("label", "options"),
    [
        (
            "Manifest bindet den Audit-Anker, die Datei fehlt",
            {"bind_media": False, "write_media": False, "bind_hwm": True, "write_hwm": False},
        ),
        (
            "Manifest bindet kein Medien-Archiv, es liegt trotzdem eines daneben",
            {"bind_media": False, "write_media": True, "bind_hwm": False, "write_hwm": False},
        ),
        (
            "Manifest bindet keinen Audit-Anker, es liegt trotzdem einer daneben",
            {"bind_media": False, "write_media": False, "bind_hwm": False, "write_hwm": True},
        ),
        (
            "Manifest enthaelt eine Zeile ohne Schluessel",
            {
                "bind_media": False,
                "write_media": False,
                "bind_hwm": False,
                "write_hwm": False,
                "empty_key_line": True,
            },
        ),
    ],
)
def test_drill_rejects_every_set_the_verify_path_rejects(tmp_path: Path, label: str, options: dict) -> None:
    compose_dir, ops, db_file = _sandbox(tmp_path, **options)

    verify = _verify_rejects(ops, db_file)
    drill = _run_drill(tmp_path, compose_dir)

    assert verify.returncode != 0, f"Vorbedingung: der Prueflauf lehnt diesen Satz ab ({label})"
    assert drill.returncode != 0, f"Der Drill darf keinen Satz annehmen, den der Prueflauf ablehnt ({label})"
    assert "Schritt 0" in drill.stdout, f"Der Abbruch gehoert in Schritt 0 ({label})"
    assert "authentifiziert" not in drill.stdout, f"Kein OK fuer einen abgelehnten Satz ({label})"


def test_drill_accepts_the_set_the_verify_path_accepts(tmp_path: Path) -> None:
    """Gegenprobe: ein zusammengehoeriger Satz kommt durch Schritt 0."""
    compose_dir, ops, db_file = _sandbox(tmp_path, bind_media=True, write_media=True, bind_hwm=True, write_hwm=True)

    verify = _verify_rejects(ops, db_file)
    drill = _run_drill(tmp_path, compose_dir)

    assert verify.returncode == 0, f"Vorbedingung verletzt:\n{verify.stdout}\n{verify.stderr}"
    assert "Schritt 0: Zeilenzahl-Manifest authentifiziert" in drill.stdout, drill.stdout


def test_drill_step_zero_uses_the_shared_set_library(tmp_path: Path) -> None:
    """Strukturpin: keine zweite, mitwachsende Kopie der Satzlogik im Drill."""
    source = (OPS_DIR / "restore-drill.sh").read_text(encoding="utf-8")

    assert "lib/backup-set.sh" in source, "restore-drill.sh muss die gemeinsame Satzbibliothek sourcen"
    assert "backup_set_load" in source and "backup_set_verify_artifacts" in source
    assert "hwm_sha256)" not in source, "Der Drill parst das Manifest nicht mehr selbst"
