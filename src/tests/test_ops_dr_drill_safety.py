"""Architektur-Guards fuer fail-closed Backup- und Restore-Drills (#2221)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
OPS_DIR = REPO_ROOT / "scripts" / "ops"


def _script(name: str) -> str:
    return (OPS_DIR / name).read_text(encoding="utf-8")


def test_restore_fails_closed_and_drill_uses_authenticated_manifest_counts() -> None:
    restore = _script("restore.sh")
    backup = _script("backup.sh")
    drill = _script("restore-drill.sh")
    completeness = drill.split("# Schritt 3b:", maxsplit=1)[1].split("# Schritt 4:", maxsplit=1)[0]

    assert 'psql -v ON_ERROR_STOP=1 --single-transaction -U "$SU_USER" "$POSTGRES_DB"' in restore
    assert 'MANIFEST_FILE="${DAILY_DIR}/anlaufstelle_${TIMESTAMP}.manifest"' in backup
    assert 'backup_write_hmac "$MANIFEST_FILE"' in backup
    assert "EXPECTED_COUNT=" in completeness
    assert "LIVE_COUNT=" not in completeness


def test_media_restore_stages_archive_at_app_path_and_checks_every_attachment() -> None:
    restore = _script("restore.sh")

    assert 'MEDIA_STAGE="${MEDIA_ROOT_CONTAINER}.restore.$$"' in restore
    assert 'tar -C "$MEDIA_STAGE" --strip-components=1 -xf -' in restore
    assert "SELECT storage_filename FROM core_attachment" in restore
    assert 'test -f "${MEDIA_STAGE}/$storage_filename"' in restore
    assert restore.index('mv "$MEDIA_STAGE" "$MEDIA_ROOT_CONTAINER"') < restore.index('rm -rf "$MEDIA_OLD"')


def test_offsite_sync_covers_all_retention_tiers_before_rotation() -> None:
    backup = _script("backup.sh")

    assert backup.index("# Off-Site-Sync") < backup.index("# Rotation")
    for directory in ("$DAILY_DIR", "$WEEKLY_DIR", "$MONTHLY_DIR"):
        assert f'rclone copy "{directory}"' in backup
        assert f'aws s3 sync "{directory}"' in backup
        assert directory in backup.split("SCP-Target", maxsplit=1)[1]
    assert 'if [[ "$OFFSITE_OK" == false ]]; then' in backup
    assert "Rotation ausgesetzt" in backup


def test_drill_binds_hwm_to_dump_timestamp_and_restored_audit_chain() -> None:
    restore = _script("restore.sh")
    drill = _script("restore-drill.sh")
    hwm_step = drill.split("# Schritt 2b:", maxsplit=1)[1].split("# Schritt 3:", maxsplit=1)[0]

    assert 'BACKUP_TIMESTAMP="${LATEST_DB##*/}"' in drill
    assert 'HWM_FILE="${BACKUP_DIR}/audit_hwm-${BACKUP_TIMESTAMP}.json"' in hwm_step
    assert 'find "$BACKUP_DIR"' not in hwm_step
    assert '-e POSTGRES_DB="$DRILL_DB"' in hwm_step
    assert "verify_audit_chain" in hwm_step
    assert "Ausgewaehlter Restore-Satz:" in drill
    assert "${HWM_FILE##*/}" in hwm_step
    assert 'HWM_FILE="${BACKUP_FILE%/*}/audit_hwm-${BACKUP_TIMESTAMP}.json"' in restore
    assert "run --rm --entrypoint sh -T maintenance" in restore
    assert "cat > '$DJANGO_AUDIT_HWM_PATH'" in restore


def test_hwm_transfer_uses_stream_for_read_only_web_container() -> None:
    drill = _script("restore-drill.sh")
    hwm_step = drill.split("# Schritt 2b:", maxsplit=1)[1].split("# Schritt 3:", maxsplit=1)[0]

    assert "dc cp" not in hwm_step
    assert 'dc exec -T web sh -c "cat > \'$HWM_DRILL_CONTAINER_PATH\'" < "$HWM_FILE"' in hwm_step


def test_hmac_error_lists_ambiguous_root_causes() -> None:
    common = _script("_backup_common.sh")
    mismatch = common.split('if [[ "$expected" != "$actual" ]]', maxsplit=1)[1].split("fi", maxsplit=1)[0]

    assert "BACKUP_ENCRYPTION_KEY" in mismatch
    assert "beschädigte Datei" in mismatch
    assert "Sidecar eines anderen Backups" in mismatch
    assert "ist manipuliert oder beschädigt" not in mismatch


def test_empty_expected_tables_and_audit_probe_keep_drill_open() -> None:
    drill = _script("restore-drill.sh")
    samples = drill.split("# Schritt 3:", maxsplit=1)[1].split("# Schritt 3b:", maxsplit=1)[0]
    audit = drill.split("# Schritt 5:", maxsplit=1)[1].split("# Schritt 6:", maxsplit=1)[0]
    mark = drill.split("# Schritt 7:", maxsplit=1)[1]

    assert '"${MANIFEST_COUNTS[$tbl]}" -gt 0 && "$COUNT" -eq 0' in samples
    assert "DRILL_UNDETERMINED=0" in drill
    assert "AUDIT_ROW_COUNT=" in audit
    assert "NICHT PRUEFBAR" in audit
    assert "DRILL_UNDETERMINED=1" in audit
    assert '[[ "$DRILL_FAILED" -eq 0 && "$DRILL_UNDETERMINED" -eq 0 ]]' in mark
    assert "DRILL OFFEN" in mark
