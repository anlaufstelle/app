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
# jeden 'exec'-Aufruf ein paar Bytes ausgeben, damit die Backup-Pipeline
# (gzip | openssl enc) eine nicht-leere Datei erzeugt.
printf '%s\\n' "$*" >> "$DOCKER_LOG"
for a in "$@"; do
  if [[ "$a" == "exec" ]]; then
    printf 'FAKE-CONTAINER-OUTPUT\\n'
    exit 0
  fi
done
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


def _make_sandbox(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Baue die Sandbox. Returns (root, docker_log, fake_bin)."""
    root = tmp_path / "root"
    ops = root / "scripts" / "ops"
    ops.mkdir(parents=True)
    for name in ("backup.sh", "_backup_common.sh"):
        shutil.copy(OPS_DIR / name, ops / name)
    (ops / "backup.sh").chmod(0o755)

    # Stub-Compose im Repo-Root — muss existieren, sonst greift der COMPOSE_FILE-Guard.
    (root / "docker-compose.prod.yml").write_text("# stub\n", encoding="utf-8")
    (root / ".env").write_text(_ENV, encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(_FAKE_DOCKER, encoding="utf-8")
    fake_docker.chmod(0o755)

    docker_log = tmp_path / "docker.log"
    docker_log.write_text("", encoding="utf-8")
    return root, docker_log, fake_bin


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
