"""SQL identifier safety for operator-provided restore database names."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_ops_restore_populated_target import _run, _sandbox

pytestmark = pytest.mark.architecture


def test_restore_rejects_database_name_with_sql_identifier_metacharacter(tmp_path: Path):
    root, bindir, action_log, db_backup, media_backup = _sandbox(tmp_path)
    env_file = root / ".env"
    env_file.write_text(
        env_file.read_text(encoding="utf-8").replace(
            "POSTGRES_DB=anlaufstelle",
            "POSTGRES_DB='anlaufstelle\"fremd'",
        ),
        encoding="utf-8",
    )

    result = _run(root, bindir, action_log, db_backup, media_backup)

    assert result.returncode != 0
    assert "POSTGRES_DB darf nur Buchstaben, Ziffern und Unterstriche enthalten" in result.stderr
    assert action_log.read_text(encoding="utf-8") == ""
