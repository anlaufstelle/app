"""Funktionaler Nachweis der Pfadaufloesung von ``scripts/ops/backup.sh`` (Refs #1336).

Das echte Skript wird in eine Sandbox-Repo-Struktur kopiert
(``<tmp>/root/scripts/ops/backup.sh`` + Stub-``docker-compose.prod.yml`` im
Repo-Root), ein Stub-``docker`` in den PATH gelegt (protokolliert seine
Argumente und liefert Bytes fuer die Backup-Pipeline) und ausgefuehrt. Geprueft
wird, dass der Dump-Aufruf gegen ``<repo-root>/docker-compose.prod.yml`` laeuft
(NICHT ``<repo-root>/scripts/docker-compose.prod.yml`` wie beim frueheren
einfachen ``dirname``-Bug) und dass ``pg_dump`` als ``POSTGRES_ADMIN_USER``
(BYPASSRLS) startet.

Rein subprocess-basiert (kein Django/DB) — analog zu
``test_backup_offsite_state.py``.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Liest das echte ``scripts/ops/backup.sh`` aus dem Repo-Tree — mutmut kopiert
# ``scripts/`` NICHT nach ``mutants/`` (nur ``src/``), sonst schlägt der
# Baseline-Stats-Lauf mit FileNotFoundError fehl. Wie die
# ``test_architecture_guards_*``-Files daher als ``architecture`` markiert und
# in Mutmut-Runs deselektiert (#930/#1388); in ``make ci``/CI läuft der Test
# regulär (nur ``e2e`` wird dort ausgeschlossen).
pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
OPS_DIR = REPO_ROOT / "scripts" / "ops"

_FAKE_DOCKER = """#!/usr/bin/env bash
# Stub-docker fuer den Pfadaufloesungs-Test: Argumente protokollieren und fuer
# Backup-Pipelines Bytes liefern. Restore-Abfragen erhalten plausible Werte.
printf '%s\\n' "$*" >> "$DOCKER_LOG"
if [[ "$*" == *"relrowsecurity"* ]]; then
    printf '20\\n'
elif [[ "$*" == *"FROM pg_trigger"* ]]; then
    printf '1\\n'
elif [[ "$*" == *"UPDATE core_auditlog"* ]]; then
    printf 'auditlog is immutable\\n' >&2
    exit 1
elif [[ "$*" == *"SELECT COUNT(*)"* ]]; then
    printf '1\\n'
elif [[ "$*" == *" pg_dump "* || "$*" == *" tar -C "* ]]; then
    printf 'FAKE-CONTAINER-OUTPUT\\n'
elif [[ "$*" == *" psql "* ]]; then
    cat >/dev/null
fi
exit 0
"""

_ENV = (
    "POSTGRES_USER=appuser\n"
    "POSTGRES_DB=anlaufstelle\n"
    "POSTGRES_ADMIN_USER=testadmin\n"
    "POSTGRES_ADMIN_PASSWORD=adminpw\n"
    "POSTGRES_PASSWORD=apppw\n"
    "BACKUP_ENCRYPTION_KEY=not-a-placeholder-0123456789abcdef\n"
)
_BACKUP_KEY = _ENV.partition("BACKUP_ENCRYPTION_KEY=")[2].splitlines()[0]


def _make_sandbox(tmp_path: Path, *, backup_env: str = "") -> tuple[Path, Path, Path]:
    """Baue die Sandbox. Returns (root, docker_log, fake_bin)."""
    root = tmp_path / "root"
    ops = root / "scripts" / "ops"
    ops.mkdir(parents=True)
    for name in ("backup.sh", "restore-drill.sh", "_backup_common.sh"):
        shutil.copy(OPS_DIR / name, ops / name)
        (ops / name).chmod(0o755)
    # Gemeinsame Satzpruefung (Refs #2292): backup.sh sourct scripts/ops/lib/.
    shutil.copytree(OPS_DIR / "lib", ops / "lib")

    # Stub-Compose im Repo-Root — muss existieren, sonst greift der COMPOSE_FILE-Guard.
    (root / "docker-compose.prod.yml").write_text("# stub\n", encoding="utf-8")
    (root / ".env").write_text(_ENV + backup_env, encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(_FAKE_DOCKER, encoding="utf-8")
    fake_docker.chmod(0o755)

    # Die rote Reproduktion darf den aus .env.example stammenden Container-
    # Pfad /backups weder anlegen noch durchsuchen. Beide Kommandos bleiben
    # vollstaendig in tmp_path; korrekte Host-Pfade gehen an die Systemtools.
    fake_mkdir = fake_bin / "mkdir"
    fake_mkdir.write_text(
        """#!/usr/bin/env bash
for arg in "$@"; do
    if [[ "$arg" == "/backups" || "$arg" == /backups/* ]]; then
        exit 97
    fi
done
exec /bin/mkdir "$@"
""",
        encoding="utf-8",
    )
    fake_mkdir.chmod(0o755)
    fake_find = fake_bin / "find"
    fake_find.write_text(
        """#!/usr/bin/env bash
if [[ "${1:-}" == "/backups" || "${1:-}" == /backups/* ]]; then
    exit 0
fi
exec /usr/bin/find "$@"
""",
        encoding="utf-8",
    )
    fake_find.chmod(0o755)

    docker_log = tmp_path / "docker.log"
    docker_log.write_text("", encoding="utf-8")
    return root, docker_log, fake_bin


def _assert_backup_and_drill_use_daily_dir(root: Path, docker_log: Path, fake_bin: Path, daily_dir: Path) -> None:
    env = dict(os.environ)
    for variable in (
        "BACKUP_DIR",
        "BACKUP_HOST_DIR",
        "DJANGO_AUDIT_HWM_PATH",
    ):
        env.pop(variable, None)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["DOCKER_LOG"] = str(docker_log)

    backup_result = subprocess.run(
        ["bash", str(root / "scripts" / "ops" / "backup.sh")],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(root),
        timeout=60,
    )
    assert backup_result.returncode == 0, (
        f"backup.sh scheiterte (rc={backup_result.returncode}).\n"
        f"STDOUT:\n{backup_result.stdout}\nSTDERR:\n{backup_result.stderr}"
    )
    db_backups = sorted(daily_dir.glob("anlaufstelle_*.sql.gz.enc"))
    assert len(db_backups) == 1
    # Der Docker-Stub liefert fuer die Medien-Pipeline bewusst nur Fake-Bytes.
    # Die Fixture bildet deshalb einen authentifizierten DB-only-Satz ohne
    # Medien-Hash; der echte Medien-Drill wird separat geprueft.
    for media_artifact in daily_dir.glob("*_media.tar.gz.enc*"):
        media_artifact.unlink()
    manifest = next(daily_dir.glob("anlaufstelle_*.manifest"))
    manifest_lines = [
        line for line in manifest.read_text(encoding="utf-8").splitlines() if not line.startswith("media_sha256=")
    ]
    manifest.write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    derived = hashlib.sha256(f"anlaufstelle-backup-hmac-v1:{_BACKUP_KEY}".encode()).hexdigest()
    signature = hmac.new(derived.encode(), manifest.read_bytes(), hashlib.sha256).hexdigest()
    manifest.with_name(f"{manifest.name}.hmac").write_text(f"{signature}\n", encoding="utf-8")

    restore_result = subprocess.run(
        ["bash", str(root / "scripts" / "ops" / "restore-drill.sh"), "--prod"],
        capture_output=True,
        text=True,
        env={
            **env,
            "COMPOSE_DIR": str(root),
            "ENV_FILE": str(root / ".env"),
        },
        cwd=str(root),
        timeout=60,
    )

    assert restore_result.returncode == 0, (
        f"restore-drill.sh scheiterte (rc={restore_result.returncode}).\n"
        f"STDOUT:\n{restore_result.stdout}\nSTDERR:\n{restore_result.stderr}"
    )
    assert f"Drill gegen DB-Backup: {db_backups[0]}" in restore_result.stdout
    assert "INFO - Schritt 6: Kein passendes Medien-Archiv — uebersprungen" in restore_result.stdout


def test_env_example_paths_use_project_host_directory(tmp_path: Path) -> None:
    root, docker_log, fake_bin = _make_sandbox(
        tmp_path,
        backup_env="BACKUP_DIR=/backups\nBACKUP_HOST_DIR=./backups\n",
    )

    _assert_backup_and_drill_use_daily_dir(root, docker_log, fake_bin, root / "backups" / "daily")


def test_absolute_backup_host_directory_is_shared_by_backup_and_drill(tmp_path: Path) -> None:
    absolute_host_dir = tmp_path / "absolute-host-backups"
    root, docker_log, fake_bin = _make_sandbox(
        tmp_path,
        backup_env=f"BACKUP_DIR=/backups\nBACKUP_HOST_DIR={absolute_host_dir}\n",
    )

    _assert_backup_and_drill_use_daily_dir(root, docker_log, fake_bin, absolute_host_dir / "daily")


def test_backup_host_directory_defaults_to_project_backups(tmp_path: Path) -> None:
    root, docker_log, fake_bin = _make_sandbox(tmp_path)

    _assert_backup_and_drill_use_daily_dir(root, docker_log, fake_bin, root / "backups" / "daily")


def test_backup_resolves_compose_file_to_repo_root(tmp_path):
    root, docker_log, fake_bin = _make_sandbox(tmp_path)
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["DOCKER_LOG"] = str(docker_log)

    result = subprocess.run(
        ["bash", str(root / "scripts" / "ops" / "backup.sh")],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=60,
    )

    assert result.returncode == 0, (
        f"backup.sh scheiterte (rc={result.returncode}).\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    log = docker_log.read_text(encoding="utf-8")
    # Compose-Datei liegt im Repo-Root (…/root/), NICHT unter …/root/scripts/.
    assert "/root/docker-compose.prod.yml" in log, f"COMPOSE_FILE zeigt nicht auf den Repo-Root.\nDOCKER-LOG:\n{log}"
    assert "/scripts/docker-compose.prod.yml" not in log, (
        f"PROJECT_DIR zeigt noch auf <repo>/scripts — der #1336-Bug ist zurueck.\nDOCKER-LOG:\n{log}"
    )
    # Der DB-Dump laeuft als Admin-Rolle (BYPASSRLS), nicht als App-User —
    # sonst waere er unter FORCE-RLS still unvollstaendig (Refs #1336, DOC-P1).
    assert "pg_dump -U testadmin" in log, f"pg_dump laeuft nicht als POSTGRES_ADMIN_USER.\nDOCKER-LOG:\n{log}"
