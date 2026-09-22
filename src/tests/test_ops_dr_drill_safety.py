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

    # Refs #2290: der Import laeuft in die Wiederherstellungs-DB, nicht ins Ziel.
    assert 'psql -v ON_ERROR_STOP=1 --single-transaction -U "$SU_USER" "$RESTORE_DB"' in restore
    # Refs #2291: der Satz entsteht im Arbeitsverzeichnis und wird erst am Ende
    # veroeffentlicht — das signierte Manifest als letztes Artefakt.
    assert 'MANIFEST_FILE="${WORK_DIR}/anlaufstelle_${TIMESTAMP}.manifest"' in backup
    assert 'backup_write_hmac "$MANIFEST_FILE"' in backup
    assert backup.index('backup_write_hmac "$MANIFEST_FILE"') < backup.index('publish_artifact "$MANIFEST_FILE"')
    assert backup.index('publish_artifact "$BACKUP_FILE"') < backup.index('publish_artifact "$MANIFEST_FILE"')
    assert "EXPECTED_COUNT=" in completeness
    assert 'if [[ "$DRILL_FORMAT" == "prod" ]]' in completeness
    assert "LIVE_COUNT=" in completeness


def test_media_restore_stages_archive_at_app_path_and_checks_every_attachment() -> None:
    restore = _script("restore.sh")

    assert 'MEDIA_STAGE="${MEDIA_ROOT_CONTAINER}.restore.$$"' in restore
    assert 'tar -C "$MEDIA_STAGE" --strip-components=1 -xf -' in restore
    assert "SELECT facility_id, storage_filename FROM core_attachment" in restore
    assert 'test -f "${MEDIA_STAGE}/$attachment_path"' in restore
    # Refs #2290: der Tausch laeuft als ein sh-Aufruf im maintenance-Container.
    switch = restore.split("# Medien-Aktivierung", maxsplit=1)[1]
    assert switch.index('mv "$stage" "$live"') < switch.index('rm -rf "$old"')
    assert restore.index("SELECT facility_id, storage_filename") < restore.index("# Medien-Aktivierung")


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

    assert 'SELECTED_TIMESTAMP="${LATEST_DB##*/}"' in drill
    assert 'BACKUP_TIMESTAMP="$SELECTED_TIMESTAMP"' in drill
    assert 'HWM_FILE="${BACKUP_DIR}/audit_hwm-${BACKUP_TIMESTAMP}.json"' in hwm_step
    assert 'find "$BACKUP_DIR"' not in hwm_step
    assert '-e POSTGRES_DB="$DRILL_DB"' in hwm_step
    assert "verify_audit_chain --require-anchor" in hwm_step
    assert "Ausgewaehlter Restore-Satz:" in drill
    assert "${HWM_FILE##*/}" in hwm_step
    assert 'HWM_FILE="${BACKUP_FILE%/*}/audit_hwm-${BACKUP_TIMESTAMP}.json"' in restore
    assert "run --rm --entrypoint sh -T maintenance" in restore
    assert 'mv -f "$HWM_TEMP" "$target"' in restore


def test_hwm_transfer_uses_stream_for_read_only_web_container() -> None:
    drill = _script("restore-drill.sh")
    hwm_step = drill.split("# Schritt 2b:", maxsplit=1)[1].split("# Schritt 3:", maxsplit=1)[0]

    assert "dc cp" not in hwm_step
    assert "--entrypoint sh maintenance" in hwm_step
    assert '< "$HWM_FILE"' in hwm_step


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
    completeness = drill.split("# Schritt 3b:", maxsplit=1)[1].split("# Schritt 2b:", maxsplit=1)[0]
    audit = drill.split("# Schritt 5:", maxsplit=1)[1].split("# Schritt 6:", maxsplit=1)[0]
    mark = drill.split("# Schritt 7:", maxsplit=1)[1]

    assert '"${MANIFEST_COUNTS[$tbl]}" -gt 0 && "$COUNT" -eq 0' in samples
    assert "DRILL_UNDETERMINED=0" in drill
    assert 'AUDIT_ROW_COUNT_BEFORE_CHAIN="$RESTORED_COUNT"' in completeness
    assert "SELECT COUNT(*) FROM core_auditlog" not in audit
    assert '"$AUDIT_ROW_COUNT_BEFORE_CHAIN" -eq 0' in audit
    assert "NICHT PRUEFBAR" in audit
    assert "DRILL_UNDETERMINED=1" in audit
    assert '[[ "$DRILL_FAILED" -eq 0 && "$DRILL_UNDETERMINED" -eq 0 ]]' in mark
    assert "DRILL OFFEN" in mark


def test_restore_checks_attachment_at_facility_relative_path() -> None:
    restore = _script("restore.sh")

    assert "SELECT facility_id, storage_filename FROM core_attachment" in restore
    assert 'attachment_path="${facility_id}/${storage_filename}"' in restore
    assert 'test -f "${MEDIA_STAGE}/$attachment_path"' in restore


def test_drill_verifies_audit_chain_in_maintenance_context_and_logs_error() -> None:
    drill = _script("restore-drill.sh")
    hwm_step = drill.split("# Schritt 2b:", maxsplit=1)[1].split("# Schritt 3:", maxsplit=1)[0]

    assert "run --rm -T" in hwm_step
    assert "--entrypoint sh maintenance" in hwm_step
    # Refs #2242: Der Anker kommt unmittelbar davor aus dem Backup-Satz. Ohne
    # --require-anchor wuerde eine signaturgueltige, aber ankerlose HWM-Datei
    # den Drill gruen passieren und den restaurierten Bestand still neu
    # verankern — genau der Fall, den Schritt 2b ausschliessen soll.
    assert "python manage.py verify_audit_chain --require-anchor" in hwm_step
    assert 'POSTGRES_DB="$DRILL_DB"' in hwm_step
    assert '< "$HWM_FILE"' in hwm_step
    assert '2>"$HWM_VERIFY_ERR"' in hwm_step
    assert "Rollen-/Verbindungsfehler" in hwm_step


def test_unconfigured_anchor_check_is_never_silently_ok() -> None:
    """Ohne Anker-Konfiguration darf Schritt 2b nicht als erledigt durchgehen (Refs #2242).

    Beide Formate weisen den nicht ausgefuehrten Schritt ausdruecklich als
    uebersprungen aus — statt ihn als beilaeufige INFO-Zeile zu fuehren oder im
    dev-Format ganz zu verschweigen. Der Prod-Zweig benennt zusaetzlich die
    fehlende Variable samt Env-Datei.
    """
    drill = _script("restore-drill.sh")
    hwm_step = drill.split("# Schritt 2b:", maxsplit=1)[1].split("# Schritt 4:", maxsplit=1)[0]
    summary = drill.split("# Schritt 7:", maxsplit=1)[1]
    notice = "Schritt 2b: Audit-Anker NICHT geprueft (keine Anker-Konfiguration)"

    assert hwm_step.count(f'skip "{notice}') == 2
    assert "DJANGO_AUDIT_HWM_PATH nicht gesetzt (${ENV_FILE})" in hwm_step
    assert "INFO - Schritt 2b" not in hwm_step
    assert "skip() {" in drill
    assert "DRILL_SKIPPED+=" in drill
    assert "Uebersprungen" in summary
    assert "${DRILL_SKIPPED[@]}" in summary


def test_manifest_count_precedes_audit_chain_verification() -> None:
    drill = _script("restore-drill.sh")

    assert drill.index('RESTORED_COUNT" -ne "$EXPECTED_COUNT"') < drill.index("verify_audit_chain")


def test_dev_keeps_live_database_completeness_comparison() -> None:
    drill = _script("restore-drill.sh")
    completeness = drill.split("# Schritt 3b:", maxsplit=1)[1].split("# Schritt 4:", maxsplit=1)[0]

    assert 'if [[ "$DRILL_FORMAT" == "prod" ]]' in completeness
    assert 'LIVE_COUNT=$(psql_su -d "$POSTGRES_DB"' in completeness
    assert 'RESTORED_COUNT" -ne "$LIVE_COUNT"' in completeness


def test_prod_drill_psql_is_fail_closed() -> None:
    drill = _script("restore-drill.sh")
    restore_step = drill.split("# Schritt 2:", maxsplit=1)[1].split("# Schritt 2b:", maxsplit=1)[0]

    assert 'psql -U "$SU_USER" -d "$DRILL_DB" -v ON_ERROR_STOP=1 --single-transaction' in restore_step
    assert 'if [[ "$PSQL_RC" -ne 0 ]]' in restore_step
    assert 'fail "Schritt 2: DB-Restore fehlgeschlagen' in restore_step


def test_manifest_binds_timestamp_and_dump_hash() -> None:
    backup = _script("backup.sh")
    drill = _script("restore-drill.sh")

    assert "printf 'backup_timestamp=%s" in backup
    assert "printf 'db_sha256=%s" in backup
    assert 'sha256sum "$BACKUP_FILE"' in backup
    assert 'MANIFEST_TIMESTAMP" != "$BACKUP_TIMESTAMP"' in drill
    assert 'ACTUAL_DB_SHA256" != "$MANIFEST_DB_SHA256"' in drill


def test_restore_hwm_is_validated_and_atomically_replaced() -> None:
    restore = _script("restore.sh")
    hwm = restore.split('if [[ -n "$HWM_FILE" ]]', maxsplit=2)[2].split("# Medien-Restore", maxsplit=1)[0]

    assert "HWM_EXPECTED_SIZE" in restore
    assert "HWM_EXPECTED_SHA256" in restore
    assert "json.load" in restore
    assert 'data.get("version") != 1' in restore
    assert 'data.get("chains")' in restore
    assert "HighWaterMarkStore.load" in restore
    assert 'mv -f "$HWM_TEMP" "$target"' in restore
    assert restore.index("HighWaterMarkStore.load") < restore.index('mv -f "$HWM_TEMP" "$target"')
    assert 'cat > "$DJANGO_AUDIT_HWM_PATH"' not in hwm


def test_hand_check_builds_bad_sql_before_valid_sidecar_and_separates_control() -> None:
    hand_checks_path = REPO_ROOT / "docs" / "dev" / "hand-checks.md"
    if not hand_checks_path.exists():
        pytest.skip("docs/dev/hand-checks.md nicht im Public-Snapshot — Guard nur im Dev-Repo relevant")
    hand_checks = hand_checks_path.read_text(encoding="utf-8")
    line = next(line for line in hand_checks.splitlines() if line.startswith("- [#2221]"))
    normalized = line.lower()

    assert "fehlerhaftes sql im klartext" in normalized
    assert (
        normalized.index("fehlerhaftes sql im klartext")
        < normalized.index("komprimieren")
        < normalized.index("sidecar")
    )
    assert "db_sha256" in normalized
    assert "manifest neu signieren" in normalized
    assert normalized.index("komprimieren") < normalized.index("db_sha256") < normalized.index("manifest neu signieren")
    assert "getrennter kontrolllauf" in normalized


def test_changelog_has_one_fixed_bullet_per_ticket_position() -> None:
    if not (REPO_ROOT / "docs" / "dev" / "hand-checks.md").exists():
        pytest.skip(
            "docs/dev/hand-checks.md fehlt = Public-Snapshot; Changelog-Refs sind dort umgeschrieben "
            "— Guard nur im Dev-Repo relevant"
        )
    # Der Guard darf nicht vom Release-Stand abhaengen: beim Cut wandern die
    # Bullets aus ``[Unreleased]`` in den Versionsblock (Lehre 88). Die Aussage
    # "die Bullets stehen unter ### Fixed" bleibt erhalten: es wird ueber ALLE
    # Fixed-Abschnitte summiert, nicht ueber die ganze Datei.
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    fixed_sections = [part.split("###", maxsplit=1)[0] for part in changelog.split("### Fixed")[1:]]

    assert sum(section.count("[#2221]") for section in fixed_sections) == 7


def test_prod_drill_accepts_explicit_backup_timestamp() -> None:
    drill = _script("restore-drill.sh")

    assert "--timestamp" in drill
    assert 'BACKUP_TIMESTAMP="${BACKUP_TIMESTAMP:-}"' in drill
    assert "anlaufstelle_${BACKUP_TIMESTAMP}.sql.gz.enc" in drill
    assert "Ohne explizite Satzwahl" in drill


def test_restore_reports_restored_backup_timestamp() -> None:
    restore = _script("restore.sh")

    assert "RESTORE-SATZ WIEDERHERGESTELLT: ${BACKUP_TIMESTAMP}" in restore
