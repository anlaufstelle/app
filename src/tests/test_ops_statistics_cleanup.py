"""The restore drill must reject every restore error after cache removal."""

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

RESTORE_DRILL = Path(__file__).resolve().parents[2] / "scripts/ops/restore-drill.sh"


def test_dev_restore_rejects_a_materialized_cache_refresh_error(tmp_path):
    backup = tmp_path / "backups"
    backup.mkdir()
    (backup / "dump-2026-11-12.pgc.enc").write_bytes(b"test dump")
    environment_file = tmp_path / "restore-test.env"
    environment_file.write_text(
        "BACKUP_ENCRYPTION_KEY=real-test-key-0123456789abcdef\nPOSTGRES_DB=restore_drill_live\n"
    )
    binaries = tmp_path / "bin"
    binaries.mkdir()
    commands = {
        "openssl": "#!/usr/bin/env bash\nprintf 'dump'\n",
        "docker": (
            "#!/usr/bin/env bash\n"
            'if [[ "$*" == *pg_restore* ]]; then\n'
            "  cat >/dev/null\n"
            "  echo 'pg_restore: error: REFRESH MATERIALIZED VIEW retired_cache' >&2\n"
            "  exit 1\n"
            "fi\nexit 0\n"
        ),
    }
    for name, source in commands.items():
        executable = binaries / name
        executable.write_text(source)
        executable.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{binaries}:{os.environ['PATH']}",
        "COMPOSE_DIR": str(tmp_path),
        "ENV_FILE": str(environment_file),
        "DEV_BACKUP_DIR": str(backup),
    }
    # Ambient values must not decide the outcome: the drill reads both from the env file.
    for ambient in ("BACKUP_TIMESTAMP", "POSTGRES_DB"):
        environment.pop(ambient, None)
    result = subprocess.run(
        ["bash", str(RESTORE_DRILL), "--dev"], env=environment, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAIL - Schritt 2: DB-Restore fehlgeschlagen" in result.stdout, result.stdout + result.stderr
    assert "OK - Schritt 2:" not in result.stdout, result.stdout + result.stderr
