"""Regression guard for the native development database target."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

MAKEFILE = Path("Makefile")


def test_db_target_binds_postgres_to_loopback() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    start = text.index("db:\n")
    end = text.index("\ndb-stop:", start)

    assert "-p 127.0.0.1:5432:5432" in text[start:end]
    assert "-p 5432:5432" not in text[start:end]
