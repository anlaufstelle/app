"""Audit anchor configuration and fail-loud backup export (Refs #2527)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
OPS_DIR = REPO_ROOT / "scripts" / "ops"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
COMPOSE_PROD = REPO_ROOT / "docker-compose.prod.yml"

BACKUP_KEY = "not-a-placeholder-0123456789abcdef"
ANCHOR_PATH = "/data/audit_hwm.json"
# Der Abschlussblock benennt die Folge fuer die Wiederherstellung; ein reiner
# WARN-Praefix irgendwo im Log genuegt dem Ticket ausdruecklich nicht.
_CLOSING_HINT = "Schritt 4b"
_ANCHOR_ARTEFACT = "audit_hwm-"


def _env_example_lines() -> list[str]:
    return ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()


def test_env_example_ships_the_anchor_path_active() -> None:
    """Die Vorlage setzt den Ankerpfad, statt ihn nur zu erwaehnen."""
    active = [
        line.strip()
        for line in _env_example_lines()
        if line.strip().startswith("DJANGO_AUDIT_HWM_PATH=")  # nicht auskommentiert
    ]

    assert active == [f"DJANGO_AUDIT_HWM_PATH={ANCHOR_PATH}"], (
        "`.env.example` muss den Audit-Ankerpfad aktiv ausliefern — eine auskommentierte "
        "Zeile erzeugt einen Backup-Satz ohne Anker-Artefakt."
    )


def test_prod_stack_carries_the_anchor_into_the_verifying_services() -> None:
    """Both maintenance services persist the anchor on its own writable volume."""
    services = yaml.safe_load(COMPOSE_PROD.read_text(encoding="utf-8"))["services"]
    for name in ("cron", "maintenance"):
        service = services[name]
        assert service.get("env_file") == ".env"
        assert "audit-anchor:/data:rw" in service["volumes"]


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _sandbox(tmp_path: Path, *, anchor_path: str | None) -> tuple[Path, Path]:
    """Backup-Sandbox mit Docker-Stub; ``anchor_path=None`` = Variable nicht gesetzt."""
    root = tmp_path / "backup-root"
    ops = root / "scripts" / "ops"
    ops.mkdir(parents=True)
    for name in ("backup.sh", "_backup_common.sh"):
        shutil.copy(OPS_DIR / name, ops / name)
    shutil.copytree(OPS_DIR / "lib", ops / "lib")
    (root / "docker-compose.prod.yml").write_text("services: {}\n", encoding="utf-8")
    env_lines = [
        "POSTGRES_USER=appuser",
        "POSTGRES_DB=anlaufstelle",
        "POSTGRES_ADMIN_USER=admin",
        "POSTGRES_ADMIN_PASSWORD=admin-password",
        f"BACKUP_ENCRYPTION_KEY={BACKUP_KEY}",
    ]
    if anchor_path is not None:
        env_lines.append(f"DJANGO_AUDIT_HWM_PATH={anchor_path}")
    (root / ".env").write_text("\n".join(env_lines) + "\n", encoding="utf-8")

    bindir = tmp_path / "backup-bin"
    bindir.mkdir()
    _write_executable(
        bindir / "docker",
        """#!/usr/bin/env bash
if [[ "$*" == *" exec -T cron cat "* ]]; then
    if [[ "${TEST_HWM_MODE:-ok}" == "missing" ]]; then
        exit 1
    fi
    if [[ "${TEST_HWM_MODE:-ok}" == "empty" ]]; then
        exit 0
    fi
    printf '%s\\n' "$TEST_HWM_JSON"
elif [[ "$*" == *"SELECT COUNT(*) FROM core_"* ]]; then
    printf '1\\n'
elif [[ "$*" == *" exec "* ]]; then
    printf 'FAKE-CONTAINER-OUTPUT\\n'
fi
exit 0
""",
    )
    return root, bindir


def _run_backup(
    root: Path, bindir: Path, *, expected_returncode: int = 0, **extra_env: str
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{bindir}:{env['PATH']}",
            "TEST_HWM_JSON": '{"version":1,"chains":{},"sig":"signed"}',
        }
    )
    env.update(extra_env)
    result = subprocess.run(
        ["bash", str(root / "scripts" / "ops" / "backup.sh")],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert result.returncode == expected_returncode, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    return result


def _daily_files(root: Path) -> list[str]:
    return sorted(path.name for path in (root / "backups" / "daily").iterdir())


@pytest.mark.parametrize("anchor_path", [None, "", ANCHOR_PATH])
@pytest.mark.parametrize("mode", ["missing", "empty"])
def test_backup_without_anchor_artifact_aborts(tmp_path: Path, anchor_path: str | None, mode: str) -> None:
    """Unset and empty values use the Compose default and cannot bypass the anchor."""
    root, bindir = _sandbox(tmp_path, anchor_path=anchor_path)
    result = _run_backup(root, bindir, expected_returncode=1, TEST_HWM_MODE=mode)
    assert "ERROR: Audit anchor" in result.stderr
    assert ANCHOR_PATH in result.stderr
    assert _daily_files(root) == []


@pytest.mark.parametrize("anchor_path", [None, ""])
def test_backup_default_anchor_produces_the_full_set(tmp_path: Path, anchor_path: str | None) -> None:
    root, bindir = _sandbox(tmp_path, anchor_path=anchor_path)
    result = _run_backup(root, bindir)
    assert len(_daily_files(root)) == 7
    assert "Audit anchor backed up" in result.stdout


def test_backup_with_configured_anchor_produces_the_full_set(tmp_path: Path) -> None:
    """Mit Ankerpfad entsteht der vollstaendige Sieben-Dateien-Satz, ohne Warnung."""
    root, bindir = _sandbox(tmp_path, anchor_path=ANCHOR_PATH)

    result = _run_backup(root, bindir)

    names = _daily_files(root)
    assert len(names) == 7, f"Erwartet wird der vollstaendige Satz, gefunden: {names}"
    assert sum(name.startswith(_ANCHOR_ARTEFACT) for name in names) == 1
    assert _CLOSING_HINT not in result.stdout + result.stderr
