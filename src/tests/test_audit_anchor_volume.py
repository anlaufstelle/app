"""Deployment and backup contracts for the isolated audit anchor."""

from __future__ import annotations

import errno
from pathlib import Path

import pytest
import yaml
from django.core.management import call_command
from django.core.management.base import CommandError

from core.models import AuditLog
from core.services.audit import audit_event
from core.services.audit.chain import verify_chain
from core.services.audit.hwm import HighWaterMarkStore
from core.services.system.health import last_backup_info
from tests.test_ops_backup_anchor_warning import _run_backup, _sandbox
from tests.test_system_health import _write_backup_set

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.architecture
@pytest.mark.parametrize("compose_name", ["prod", "staging", "dev", "demo", "barbara"])
def test_anchor_volume_is_read_only_for_web_and_writable_only_for_maintenance(compose_name):
    compose_path = ROOT / (
        "dev-ops/deploy/barbara/docker-compose.barbara.yml"
        if compose_name == "barbara"
        else f"docker-compose.{compose_name}.yml"
    )
    if compose_name == "barbara" and not compose_path.is_file():
        pytest.skip("dev-ops/deploy/barbara/docker-compose.barbara.yml not in the public snapshot — dev repo only")
    if compose_name == "dev" and not compose_path.is_file():
        pytest.skip("docker-compose.dev.yml not in the public snapshot — dev repo only")
    if compose_name == "staging" and not compose_path.is_file():
        pytest.skip("docker-compose.staging.yml not in the public snapshot — dev repo only")
    if compose_name == "demo" and not compose_path.is_file():
        pytest.skip("docker-compose.demo.yml not in the public snapshot — dev repo only")
    compose = yaml.safe_load(compose_path.read_text())
    assert "audit-anchor" in compose["volumes"]
    mounts = {}
    for name, service in compose["services"].items():
        for mount in service.get("volumes", []):
            if isinstance(mount, str) and mount.split(":")[0] == "audit-anchor":
                mounts[name] = mount.split(":")
    assert set(mounts) == {name for name in ("web", "cron", "maintenance") if name in compose["services"]}
    assert mounts["web"][1] == "/data"
    assert mounts["web"][-1] == "ro"
    for name in ("cron", "maintenance"):
        if name in mounts:
            assert mounts[name][-1] == "rw"
    for name in mounts:
        service = compose["services"][name]
        media_value = service["environment"]["MEDIA_ROOT"]
        assert media_value in ("/media/media", "${MEDIA_ROOT:-/media/media}")
        assert any(
            isinstance(mount, str) and mount.split(":")[1] == "/media" and "ro" not in mount.split(":")[2:]
            for mount in service["volumes"]
        )
        value = service["environment"]["DJANGO_AUDIT_HWM_PATH"]
        assert value.startswith("${DJANGO_AUDIT_HWM_PATH:-/")
        assert value == "${DJANGO_AUDIT_HWM_PATH:-/data/audit_hwm.json}"
        path = value.split(":-", 1)[1][:-1]
        assert Path(mounts[name][1]) in Path(path).parents


@pytest.mark.architecture
@pytest.mark.parametrize("mode", ["missing", "empty"])
def test_unreadable_configured_anchor_aborts_without_publishing(tmp_path, mode):
    root, bindir = _sandbox(tmp_path, anchor_path="/fake/unreadable-anchor.json")
    result = _run_backup(root, bindir, TEST_HWM_MODE=mode, expected_returncode=1)
    assert "ERROR: Audit anchor" in result.stderr
    assert not list((root / "backups" / "daily").glob("*.manifest"))
    assert not list((root / "backups" / "daily").glob("*.enc"))


@pytest.mark.django_db
def test_verify_command_reports_read_only_anchor_without_success_marker(settings, tmp_path, monkeypatch):
    path = tmp_path / "audit_hwm.json"
    HighWaterMarkStore(path=str(path)).save()
    before = path.read_bytes()
    settings.AUDIT_HWM_PATH = str(path)
    count = AuditLog.objects.count()
    attempts = []

    def read_only_volume(*args, **kwargs):
        attempts.append(kwargs["dir"])
        raise OSError(errno.EROFS, "Read-only file system")

    monkeypatch.setattr("core.services.audit.hwm.tempfile.mkstemp", read_only_volume)
    with pytest.raises(CommandError, match="AUDIT_HWM_PATH.*not writable"):
        call_command("verify_audit_chain")
    assert attempts == [str(tmp_path)]
    assert path.read_bytes() == before
    assert AuditLog.objects.count() == count


@pytest.mark.django_db
def test_chain_verification_reads_anchor_without_writing(facility, staff_user, tmp_path, monkeypatch):
    entry = audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
    entry.refresh_from_db()
    path = tmp_path / "audit_hwm.json"
    store = HighWaterMarkStore(path=str(path))
    store.record(facility.pk, 1, entry.entry_hash)
    store.save()
    before = (path.read_bytes(), path.stat().st_mtime_ns)

    def deny_write(*args, **kwargs):
        raise AssertionError("Read-only verification must not write the anchor")

    monkeypatch.setattr("core.services.audit.hwm.tempfile.mkstemp", deny_write)
    result = verify_chain(facility, hwm=HighWaterMarkStore.load(str(path)))
    assert result.ok
    assert before == (path.read_bytes(), path.stat().st_mtime_ns)


def test_backup_health_reads_set_anchor_instead_of_live_anchor(settings, tmp_path):
    manifest = _write_backup_set(tmp_path / "backups", "2026-09-12_020000")
    manifest.write_text(manifest.read_text() + f"hwm_sha256={'a' * 64}\n")
    anchor = manifest.with_name("audit_hwm-2026-09-12_020000.json")
    anchor.write_text("{}")
    settings.AUDIT_HWM_PATH = str(tmp_path / "unavailable-live-anchor.json")
    before = (anchor.read_bytes(), anchor.stat().st_mtime_ns)
    assert last_backup_info(manifest.parent) is not None
    assert before == (anchor.read_bytes(), anchor.stat().st_mtime_ns)
    anchor.unlink()
    assert last_backup_info(manifest.parent) is None


@pytest.mark.architecture
def test_image_initializes_anchor_volume_with_the_maintenance_user():
    """Docker volume initialization needs an existing, correctly owned target."""
    import shlex

    commands = [
        line.removeprefix("RUN ") for line in (ROOT / "Dockerfile").read_text().splitlines() if line.startswith("RUN ")
    ]
    assert any(
        "/data" in shlex.split(command.split("&&")[0]) and "chown -R appuser:appuser /media /data" in command
        for command in commands
    )


def test_deploy_check_recommends_the_writable_media_volume(settings):
    from django.core.checks import run_checks

    settings.DEBUG = False
    settings.BASE_DIR = Path("/app")
    settings.MEDIA_ROOT = "/app/media"
    errors = run_checks(include_deployment_checks=True, databases=[])
    error = next(error for error in errors if error.id == "core.E002")
    assert "MEDIA_ROOT=/media/media" in error.hint


@pytest.mark.architecture
@pytest.mark.parametrize("compose_name", ["prod", "staging", "dev", "demo", "barbara"])
def test_missing_compose_files_follow_the_public_profile(compose_name, tmp_path, monkeypatch):
    import sys

    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    if compose_name == "prod":
        with pytest.raises(FileNotFoundError):
            test_anchor_volume_is_read_only_for_web_and_writable_only_for_maintenance(compose_name)
    else:
        with pytest.raises(pytest.skip.Exception, match="not in the public snapshot"):
            test_anchor_volume_is_read_only_for_web_and_writable_only_for_maintenance(compose_name)
