"""``backup.sh`` keeps backup files to their owner and ``--verify`` reports what it did and did not check.

Backups hold Art. 9 data, so every file and directory a run creates must carry no
permission for others, whatever umask the caller (cron, sudo) has. Dump, media
archive, audit anchor and the ``.hmac`` signatures are for the owner only (``0600``).
The group may read the manifest alone (``0640``, directories ``0750``): the ``web``
service reads it through the group to show the last backup.
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
    if [[ -n "${TEST_CRON_DOWN:-}" ]]; then
        # The cron sidecar is not running: compose cannot exec into it.
        exit 1
    fi
    if [[ -n "${TEST_ANCHOR_MODE:-}" ]]; then
        # Simulates a file system that ignores the umask (for example a default ACL).
        chmod "$TEST_ANCHOR_MODE" /dev/stdout
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
    "+%Y-%m-%d_%H%M%S")
        if [[ -n "${TEST_FIXED_TIMESTAMP:-}" ]]; then echo "$TEST_FIXED_TIMESTAMP"; else exec /usr/bin/date "$@"; fi ;;
    *) exec /usr/bin/date "$@" ;;
esac
"""

# Simulates a file system that ignores the umask for copies (for example a default ACL).
CP_STUB = """#!/usr/bin/env bash
command -p cp "$@" || exit $?
if [[ -n "${TEST_CP_MODE:-}" ]]; then
    for copied in "${@: -1}"/*; do
        chmod "$TEST_CP_MODE" "$copied"
    done
fi
"""


def _run_backup(
    tmp_path: Path, *, retention_day: bool = False, again: bool = False, **overrides: str
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run ``backup.sh`` in ``tmp_path``; ``again=True`` reuses the tree of a previous run."""
    root = tmp_path / "root"
    media_source = tmp_path / "media-source"
    if again:
        bindir = tmp_path / "bin"
    else:
        _install_scripts(root)
        (media_source / "media" / "1").mkdir(parents=True)
        (media_source / "media" / "1" / "alpha.bin").write_bytes(b"attachment")
        bindir = _stub_bin(tmp_path, RUN_STUB)
    for name, body in (("cp", CP_STUB), *((("date", SUNDAY_FIRST_OF_MONTH_DATE_STUB),) if retention_day else ())):
        stub = bindir / name
        stub.write_text(body, encoding="utf-8")
        stub.chmod(0o755)
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


def _expected_mode(path: Path) -> int:
    """Only the manifest is group-readable; every other artefact is for the owner alone."""
    return 0o640 if path.name.endswith(".manifest") else 0o600


def test_backup_gives_group_read_only_to_the_manifest(tmp_path: Path) -> None:
    result, backups = _run_backup(tmp_path, DJANGO_AUDIT_HWM_PATH="/data/audit_hwm.json")

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    files = [path for path in backups.rglob("*") if path.is_file()]
    names = {path.name for path in files}
    assert any(name.endswith(".manifest") for name in names), names
    assert any(name.startswith("audit_hwm-") for name in names), names
    # Group read is deliberate for the manifest (the web service reads it); nothing else.
    odd_files = {
        str(path.relative_to(backups)): oct(_mode(path)) for path in files if _mode(path) != _expected_mode(path)
    }
    assert odd_files == {}
    directories = [backups, *[path for path in backups.rglob("*") if path.is_dir()]]
    odd_dirs = {str(path.relative_to(backups)): oct(_mode(path)) for path in directories if _mode(path) != 0o750}
    assert odd_dirs == {}


def test_backup_reports_the_file_mode_check(tmp_path: Path) -> None:
    result, _ = _run_backup(tmp_path)

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "File modes checked (backup set)" in result.stdout
    assert "only the manifest readable by the group, none accessible to others" in result.stdout


def test_weekly_and_monthly_copies_have_nothing_for_others_and_are_checked(tmp_path: Path) -> None:
    result, backups = _run_backup(tmp_path, retention_day=True)

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    for stage in ("weekly", "monthly"):
        copies = [path for path in (backups / stage).iterdir() if path.is_file()]
        assert len(copies) == 6, f"{stage}: {[path.name for path in copies]}"
        assert {path.name: oct(_mode(path)) for path in copies} == {
            path.name: oct(_expected_mode(path)) for path in copies
        }
        assert (
            f"File modes checked ({stage} copy): 6 files, only the manifest readable by the group,"
            " none accessible to others." in result.stdout
        )


@pytest.mark.parametrize("mode", ["600", "400"])
def test_a_published_set_with_owner_only_artefacts_passes_the_check(tmp_path: Path, mode: str) -> None:
    result, backups = _run_backup(
        tmp_path,
        DJANGO_AUDIT_HWM_PATH="/data/audit_hwm.json",
        TEST_ANCHOR_MODE=mode,
    )

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "none accessible to others" in result.stdout
    assert any((backups / "daily").iterdir())


# A file system that ignores the umask (default ACL) can leave group or other rights
# on an artefact; any of them stops the run before anything is published.
@pytest.mark.parametrize("mode", ["640", "660", "610", "644", "604", "641"])
def test_a_published_set_with_rights_beyond_the_owner_fails_the_run_before_publication(
    tmp_path: Path, mode: str
) -> None:
    result, backups = _run_backup(
        tmp_path,
        DJANGO_AUDIT_HWM_PATH="/data/audit_hwm.json",
        TEST_ANCHOR_MODE=mode,
    )

    assert result.returncode != 0
    assert f"unexpected file mode {mode}" in result.stderr
    published = [path.name for path in (backups / "daily").iterdir() if path.is_file()]
    assert published == [], "a set with rights for others must not be published"


def _daily_files(backups: Path) -> list[str]:
    return sorted(path.name for path in (backups / "daily").iterdir() if path.is_file())


@pytest.mark.parametrize("mode", ["644", "640"])
def test_a_retention_copy_with_rights_beyond_the_owner_is_not_published_at_all(tmp_path: Path, mode: str) -> None:
    result, backups = _run_backup(tmp_path, retention_day=True, TEST_CP_MODE=mode)

    assert result.returncode != 0
    assert f"unexpected file mode {mode}" in result.stderr
    # Nothing of the rejected copy is visible, not even a staging directory.
    assert list((backups / "weekly").iterdir()) == []
    assert list((backups / "monthly").iterdir()) == []
    # The daily set was checked and published before; it stays.
    assert len(_daily_files(backups)) == 6


def test_a_rejected_retention_copy_leaves_the_earlier_copies_untouched(tmp_path: Path) -> None:
    first, backups = _run_backup(tmp_path, retention_day=True, TEST_FIXED_TIMESTAMP="2026-09-06_020000")
    assert first.returncode == 0, first.stderr
    before = {path.name: path.read_bytes() for path in (backups / "weekly").iterdir()}

    second, _ = _run_backup(
        tmp_path,
        retention_day=True,
        again=True,
        TEST_FIXED_TIMESTAMP="2026-09-13_020000",
        TEST_CP_MODE="644",
    )

    assert second.returncode != 0
    assert {path.name: path.read_bytes() for path in (backups / "weekly").iterdir()} == before


def test_a_repeated_retention_copy_replaces_wide_leftovers_with_owner_only_files(tmp_path: Path) -> None:
    stamp = "2026-09-06_020000"
    first, backups = _run_backup(tmp_path, retention_day=True, TEST_FIXED_TIMESTAMP=stamp)
    assert first.returncode == 0, first.stderr
    for stage in ("weekly", "monthly"):
        for path in (backups / stage).iterdir():
            path.chmod(0o644)

    second, _ = _run_backup(tmp_path, retention_day=True, again=True, TEST_FIXED_TIMESTAMP=stamp)

    assert second.returncode == 0, f"STDOUT:\n{second.stdout}\nSTDERR:\n{second.stderr}"
    for stage in ("weekly", "monthly"):
        copies = [path for path in (backups / stage).iterdir()]
        assert len(copies) == 6
        assert {path.name: oct(_mode(path)) for path in copies} == {
            path.name: oct(_expected_mode(path)) for path in copies
        }


def test_without_the_cron_sidecar_the_set_carries_no_audit_anchor(tmp_path: Path) -> None:
    result, backups = _run_backup(
        tmp_path,
        DJANGO_AUDIT_HWM_PATH="/data/audit_hwm.json",
        TEST_CRON_DOWN="1",
    )

    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "KEINEN Audit-Anker" in result.stderr
    assert not [name for name in _daily_files(backups) if name.startswith("audit_hwm-")]


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
