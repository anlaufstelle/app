"""SQL identifier safety for operator-provided restore database names."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_ops_restore_populated_target import _run, _sandbox

pytestmark = pytest.mark.architecture

_IDENTIFIER_ERROR = "darf nur Buchstaben, Ziffern, Unterstriche und Bindestriche enthalten"


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
    assert f"POSTGRES_DB {_IDENTIFIER_ERROR}" in result.stderr
    assert action_log.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("admin_user", ['admin"fremd', "admin user", "admin$user"])
def test_restore_rejects_unsafe_admin_user_before_any_action(tmp_path: Path, admin_user: str):
    root, bindir, action_log, db_backup, media_backup = _sandbox(tmp_path)
    with (root / ".env").open("a", encoding="utf-8") as env_file:
        env_file.write(f"POSTGRES_ADMIN_USER='{admin_user}'\n")

    result = _run(root, bindir, action_log, db_backup, media_backup)

    assert result.returncode != 0
    assert f"POSTGRES_ADMIN_USER {_IDENTIFIER_ERROR}" in result.stderr
    assert action_log.read_text(encoding="utf-8") == ""


def test_restore_accepts_hyphens_in_database_and_admin_identifiers(tmp_path: Path):
    root, bindir, action_log, db_backup, media_backup = _sandbox(tmp_path)
    env_file = root / ".env"
    env_file.write_text(
        env_file.read_text(encoding="utf-8")
        .replace("POSTGRES_DB=anlaufstelle", "POSTGRES_DB=anlaufstelle-test")
        .replace("POSTGRES_ADMIN_USER=adminuser", "POSTGRES_ADMIN_USER=admin-user"),
        encoding="utf-8",
    )

    result = _run(root, bindir, action_log, db_backup, media_backup)

    assert result.returncode == 0, result.stderr
    actions = action_log.read_text(encoding="utf-8")
    assert 'OWNER "admin-user"' in actions
    assert 'ALTER DATABASE "anlaufstelle-test"' in actions
