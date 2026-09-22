"""Audit-Anker ab Werk: Vorlage, Compose-Weg und Backup-Warnung (Refs #2315).

Befund aus dem Fresh-Host-Drill (Refs #2024): Eine streng nach README und
Admin-Guide durchgefuehrte Erstinstallation erzeugt einen Backup-Satz mit
**sechs statt sieben** Dateien — ohne ``audit_hwm-<Zeitstempel>.json``. Ursache
war die Kombination aus einer auskommentierten Vorlagenzeile und einem
``backup.sh``, das den fehlenden Anker nur als ``INFO`` meldete. Folge: Schritt
4b des Wiederherstellungs-Runbooks (Audit-HWM zurueckspielen und
``verify_audit_chain --require-anchor``) ist nach einem echten Totalverlust
nicht ausfuehrbar, und der Prod-Drill fuehrt Schritt 2b als uebersprungen.

Gedeckt wird deshalb:

- ``.env.example`` liefert den Ankerpfad **aktiv** aus (nicht auskommentiert).
- Der ausgelieferte Prod-Stack reicht die Variable an die Dienste durch, die
  ``verify_audit_chain`` fahren, und mountet das persistente Volume, in dem die
  Datei liegt — sonst waere der aktive Vorlagenwert wirkungslos.
- ``backup.sh`` warnt deutlich (WARN-Zeile + eigener Hinweis im Abschlussblock),
  wenn kein Anker-Artefakt exportiert wurde — in allen drei Ursachen (Variable
  nicht gesetzt, Datei fehlt/unlesbar, Datei leer).
- Mit konfiguriertem Anker entsteht der vollstaendige Sieben-Dateien-Satz und
  es gibt keine Warnung.
"""

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
    """Ohne Durchreichung und persistentes Volume bliebe der Vorlagenwert wirkungslos.

    ``verify_audit_chain`` laeuft im ``cron``-Sidecar und im
    ``maintenance``-Dienst. Beide beziehen die vollstaendige ``.env`` (und damit
    den Ankerpfad) und sehen dasselbe ``media``-Volume unter ``/data``, in dem
    die signierte Datei liegt. Der HTTP-Dienst ``web`` hat bewusst nur eine
    Allowlist; er fuehrt den Ankerpfad zwar darin (jede aktive Zeile der
    Vorlage muss dort stehen — Drift-Guard aus Refs #1962), hat aber keinen
    Leser dafuer. Diese Zeile ist reine Allowlist-Pflege, kein Funktionsweg.
    """
    services = yaml.safe_load(COMPOSE_PROD.read_text(encoding="utf-8"))["services"]

    for name in ("cron", "maintenance"):
        service = services[name]
        assert service.get("env_file") == ".env", f"{name} muss die vollstaendige .env beziehen"
        mounts = [str(volume).split(":")[:2] for volume in service.get("volumes", [])]
        assert ["media", "/data"] in mounts, f"{name} braucht das persistente /data-Volume fuer {ANCHOR_PATH}"


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


def _run_backup(root: Path, bindir: Path, **extra_env: str) -> subprocess.CompletedProcess[str]:
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
    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    return result


def _daily_files(root: Path) -> list[str]:
    return sorted(path.name for path in (root / "backups" / "daily").iterdir())


@pytest.mark.parametrize(
    ("anchor_path", "mode", "label"),
    [
        (None, "ok", "Variable nicht gesetzt"),
        (ANCHOR_PATH, "missing", "Ankerdatei fehlt oder ist unlesbar"),
        (ANCHOR_PATH, "empty", "Ankerdatei ist leer"),
    ],
)
def test_backup_without_anchor_artifact_warns_loudly(
    tmp_path: Path, anchor_path: str | None, mode: str, label: str
) -> None:
    """Ein Satz ohne Anker darf nicht als beilaeufige INFO-Zeile durchgehen."""
    root, bindir = _sandbox(tmp_path, anchor_path=anchor_path)

    result = _run_backup(root, bindir, TEST_HWM_MODE=mode)

    output = result.stdout + result.stderr
    assert not any(name.startswith(_ANCHOR_ARTEFACT) for name in _daily_files(root)), label
    assert "WARNUNG" in output, f"{label}: der fehlende Anker muss als Warnung erscheinen"
    assert _CLOSING_HINT in output, f"{label}: der Abschlussblock muss die Folge fuer die Wiederherstellung benennen"


def test_backup_with_configured_anchor_produces_the_full_set(tmp_path: Path) -> None:
    """Mit Ankerpfad entsteht der vollstaendige Sieben-Dateien-Satz, ohne Warnung."""
    root, bindir = _sandbox(tmp_path, anchor_path=ANCHOR_PATH)

    result = _run_backup(root, bindir)

    names = _daily_files(root)
    assert len(names) == 7, f"Erwartet wird der vollstaendige Satz, gefunden: {names}"
    assert sum(name.startswith(_ANCHOR_ARTEFACT) for name in names) == 1
    assert _CLOSING_HINT not in result.stdout + result.stderr
