"""Nicht-interaktiver Restore-Block und Bestaetigung vor der Zerstoerung (#2318).

Der in ``docs/disaster-recovery.md`` § 6 Schritt 4 gedruckte Befehlsblock wird
programmatisch aus der committeten Datei extrahiert und genau so gefahren
(ausgelieferter Wortlaut statt Arbeitsfassung). Ersetzt werden ausschliesslich die drei hostabhaengigen
Platzhalter (`/opt/anlaufstelle`, die Host-Backup-Ablage und der Log-Pfad);
jede andere Zeile laeuft im Wortlaut der Auslieferung.
"""

from __future__ import annotations

import gzip
import hashlib
import hmac
import io
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
OPS_DIR = REPO_ROOT / "scripts" / "ops"
DR_DOC = REPO_ROOT / "docs" / "disaster-recovery.md"
BACKUP_KEY = "restore-confirm-key-0123456789abcdef"
RESTORE_TIMESTAMP = "2026-09-03_020000"
REAL_OPENSSL = shutil.which("openssl")

# Kommandos, die den vorhandenen Zielbestand veraendern. Keines davon darf ohne
# Bestaetigung im Aktionsprotokoll auftauchen.
DESTRUCTIVE_MARKERS = (
    "dropdb",
    "createdb",
    "DROP DATABASE",
    "CREATE DATABASE",
    "RENAME TO",
    "ON_ERROR_STOP=1 --single-transaction",
    " mv ",
    "rm -rf",
)
# Dienste anzuhalten ist im dokumentierten Block eine bewusste Vorarbeit des
# Betreibers (der Block stoppt web/cron selbst); ein direkter Skriptaufruf ohne
# Bestaetigung darf dagegen auch das nicht tun.
SERVICE_MARKERS = ("stop web cron",)


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


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


def _sign(path: Path) -> None:
    derived = hashlib.sha256(f"anlaufstelle-backup-hmac-v1:{BACKUP_KEY}".encode()).hexdigest()
    signature = hmac.new(derived.encode(), path.read_bytes(), hashlib.sha256).hexdigest()
    path.with_name(f"{path.name}.hmac").write_text(f"{signature}\n", encoding="utf-8")


def _media_payload() -> bytes:
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as archive:
        info = tarfile.TarInfo("media/1/alpha.bin")
        content = b"anhang"
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    return gzip.compress(payload.getvalue())


def documented_restore_block() -> str:
    """Den kopierbaren Befehlsblock aus § 6 Schritt 4 der DR-Doku holen."""
    text = DR_DOC.read_text(encoding="utf-8")
    section = text.split("### Schritt 4 — DB-Backup", maxsplit=1)[1].split("### Schritt 4b", maxsplit=1)[0]
    blocks = re.findall(r"```bash\n(.*?)```", section, flags=re.DOTALL)
    matching = [block for block in blocks if "scripts/ops/restore.sh" in block]
    assert len(matching) == 1, f"Erwartet genau einen Restore-Block, gefunden: {len(matching)}"
    return matching[0]


def _sandbox(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "opt-anlaufstelle"
    ops = root / "scripts" / "ops"
    ops.mkdir(parents=True)
    for name in ("restore.sh", "_backup_common.sh"):
        shutil.copy(OPS_DIR / name, ops / name)
        (ops / name).chmod(0o755)
    lib_src = OPS_DIR / "lib"
    if lib_src.is_dir():
        shutil.copytree(lib_src, ops / "lib")
    (root / "docker-compose.prod.yml").write_text("services: {}\n", encoding="utf-8")
    (root / ".env").write_text(
        "POSTGRES_USER=appuser\n"
        "POSTGRES_ADMIN_USER=adminuser\n"
        "POSTGRES_DB=anlaufstelle\n"
        f"BACKUP_ENCRYPTION_KEY={BACKUP_KEY}\n",
        encoding="utf-8",
    )

    daily = root / "backups" / "daily"
    daily.mkdir(parents=True)
    db_backup = daily / f"anlaufstelle_{RESTORE_TIMESTAMP}.sql.gz.enc"
    media_backup = daily / f"anlaufstelle_{RESTORE_TIMESTAMP}_media.tar.gz.enc"
    _encrypt(gzip.compress(b"SELECT 1;\n"), db_backup)
    _encrypt(_media_payload(), media_backup)
    manifest = daily / f"anlaufstelle_{RESTORE_TIMESTAMP}.manifest"
    manifest.write_text(
        "table_core_facility=1\n"
        "table_core_client=1\n"
        "table_core_event=1\n"
        "table_core_workitem=1\n"
        "table_core_auditlog=1\n"
        "table_core_attachment=1\n"
        f"backup_timestamp={RESTORE_TIMESTAMP}\n"
        f"db_sha256={hashlib.sha256(db_backup.read_bytes()).hexdigest()}\n"
        f"media_sha256={hashlib.sha256(media_backup.read_bytes()).hexdigest()}\n",
        encoding="utf-8",
    )
    _sign(manifest)

    bindir = tmp_path / "bin"
    bindir.mkdir()
    action_log = tmp_path / "actions.log"
    action_log.write_text("", encoding="utf-8")
    _write_executable(
        bindir / "docker",
        """#!/usr/bin/env bash
printf 'docker %s\\n' "$*" >> "$ACTION_LOG"
if [[ "$*" == *" exec -T web sh -c "* ]]; then
    MEDIA_ROOT="${TEST_WEB_MEDIA_ROOT-/data/media}" sh -c "${@: -1}"
elif [[ "$*" == *" exec -T db psql "* || "$*" == *" exec -T db sh -c "* ]]; then
    if [[ "$*" == *"SELECT facility_id, storage_filename"* ]]; then
        printf '1|alpha.bin\\n'
    elif [[ "$*" == *"SELECT COUNT(*)"* ]]; then
        printf '1\\n'
    else
        cat >/dev/null
    fi
elif [[ "$*" == *" exec -T web "* ]]; then
    cat >/dev/null
fi
exit 0
""",
    )
    # The documented block runs restore.sh with sudo (backup files are 0600 and
    # owned by root); the stub records the call and keeps the stubbed PATH.
    _write_executable(
        bindir / "sudo",
        """#!/usr/bin/env bash
printf 'sudo %s\\n' "$*" >> "$ACTION_LOG"
exec "$@"
""",
    )
    return root, bindir, action_log


def _env(root: Path, bindir: Path, action_log: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("DJANGO_AUDIT_HWM_PATH", None)
    env.pop("RESTORE_CONFIRM", None)
    env.update({"PATH": f"{bindir}:{env['PATH']}", "ACTION_LOG": str(action_log)})
    return env


def _run_block(root: Path, bindir: Path, action_log: Path, block: str, tmp_path: Path, **kwargs):
    host_dir = 'BACKUP_HOST_DIR="/opt/anlaufstelle/drill-backups/$RESTORE_TIMESTAMP"'
    log_path = "/tmp/anlaufstelle-restore.log"  # noqa: S108 — Wortlaut der Doku, wird ersetzt
    script = (
        block.replace("cd /opt/anlaufstelle", f"cd {root}")
        .replace(host_dir, f'BACKUP_HOST_DIR="{root}/backups"')
        .replace(log_path, str(tmp_path / "restore.log"))
    )
    assert "/opt/anlaufstelle" not in script
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=_env(root, bindir, action_log),
        cwd=root,
        timeout=90,
        **kwargs,
    )


def _destructive_entries(action_log: Path, markers: tuple[str, ...] = DESTRUCTIVE_MARKERS) -> list[str]:
    return [
        line
        for line in action_log.read_text(encoding="utf-8").splitlines()
        if any(marker in line for marker in markers)
    ]


@pytest.mark.skipif(REAL_OPENSSL is None, reason="openssl nicht installiert")
def test_documented_block_runs_non_interactively(tmp_path: Path) -> None:
    """Der gedruckte Block laeuft ohne Tastatureingabe vollstaendig durch."""
    root, bindir, action_log = _sandbox(tmp_path)

    result = _run_block(root, bindir, action_log, documented_restore_block(), tmp_path, stdin=subprocess.DEVNULL)

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "RESTORE-SATZ WIEDERHERGESTELLT" in result.stdout
    assert _destructive_entries(action_log), "Ohne zerstoerenden Schritt ist kein Restore gelaufen"
    assert "sudo ./scripts/ops/restore.sh --yes " in action_log.read_text(encoding="utf-8"), (
        "The documented block must run restore.sh as the owner of the 0600 backup files"
    )


@pytest.mark.skipif(REAL_OPENSSL is None, reason="openssl nicht installiert")
def test_documented_block_without_switch_stops_before_every_destructive_step(tmp_path: Path) -> None:
    """Ohne Bestaetigungsschalter bricht der Block ab, bevor irgendetwas faellt."""
    root, bindir, action_log = _sandbox(tmp_path)
    block = documented_restore_block()
    stripped = block.replace(" --yes", "").replace("RESTORE_CONFIRM=ja ", "")
    assert stripped != block, "Der dokumentierte Block nennt keinen Bestaetigungsschalter"

    result = _run_block(root, bindir, action_log, stripped, tmp_path, stdin=subprocess.DEVNULL)

    assert result.returncode != 0
    # Der Block stoppt web/cron selbst — gemeint sind die Datenschritte.
    assert _destructive_entries(action_log) == []


def test_documented_block_prepares_target_inside_the_script() -> None:
    """Die Zielvorbereitung steht im Skript (nach der Bestaetigung), nicht im Block."""
    block = documented_restore_block()

    assert "dropdb" not in block
    assert "createdb" not in block
    assert "scripts/ops/restore.sh" in block


def test_restore_script_confirms_before_any_destructive_command() -> None:
    restore = (OPS_DIR / "restore.sh").read_text(encoding="utf-8")
    confirm_index = restore.index("RESTORE_CONFIRMED=1")

    for marker in ("--single-transaction", "RENAME TO", "stop web cron"):
        assert restore.index(marker) > confirm_index, f"{marker} steht vor der Bestaetigung"


@pytest.mark.skipif(REAL_OPENSSL is None, reason="openssl nicht installiert")
@pytest.mark.parametrize("confirmation", ["switch", "env", "stdin"])
def test_accepted_confirmations_reach_the_restore(tmp_path: Path, confirmation: str) -> None:
    """Schalter, Umgebungsvariable und durchgereichte Antwort bestaetigen gleich."""
    root, bindir, action_log = _sandbox(tmp_path)
    env = _env(root, bindir, action_log)
    args = ["bash", str(root / "scripts" / "ops" / "restore.sh")]
    stdin_text = ""
    if confirmation == "switch":
        args.append("--yes")
    elif confirmation == "env":
        env["RESTORE_CONFIRM"] = "ja"
    else:
        stdin_text = "ja\n"
    args += [
        str(root / "backups" / "daily" / f"anlaufstelle_{RESTORE_TIMESTAMP}.sql.gz.enc"),
        str(root / "backups" / "daily" / f"anlaufstelle_{RESTORE_TIMESTAMP}_media.tar.gz.enc"),
    ]

    result = subprocess.run(args, input=stdin_text, capture_output=True, text=True, env=env, cwd=root, timeout=90)

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "RESTORE-SATZ WIEDERHERGESTELLT" in result.stdout


@pytest.mark.skipif(REAL_OPENSSL is None, reason="openssl nicht installiert")
@pytest.mark.parametrize("answer", ["", "nein"])
def test_missing_or_negative_answer_fails_closed(tmp_path: Path, answer: str) -> None:
    """Ohne 'ja' endet der Lauf mit Exit != 0 und ohne jede Zerstoerung."""
    root, bindir, action_log = _sandbox(tmp_path)

    result = subprocess.run(
        [
            "bash",
            str(root / "scripts" / "ops" / "restore.sh"),
            str(root / "backups" / "daily" / f"anlaufstelle_{RESTORE_TIMESTAMP}.sql.gz.enc"),
        ],
        input=answer,
        capture_output=True,
        text=True,
        env=_env(root, bindir, action_log),
        cwd=root,
        timeout=90,
    )

    assert result.returncode != 0
    assert _destructive_entries(action_log, DESTRUCTIVE_MARKERS + SERVICE_MARKERS) == []
