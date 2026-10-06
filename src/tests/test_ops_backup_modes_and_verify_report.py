"""``backup.sh`` writes owner-only files and ``--verify`` reports what it did and did not check.

Backups hold Art. 9 data, so every file and directory a run creates must be
unreadable for group and others, whatever umask the caller (cron, sudo) has.
``--verify`` is a sample, not a row-by-row comparison; its output must say so.
"""

from __future__ import annotations

import gzip
import hashlib
import hmac
import io
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
OPS_DIR = REPO_ROOT / "scripts" / "ops"
BACKUP_KEY = "backup-modes-key-0123456789abcdef"
TIMESTAMP = "2026-09-12_020000"
REAL_OPENSSL = shutil.which("openssl")
RLS_TABLES = ("core_client", "core_event", "core_workitem", "core_auditlog")
MANIFEST_TABLES = ("core_facility", *RLS_TABLES, "core_attachment")

pytestmark = [pytest.mark.architecture, pytest.mark.skipif(REAL_OPENSSL is None, reason="openssl is not installed")]

RUN_STUB = """#!/usr/bin/env bash
printf 'docker %s\\n' "$*" >> "$ACTION_LOG"
if [[ "$*" == *"SELECT COUNT(*) FROM core_"* ]]; then
    printf '3\\n'
elif [[ "$*" == *" pg_dump "* ]]; then
    printf -- '-- dump\\nSELECT 1;\\n'
elif [[ "$*" == *" tar -C "* ]]; then
    tar -C "$MEDIA_SOURCE" -cf - media
elif [[ "$*" == *" exec -T cron cat "* ]]; then
    if [[ "${TEST_WIDEN_ANCHOR_MODE:-0}" == "1" ]]; then
        # Simulates a file system that ignores the umask (for example a default ACL).
        chmod 644 /dev/stdout
    fi
    printf '{"version": 1}\\n'
fi
exit 0
"""

VERIFY_STUB = """#!/usr/bin/env bash
printf 'docker %s\\n' "$*" >> "$ACTION_LOG"
if [[ "$*" == *" exec -T db psql "* ]]; then
    if [[ "$*" == *"SELECT COUNT(*)"* ]]; then
        printf '%s\\n' "${TEST_COUNT-1}"
    else
        cat >/dev/null
    fi
fi
exit 0
"""


def _install_scripts(root: Path) -> None:
    ops = root / "scripts" / "ops"
    ops.mkdir(parents=True)
    for name in ("backup.sh", "_backup_common.sh"):
        shutil.copy(OPS_DIR / name, ops / name)
        (ops / name).chmod(0o755)
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


def _stub_bin(tmp_path: Path, body: str) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "docker"
    stub.write_text(body, encoding="utf-8")
    stub.chmod(0o755)
    return bindir


SUNDAY_FIRST_OF_MONTH_DATE_STUB = """#!/usr/bin/env bash
case "$*" in
    "+%u") echo 7 ;;
    "+%d") echo 01 ;;
    *) exec /usr/bin/date "$@" ;;
esac
"""


def _run_backup(
    tmp_path: Path, *, retention_day: bool = False, **overrides: str
) -> tuple[subprocess.CompletedProcess[str], Path]:
    root = tmp_path / "root"
    _install_scripts(root)
    media_source = tmp_path / "media-source"
    (media_source / "media" / "1").mkdir(parents=True)
    (media_source / "media" / "1" / "alpha.bin").write_bytes(b"attachment")
    bindir = _stub_bin(tmp_path, RUN_STUB)
    if retention_day:
        date_stub = bindir / "date"
        date_stub.write_text(SUNDAY_FIRST_OF_MONTH_DATE_STUB, encoding="utf-8")
        date_stub.chmod(0o755)
    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{bindir}:{env['PATH']}",
            "ACTION_LOG": str(tmp_path / "actions.log"),
            "MEDIA_SOURCE": str(media_source),
        }
    )
    env.pop("DJANGO_AUDIT_HWM_PATH", None)
    env.update(overrides)
    result = subprocess.run(
        ["bash", str(root / "scripts" / "ops" / "backup.sh")],
        capture_output=True,
        text=True,
        env=env,
        cwd=root,
        timeout=120,
        stdin=subprocess.DEVNULL,
        # The caller's umask is deliberately permissive: the script must not depend on it.
        preexec_fn=lambda: os.umask(0o022),
    )
    return result, root / "backups"


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_backup_creates_only_owner_readable_files_and_directories(tmp_path: Path) -> None:
    result, backups = _run_backup(tmp_path)

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    files = [path for path in backups.rglob("*") if path.is_file()]
    assert files, "the run must have published a backup set"
    wide_files = {str(path.relative_to(backups)): oct(_mode(path)) for path in files if _mode(path) != 0o600}
    assert wide_files == {}
    directories = [backups, *[path for path in backups.rglob("*") if path.is_dir()]]
    wide_dirs = {str(path.relative_to(backups)): oct(_mode(path)) for path in directories if _mode(path) & 0o077}
    assert wide_dirs == {}


def test_backup_reports_the_file_mode_check(tmp_path: Path) -> None:
    result, _ = _run_backup(tmp_path)

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "File modes checked" in result.stdout
    assert "all 600" in result.stdout


def test_weekly_and_monthly_copies_are_owner_only_and_checked(tmp_path: Path) -> None:
    result, backups = _run_backup(tmp_path, retention_day=True)

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    for stage in ("weekly", "monthly"):
        copies = [path for path in (backups / stage).iterdir() if path.is_file()]
        assert len(copies) == 6, f"{stage}: {[path.name for path in copies]}"
        assert {oct(_mode(path)) for path in copies} == {"0o600"}
        assert f"File modes checked ({stage} copy): 6 files, all 600." in result.stdout


def test_published_set_with_a_wider_mode_fails_the_run_before_publication(tmp_path: Path) -> None:
    result, backups = _run_backup(
        tmp_path,
        DJANGO_AUDIT_HWM_PATH="/data/audit_hwm.json",
        TEST_WIDEN_ANCHOR_MODE="1",
    )

    assert result.returncode != 0
    assert "unexpected file mode 644" in result.stderr
    published = [path.name for path in (backups / "daily").iterdir() if path.is_file()]
    assert published == [], "a set with wide permissions must not be published"


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


def _verify_set(tmp_path: Path, *, with_media: bool, **overrides: str) -> subprocess.CompletedProcess[str]:
    root = tmp_path / "root"
    _install_scripts(root)
    daily = root / "backups" / "daily"
    daily.mkdir(parents=True)
    dump = daily / f"anlaufstelle_{TIMESTAMP}.sql.gz.enc"
    _encrypt(gzip.compress(b"SELECT 1;\n"), dump)
    lines = [f"table_{table}=1" for table in MANIFEST_TABLES]
    lines += [f"backup_timestamp={TIMESTAMP}", f"db_sha256={hashlib.sha256(dump.read_bytes()).hexdigest()}"]
    if with_media:
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w") as archive:
            info = tarfile.TarInfo("media/1/alpha.bin")
            info.size = 3
            archive.addfile(info, io.BytesIO(b"abc"))
        media = daily / f"anlaufstelle_{TIMESTAMP}_media.tar.gz.enc"
        _encrypt(gzip.compress(payload.getvalue()), media)
        lines.append(f"media_sha256={hashlib.sha256(media.read_bytes()).hexdigest()}")
    manifest = daily / f"anlaufstelle_{TIMESTAMP}.manifest"
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _sign(manifest)

    bindir = _stub_bin(tmp_path, VERIFY_STUB)
    env = dict(os.environ)
    env.update({"PATH": f"{bindir}:{env['PATH']}", "ACTION_LOG": str(tmp_path / "actions.log")})
    env.pop("DJANGO_AUDIT_HWM_PATH", None)
    env.update(overrides)
    return subprocess.run(
        ["bash", str(root / "scripts" / "ops" / "backup.sh"), "--verify"],
        capture_output=True,
        text=True,
        env=env,
        cwd=root,
        timeout=120,
        stdin=subprocess.DEVNULL,
    )


def test_verify_states_what_was_checked_and_what_was_not(tmp_path: Path) -> None:
    result = _verify_set(tmp_path, with_media=True)

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    summary = result.stdout.split("Verification summary", 1)[1]
    checked, not_checked = summary.split("Not checked:", 1)
    assert "Checked:" in checked
    for table in RLS_TABLES:
        assert table in checked
    assert "temporary database" in checked
    assert "1 entries" in checked
    # The guard compares nothing row by row; the summary must not claim it does.
    assert "manifest" in not_checked
    assert "live database" in not_checked
    assert "audit chain" in not_checked
    assert "restore-drill.sh --prod" in summary


def test_verify_no_longer_claims_a_consistent_row_comparison(tmp_path: Path) -> None:
    result = _verify_set(tmp_path, with_media=True)

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "konsistent" not in result.stdout
    assert "Zeilenzahl-Abgleich" not in result.stdout


def test_verify_of_a_database_only_set_says_no_media_were_checked(tmp_path: Path) -> None:
    result = _verify_set(tmp_path, with_media=False)

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    checked = result.stdout.split("Verification summary", 1)[1].split("Not checked:", 1)[0]
    assert "no media archive bound" in checked


def test_verify_summary_is_absent_when_the_guard_fails(tmp_path: Path) -> None:
    result = _verify_set(tmp_path, with_media=True, TEST_COUNT="x")

    assert result.returncode != 0
    assert "Verification summary" not in result.stdout
