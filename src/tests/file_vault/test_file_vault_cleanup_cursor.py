"""Validation of the stored resume cursor of the media tree sweep (Refs #2376)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.services.file_vault import cleanup as cleanup_module

INVALID_PATH_WARNING = "Fortsetzungspunkt hat einen ungueltigen Pfad; Start am Anfang."


def _read_cursor(tmp_path: Path, cursor: str) -> tuple[str | None, str | None]:
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"after": cursor}), encoding="utf-8")
    return cleanup_module._read_cleanup_cursor(state_path)


@pytest.mark.parametrize(
    "cursor",
    ["..", "../a.enc", "sub/../../a.enc", "/a.enc", "/srv/media/a.enc"],
)
def test_cursor_leaving_the_media_root_restarts_with_warning(tmp_path, cursor):
    assert _read_cursor(tmp_path, cursor) == (None, INVALID_PATH_WARNING)


@pytest.mark.parametrize(
    "cursor",
    ["a.enc", "sub/a.enc", ".hidden.enc", "sub/..a.enc", "./a.enc", "sub/./a.enc"],
)
def test_relative_cursor_inside_the_media_root_is_kept(tmp_path, cursor):
    # ``pathlib`` drops ``.`` components, so a cursor containing them
    # compares like its normalised form and is kept unchanged.
    assert _read_cursor(tmp_path, cursor) == (cursor, None)
