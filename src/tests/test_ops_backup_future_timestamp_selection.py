"""Future-dated backup names remain selectable despite host clock drift."""

from __future__ import annotations

import gzip
import hashlib
import hmac
import subprocess
from pathlib import Path

import pytest

from tests.test_ops_backup_hwm import AUDIT_KEY, _restore_sandbox
from tests.test_ops_backup_verify_set_binding import _encrypt, _media_payload, _verify, _write_manifest
from tests.test_ops_backup_verify_set_binding import _sandbox as _backup_sandbox

pytestmark = pytest.mark.architecture


@pytest.fixture(scope="module", autouse=True)
def _require_timezone_data() -> None:
    offsets = {
        timezone: subprocess.run(
            ["date", "+%z"],
            capture_output=True,
            check=True,
            env={"TZ": timezone},
            text=True,
            timeout=10,
        ).stdout.strip()
        for timezone in ("Europe/Berlin", "UTC")
    }
    if offsets["Europe/Berlin"] == offsets["UTC"]:
        pytest.fail("tzdata fehlt")


def _timestamp(timezone: str, hours: int = 0) -> str:
    result = subprocess.run(
        ["date", f"--date={hours:+d} hours", "+%Y-%m-%d_%H%M%S"],
        capture_output=True,
        check=True,
        env={"TZ": timezone},
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


@pytest.mark.parametrize("timezone", ["UTC", "Europe/Berlin"])
@pytest.mark.parametrize("hours, expects_warning", [(0, False), (3, True)])
def test_backup_verify_uses_local_set_clock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    timezone: str,
    hours: int,
    expects_warning: bool,
) -> None:
    monkeypatch.setenv("TZ", timezone)
    timestamp = _timestamp(timezone, hours)
    root, bindir, daily = _backup_sandbox(tmp_path)
    dump = daily / f"anlaufstelle_{timestamp}.sql.gz.enc"
    media = daily / f"anlaufstelle_{timestamp}_media.tar.gz.enc"
    _encrypt(gzip.compress(b"new dump"), dump)
    _encrypt(_media_payload("new.bin"), media)
    _write_manifest(daily, timestamp, media=media)

    result = _verify(tmp_path, root, bindir)

    assert result.returncode == 0, result.stderr
    assert ("Zeitstempel in der Zukunft" in result.stderr) is expects_warning


@pytest.mark.parametrize("timezone", ["UTC", "Europe/Berlin"])
@pytest.mark.parametrize("hours, expects_warning", [(0, False), (3, True)])
def test_restore_drill_uses_local_set_clock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    timezone: str,
    hours: int,
    expects_warning: bool,
) -> None:
    monkeypatch.setenv("TZ", timezone)
    timestamp = _timestamp(timezone, hours)
    signature = hmac.new(AUDIT_KEY.encode(), b"{}", hashlib.sha256).hexdigest()

    _, _, result = _restore_sandbox(
        tmp_path,
        hwm_payload=f'{{"version":1,"chains":{{}},"sig":"{signature}"}}',
        source_timestamp=timestamp,
    )

    assert result.returncode == 0, result.stderr
    assert ("Zeitstempel in der Zukunft" in result.stderr) is expects_warning
