"""Exercise verification of a complete signed set through the shipped script."""

import gzip
import hashlib
import os
import shutil
import subprocess

import pytest

from tests.test_ops_restore_populated_target import OPS_DIR, _encrypt, _sandbox, _sign, _write_executable

pytestmark = pytest.mark.architecture


@pytest.mark.parametrize("failure", ["import", "prepare", "query", "invalid_live", "media", "cleanup", "none"])
def test_verify_rejects_every_failed_part_and_cleans_only_temporary_database(tmp_path, failure):
    root, bindir, log, db, _media = _sandbox(tmp_path)
    if failure == "import":
        tables = ("core_client", "core_event", "core_workitem", "core_auditlog")
        sql = "".join(f"CREATE TABLE {table} (id integer); INSERT INTO {table} VALUES (1);\n" for table in tables)
        sql += "CREATE INDEX broken ON nonexistent_table (id);\n"
        _encrypt(gzip.compress(sql.encode()), db)
        manifest = next(db.parent.glob("*.manifest"))
        lines = manifest.read_text().splitlines()
        lines = [
            f"db_sha256={hashlib.sha256(db.read_bytes()).hexdigest()}" if line.startswith("db_sha256=") else line
            for line in lines
        ]
        manifest.write_text("\n".join(lines) + "\n")
        _sign(manifest)
    shutil.copy(OPS_DIR / "backup.sh", root / "scripts" / "ops" / "backup.sh")
    _write_executable(
        bindir / "docker",
        """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$ACTION_LOG"
if [[ "$*" == *'DROP DATABASE'* && "$TEST_FAILURE" == cleanup ]]; then
    echo 'ERROR: cannot clean verification database' >&2
    exit 1
elif [[ "$*" == *'CREATE DATABASE'* && "$TEST_FAILURE" == prepare ]]; then
    echo 'ERROR: cannot create database' >&2
    [[ "$*" == *'ON_ERROR_STOP=1'* ]] && exit 1
elif [[ "$*" == *'SELECT COUNT(*)'* ]]; then
    if [[ "$TEST_FAILURE" == query ]]; then
        echo 'ERROR: query failed' >&2
        exit 1
    fi
    if [[ "$TEST_FAILURE" == invalid_live && "$*" != *' -d anlaufstelle_verify_'* ]]; then
        echo invalid
    else
        echo 1
    fi
elif [[ "$*" != *'DROP DATABASE'* && "$*" != *'CREATE DATABASE'* ]]; then
    sql=$(cat)
    if [[ "$TEST_FAILURE" == import && "$sql" == *'CREATE INDEX broken'* ]]; then
        echo 'ERROR: late index failure after all guard tables' >&2
        [[ "$*" == *'ON_ERROR_STOP=1'* ]] && exit 1
    fi
fi
exit 0
""",
    )
    if failure == "media":
        _write_executable(
            bindir / "tar", "#!/usr/bin/env bash\ncat >/dev/null\necho 'ERROR: invalid archive' >&2\nexit 1\n"
        )
    result = subprocess.run(
        ["bash", str(root / "scripts" / "ops" / "backup.sh"), "--verify"],
        env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "ACTION_LOG": str(log), "TEST_FAILURE": failure},
        capture_output=True,
        text=True,
        timeout=60,
    )
    if failure == "none":
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Verification summary" in result.stdout
    else:
        assert result.returncode != 0, result.stdout + result.stderr
        assert "ERROR" in result.stdout + result.stderr
        assert "DB-Backup ist gueltig" not in result.stdout
        assert "Verification summary" not in result.stdout
    drops = [line for line in log.read_text().splitlines() if "DROP DATABASE" in line]
    assert len(drops) == 1
    assert 'DROP DATABASE IF EXISTS "anlaufstelle_verify_' in drops[0]
