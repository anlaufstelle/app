"""The documented archive comparison fails closed: a read error is never a success.

The block is extracted from the disaster-recovery guide and executed as printed.
"""

from __future__ import annotations

import io
import os
import shlex
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS = REPO_ROOT / "docs"

pytestmark = pytest.mark.architecture


def _archive_block_body(document: str) -> str:
    """Return the ``sh -c`` script of the media archive fidelity block."""
    fences = document.split("```bash\n")[1:]
    block = next(part.split("```", 1)[0] for part in fences if part.startswith("MEDIA_ARCHIVE="))
    tokens = shlex.split(block)
    return tokens[tokens.index("-c") + 1]


def _tar_of(files: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, payload in files.items():
            info = tarfile.TarInfo(f"media/{name}")
            info.size = len(payload)
            info.mode = 0o640
            archive.addfile(info, io.BytesIO(payload))
    return stream.getvalue()


def _compare(body: str, work: Path, media: Path, archive: bytes) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["sh", "-c", body, "sh", str(work / "media-verify"), str(media)],
        input=archive,
        capture_output=True,
        timeout=30,
    )


@pytest.fixture
def archive_block() -> str:
    return _archive_block_body((DOCS / "disaster-recovery.md").read_text(encoding="utf-8"))


def test_archive_comparison_accepts_identical_trees(archive_block: str, tmp_path: Path) -> None:
    media = tmp_path / "media"
    (media / "1").mkdir(parents=True)
    (media / "1" / "a.enc").write_bytes(b"alpha")

    result = _compare(archive_block, tmp_path, media, _tar_of({"1/a.enc": b"alpha"}))

    assert result.returncode == 0, result.stderr.decode()
    assert not (tmp_path / "media-verify").exists(), "the work directory must be cleaned up"


@pytest.mark.parametrize(
    "live",
    [{"1/a.enc": b"changed"}, {"1/a.enc": b"alpha", "1/extra.enc": b"extra"}, {}],
    ids=["changed", "extra-file", "missing-file"],
)
def test_archive_comparison_rejects_a_difference(archive_block: str, tmp_path: Path, live: dict[str, bytes]) -> None:
    media = tmp_path / "media"
    media.mkdir()
    for name, payload in live.items():
        (media / name).parent.mkdir(parents=True, exist_ok=True)
        (media / name).write_bytes(payload)

    result = _compare(archive_block, tmp_path, media, _tar_of({"1/a.enc": b"alpha"}))

    assert result.returncode != 0


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every directory; the container runs as UID 1000")
def test_archive_comparison_fails_when_a_directory_of_the_live_tree_is_unreadable(
    archive_block: str, tmp_path: Path
) -> None:
    media = tmp_path / "media"
    (media / "1").mkdir(parents=True)
    (media / "1" / "a.enc").write_bytes(b"alpha")
    hidden = media / "2"
    hidden.mkdir()
    (hidden / "b.enc").write_bytes(b"beta")
    hidden.chmod(0)
    try:
        result = _compare(archive_block, tmp_path, media, _tar_of({"1/a.enc": b"alpha"}))
    finally:
        hidden.chmod(0o700)

    assert b"Permission denied" in result.stderr, result.stderr.decode()
    assert result.returncode != 0, "a read error must never count as a successful comparison"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every directory; the container runs as UID 1000")
def test_archive_comparison_fails_when_the_archive_side_is_unreadable_too(archive_block: str, tmp_path: Path) -> None:
    media = tmp_path / "media"
    (media / "1").mkdir(parents=True)
    (media / "1" / "a.enc").write_bytes(b"alpha")
    (media / "1" / "a.enc").chmod(0)
    try:
        result = _compare(archive_block, tmp_path, media, _tar_of({"1/a.enc": b"alpha"}))
    finally:
        (media / "1" / "a.enc").chmod(0o600)

    assert result.returncode != 0
