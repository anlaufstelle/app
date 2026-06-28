"""Unit tests for ``core.services.compliance`` (Refs #919)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from core.models import AuditLog
from core.models.user import User
from core.services import compliance


def _make_role_check(label: str, role: str, actual_super: bool | None, actual_bypassrls: bool | None):
    """Mock fuer ``RoleCheck`` ohne Postgres-Roundtrip."""
    from core.management.commands.check_db_roles import RoleCheck

    return RoleCheck(
        role=role,
        label=label,
        expected_super=False,
        expected_bypassrls=(label == "Admin"),
        actual_super=actual_super,
        actual_bypassrls=actual_bypassrls,
    )


@pytest.mark.django_db
class TestAggregateChecks:
    def test_returns_a_list(self):
        result = compliance.aggregate_checks()
        assert isinstance(result, list)
        assert all(isinstance(c, compliance.ComplianceCheck) for c in result)

    def test_includes_all_categories(self):
        result = compliance.aggregate_checks()
        categories = {c.category for c in result}
        assert {"Datenbank", "Backup", "Virus-Scan", "Retention", "MFA", "System", "Audit"} <= categories

    def test_single_helper_failure_does_not_crash_aggregator(self):
        with patch.object(compliance, "_db_role_checks", side_effect=RuntimeError("boom")):
            result = compliance.aggregate_checks()
        # Anderer Checks laufen weiter.
        assert any(c.key.startswith("_internal_") for c in result)
        internal = [c for c in result if c.key.startswith("_internal_")][0]
        assert internal.status == compliance.ComplianceStatus.UNKNOWN
        assert "boom" in (internal.detail or "")

    def test_status_enum_values(self):
        for status in compliance.ComplianceStatus:
            assert status.value in {"ok", "warning", "critical", "unknown"}


@pytest.mark.django_db
class TestCronJobChecks:
    """Refs #977: gebuendelte Last-Run-Checks der Hintergrundjobs fuer die /system/-Uebersicht."""

    def test_returns_list_of_compliance_checks(self):
        result = compliance.cron_job_checks()
        assert isinstance(result, list)
        assert all(isinstance(c, compliance.ComplianceCheck) for c in result)

    def test_contains_the_five_cron_job_keys(self):
        keys = {c.key for c in compliance.cron_job_checks()}
        assert {
            "backup_age",
            "retention_last_run",
            "snapshot_last_run",
            "breach_scan_last_run",
            "mv_refresh_last_run",
        } <= keys

    def test_single_helper_failure_does_not_crash(self):
        with patch.object(compliance, "_snapshot_checks", side_effect=RuntimeError("boom")):
            result = compliance.cron_job_checks()
        assert any(c.key.startswith("_internal_") for c in result)
        internal = [c for c in result if c.key.startswith("_internal_")][0]
        assert internal.status == compliance.ComplianceStatus.UNKNOWN


@pytest.mark.django_db
class TestDbRoleChecks:
    def test_ok_when_app_role_correct(self):
        app = _make_role_check("App", "anlaufstelle", actual_super=False, actual_bypassrls=False)
        admin = _make_role_check("Admin", "anlaufstelle_admin", actual_super=False, actual_bypassrls=True)
        with patch("core.management.commands.check_db_roles.check_db_roles", return_value=([app, admin], [])):
            checks = compliance._db_role_checks()
        # 2 App-Attribute + 1 Admin = 3 Checks.
        assert len(checks) == 3
        assert all(c.status == compliance.ComplianceStatus.OK for c in checks)

    def test_critical_when_app_is_superuser(self):
        app = _make_role_check("App", "anlaufstelle", actual_super=True, actual_bypassrls=False)
        with patch("core.management.commands.check_db_roles.check_db_roles", return_value=([app], [])):
            checks = compliance._db_role_checks()
        super_check = next(c for c in checks if c.key == "db_role_app_nosuperuser")
        assert super_check.status == compliance.ComplianceStatus.CRITICAL
        assert "rolsuper=True" in super_check.detail

    def test_critical_when_app_has_bypassrls(self):
        app = _make_role_check("App", "anlaufstelle", actual_super=False, actual_bypassrls=True)
        with patch("core.management.commands.check_db_roles.check_db_roles", return_value=([app], [])):
            checks = compliance._db_role_checks()
        bypass_check = next(c for c in checks if c.key == "db_role_app_nobypassrls")
        assert bypass_check.status == compliance.ComplianceStatus.CRITICAL

    def test_warning_when_admin_role_missing(self):
        app = _make_role_check("App", "anlaufstelle", actual_super=False, actual_bypassrls=False)
        with patch(
            "core.management.commands.check_db_roles.check_db_roles",
            return_value=([app], ["POSTGRES_ADMIN_USER ist nicht gesetzt"]),
        ):
            checks = compliance._db_role_checks()
        admin_check = next(c for c in checks if c.key == "db_role_admin_missing")
        assert admin_check.status == compliance.ComplianceStatus.WARNING
        assert "POSTGRES_ADMIN_USER" in (admin_check.detail or "")

    def test_unknown_when_app_role_not_in_pg_roles(self):
        app = _make_role_check("App", "ghost", actual_super=None, actual_bypassrls=None)
        with patch("core.management.commands.check_db_roles.check_db_roles", return_value=([app], [])):
            checks = compliance._db_role_checks()
        assert all(c.status == compliance.ComplianceStatus.UNKNOWN for c in checks)


@pytest.mark.django_db
class TestAppSuperuserChecks:
    """Refs #1297: Django-Ebenen-Guard — kein App-``User`` hat is_superuser=True.

    Bewusst abgegrenzt von :class:`TestDbRoleChecks` (prueft die
    PostgreSQL-Rollen-Attribute ueber den ``check_db_roles``-Command) und von
    #793 (Health-Endpoint NOSUPERUSER auf der Postgres-Rolle): dieser Check
    betrachtet ausschliesslich das Django-``User.is_superuser``-Feld.
    """

    def test_ok_when_no_app_user_is_superuser(self):
        User.objects.create_user(username="emma", password="x", role=User.Role.LEAD)
        checks = compliance._app_superuser_checks()
        assert len(checks) == 1
        assert checks[0].key == "app_user_no_django_superuser"
        assert checks[0].status == compliance.ComplianceStatus.OK

    def test_critical_when_an_app_user_is_superuser(self):
        rogue = User.objects.create_user(username="rogue", password="x", role=User.Role.STAFF)
        rogue.is_superuser = True
        rogue.save(update_fields=["is_superuser"])
        checks = compliance._app_superuser_checks()
        assert checks[0].status == compliance.ComplianceStatus.CRITICAL
        assert "rogue" in (checks[0].detail or "")

    def test_aggregate_includes_app_superuser_check(self):
        keys = {c.key for c in compliance.aggregate_checks()}
        assert "app_user_no_django_superuser" in keys


@pytest.mark.django_db
class TestBackupChecks:
    def test_unknown_when_no_backup_info(self):
        with patch("core.services.system.health.last_backup_info", return_value=None):
            checks = compliance._backup_checks()
        assert checks[0].status == compliance.ComplianceStatus.UNKNOWN

    def test_ok_when_backup_under_24h(self):
        with patch(
            "core.services.system.health.last_backup_info",
            return_value={"path": "/var/backups/x.sql", "mtime": None, "age_hours": 6.0, "is_stale": False},
        ):
            checks = compliance._backup_checks()
        assert checks[0].status == compliance.ComplianceStatus.OK

    def test_warning_when_backup_24_to_72h(self):
        with patch(
            "core.services.system.health.last_backup_info",
            return_value={"path": "/var/backups/x.sql", "mtime": None, "age_hours": 48.0, "is_stale": True},
        ):
            checks = compliance._backup_checks()
        assert checks[0].status == compliance.ComplianceStatus.WARNING

    def test_critical_when_backup_over_72h(self):
        with patch(
            "core.services.system.health.last_backup_info",
            return_value={"path": "/var/backups/x.sql", "mtime": None, "age_hours": 96.0, "is_stale": True},
        ):
            checks = compliance._backup_checks()
        assert checks[0].status == compliance.ComplianceStatus.CRITICAL


@pytest.mark.django_db
class TestRestoreChecks:
    def test_unknown_when_no_restore_entry(self):
        AuditLog.objects.filter(action=AuditLog.Action.RESTORE_VERIFIED).delete()
        checks = compliance._restore_checks()
        assert checks[0].status == compliance.ComplianceStatus.UNKNOWN

    def test_ok_when_restore_within_90d(self):
        AuditLog.objects.filter(action=AuditLog.Action.RESTORE_VERIFIED).delete()
        AuditLog.objects.create(
            action=AuditLog.Action.RESTORE_VERIFIED,
            facility=None,
            target_type="RestoreVerification",
            detail={"note": "test"},
        )
        checks = compliance._restore_checks()
        assert checks[0].status == compliance.ComplianceStatus.OK

    def test_warning_when_restore_90_to_180d(self):
        AuditLog.objects.filter(action=AuditLog.Action.RESTORE_VERIFIED).delete()
        old = AuditLog.objects.create(
            action=AuditLog.Action.RESTORE_VERIFIED,
            facility=None,
            target_type="RestoreVerification",
            detail={},
        )
        from core.services.system import bypass_replication_triggers

        with bypass_replication_triggers():
            AuditLog.objects.filter(pk=old.pk).update(timestamp=timezone.now() - timedelta(days=120))
        checks = compliance._restore_checks()
        assert checks[0].status == compliance.ComplianceStatus.WARNING

    def test_critical_when_restore_over_180d(self):
        AuditLog.objects.filter(action=AuditLog.Action.RESTORE_VERIFIED).delete()
        old = AuditLog.objects.create(
            action=AuditLog.Action.RESTORE_VERIFIED,
            facility=None,
            target_type="RestoreVerification",
            detail={},
        )
        from core.services.system import bypass_replication_triggers

        with bypass_replication_triggers():
            AuditLog.objects.filter(pk=old.pk).update(timestamp=timezone.now() - timedelta(days=400))
        checks = compliance._restore_checks()
        assert checks[0].status == compliance.ComplianceStatus.CRITICAL


@pytest.mark.django_db
class TestClamavChecks:
    def test_critical_when_ping_false(self):
        with (
            patch("core.services.file_vault.virus_scan.ping", return_value=False),
            patch("core.services.file_vault.virus_scan.signature_info", return_value=None),
        ):
            checks = compliance._clamav_checks()
        reach = next(c for c in checks if c.key == "clamav_reachable")
        assert reach.status == compliance.ComplianceStatus.CRITICAL

    def test_ok_when_ping_true(self):
        with (
            patch("core.services.file_vault.virus_scan.ping", return_value=True),
            patch("core.services.file_vault.virus_scan.signature_info", return_value=None),
        ):
            checks = compliance._clamav_checks()
        reach = next(c for c in checks if c.key == "clamav_reachable")
        assert reach.status == compliance.ComplianceStatus.OK

    def test_signature_unknown_when_none(self):
        with (
            patch("core.services.file_vault.virus_scan.ping", return_value=True),
            patch("core.services.file_vault.virus_scan.signature_info", return_value=None),
        ):
            checks = compliance._clamav_checks()
        sig = next(c for c in checks if c.key == "clamav_signature")
        assert sig.status == compliance.ComplianceStatus.UNKNOWN

    def test_signature_ok_when_fresh(self):
        with (
            patch("core.services.file_vault.virus_scan.ping", return_value=True),
            patch(
                "core.services.file_vault.virus_scan.signature_info",
                return_value={"version": "1.4.0", "signature_date": None, "age_days": 3},
            ),
        ):
            checks = compliance._clamav_checks()
        sig = next(c for c in checks if c.key == "clamav_signature")
        assert sig.status == compliance.ComplianceStatus.OK

    def test_signature_warning_when_old(self):
        with (
            patch("core.services.file_vault.virus_scan.ping", return_value=True),
            patch(
                "core.services.file_vault.virus_scan.signature_info",
                return_value={"version": "1.4.0", "signature_date": None, "age_days": 30},
            ),
        ):
            checks = compliance._clamav_checks()
        sig = next(c for c in checks if c.key == "clamav_signature")
        assert sig.status == compliance.ComplianceStatus.WARNING


@pytest.mark.django_db
class TestRetentionChecks:
    def test_unknown_when_no_entry(self):
        AuditLog.objects.filter(action=AuditLog.Action.RETENTION_RUN_COMPLETED).delete()
        checks = compliance._retention_checks()
        assert checks[0].status == compliance.ComplianceStatus.UNKNOWN

    def test_ok_when_within_7d(self):
        AuditLog.objects.filter(action=AuditLog.Action.RETENTION_RUN_COMPLETED).delete()
        AuditLog.objects.create(
            action=AuditLog.Action.RETENTION_RUN_COMPLETED,
            facility=None,
            target_type="RetentionRun",
        )
        checks = compliance._retention_checks()
        assert checks[0].status == compliance.ComplianceStatus.OK

    def test_warning_when_7_to_14d(self):
        AuditLog.objects.filter(action=AuditLog.Action.RETENTION_RUN_COMPLETED).delete()
        old = AuditLog.objects.create(
            action=AuditLog.Action.RETENTION_RUN_COMPLETED, facility=None, target_type="RetentionRun"
        )
        from core.services.system import bypass_replication_triggers

        with bypass_replication_triggers():
            AuditLog.objects.filter(pk=old.pk).update(timestamp=timezone.now() - timedelta(days=10))
        checks = compliance._retention_checks()
        assert checks[0].status == compliance.ComplianceStatus.WARNING

    def test_critical_when_over_14d(self):
        AuditLog.objects.filter(action=AuditLog.Action.RETENTION_RUN_COMPLETED).delete()
        old = AuditLog.objects.create(
            action=AuditLog.Action.RETENTION_RUN_COMPLETED, facility=None, target_type="RetentionRun"
        )
        from core.services.system import bypass_replication_triggers

        with bypass_replication_triggers():
            AuditLog.objects.filter(pk=old.pk).update(timestamp=timezone.now() - timedelta(days=30))
        checks = compliance._retention_checks()
        assert checks[0].status == compliance.ComplianceStatus.CRITICAL


@pytest.mark.django_db
class TestMfaChecks:
    def test_unknown_when_no_privileged_users(self, facility):
        User.objects.filter(role__in=compliance._PRIVILEGED_ROLES).delete()
        checks = compliance._mfa_checks()
        assert checks[0].status == compliance.ComplianceStatus.UNKNOWN

    def test_critical_when_no_mfa(self, facility):
        # Erstelle privilegierte User ohne MFA
        User.objects.filter(role__in=compliance._PRIVILEGED_ROLES).delete()
        for i in range(3):
            User.objects.create_user(
                username=f"prov{i}",
                password="anlaufstelle2026",
                facility=facility,
                role=User.Role.LEAD,
            )
        checks = compliance._mfa_checks()
        assert checks[0].status == compliance.ComplianceStatus.CRITICAL


@pytest.mark.django_db
class TestMigrationChecks:
    def test_ok_when_no_pending(self):
        with patch("core.services.system.health.pending_migrations", return_value=[]):
            checks = compliance._migration_checks()
        assert checks[0].status == compliance.ComplianceStatus.OK

    def test_critical_when_pending(self):
        with patch("core.services.system.health.pending_migrations", return_value=[("core", "0099_x")]):
            checks = compliance._migration_checks()
        assert checks[0].status == compliance.ComplianceStatus.CRITICAL


@pytest.mark.django_db
class TestVersionChecks:
    def test_returns_version_string(self):
        checks = compliance._version_checks()
        assert checks[0].status == compliance.ComplianceStatus.OK
        assert "Django" in checks[0].message
        assert "Python" in checks[0].message


@pytest.mark.django_db
class TestAuditEventChecks:
    def test_ok_when_no_critical_events(self):
        AuditLog.objects.filter(action__in=compliance._CRITICAL_AUDIT_ACTIONS).delete()
        checks = compliance._audit_event_checks()
        assert checks[0].status == compliance.ComplianceStatus.OK

    def test_warning_when_1_to_5_events(self, facility, staff_user):
        AuditLog.objects.filter(action__in=compliance._CRITICAL_AUDIT_ACTIONS).delete()
        for _ in range(3):
            AuditLog.objects.create(
                facility=facility,
                user=staff_user,
                action=AuditLog.Action.MFA_FAILED,
                target_type="User",
                target_id=str(staff_user.pk),
            )
        checks = compliance._audit_event_checks()
        assert checks[0].status == compliance.ComplianceStatus.WARNING

    def test_sudo_mode_failed_counts_as_critical_event(self, facility, staff_user):
        """SUDO_MODE_FAILED zaehlt fuer Check #11 — analog MFA_FAILED (Refs #1084)."""
        AuditLog.objects.filter(action__in=compliance._CRITICAL_AUDIT_ACTIONS).delete()
        AuditLog.objects.create(
            facility=facility,
            user=staff_user,
            action=AuditLog.Action.SUDO_MODE_FAILED,
            target_type="User",
            target_id=str(staff_user.pk),
        )
        checks = compliance._audit_event_checks()
        assert checks[0].status == compliance.ComplianceStatus.WARNING

    def test_critical_when_over_5_events(self, facility, staff_user):
        AuditLog.objects.filter(action__in=compliance._CRITICAL_AUDIT_ACTIONS).delete()
        for _ in range(7):
            AuditLog.objects.create(
                facility=facility,
                user=staff_user,
                action=AuditLog.Action.SECURITY_VIOLATION,
                target_type="EventAttachment",
            )
        checks = compliance._audit_event_checks()
        assert checks[0].status == compliance.ComplianceStatus.CRITICAL

    def test_only_24h_window_counted(self, facility, staff_user):
        AuditLog.objects.filter(action__in=compliance._CRITICAL_AUDIT_ACTIONS).delete()
        # Eintrag vor 48h — sollte nicht zaehlen.
        old = AuditLog.objects.create(
            facility=facility,
            user=staff_user,
            action=AuditLog.Action.MFA_FAILED,
            target_type="User",
        )
        from core.services.system import bypass_replication_triggers

        with bypass_replication_triggers():
            AuditLog.objects.filter(pk=old.pk).update(timestamp=timezone.now() - timedelta(hours=48))
        checks = compliance._audit_event_checks()
        assert checks[0].status == compliance.ComplianceStatus.OK


@pytest.mark.django_db
class TestCronChecks:
    """Last-Run-Checks für die per systemd-Timer laufenden Cron-Jobs (Refs #794)."""

    def _backdate(self, entry, **delta):
        from core.services.system import bypass_replication_triggers

        with bypass_replication_triggers():
            AuditLog.objects.filter(pk=entry.pk).update(timestamp=timezone.now() - timedelta(**delta))

    # --- Snapshots (monatlich) ---
    def test_snapshot_unknown_when_no_entry(self):
        AuditLog.objects.filter(action=AuditLog.Action.SNAPSHOT_RUN_COMPLETED).delete()
        assert compliance._snapshot_checks()[0].status == compliance.ComplianceStatus.UNKNOWN

    def test_snapshot_ok_within_35d(self):
        AuditLog.objects.filter(action=AuditLog.Action.SNAPSHOT_RUN_COMPLETED).delete()
        AuditLog.objects.create(action=AuditLog.Action.SNAPSHOT_RUN_COMPLETED, facility=None, target_type="SnapshotRun")
        assert compliance._snapshot_checks()[0].status == compliance.ComplianceStatus.OK

    def test_snapshot_warning_35_to_65d(self):
        AuditLog.objects.filter(action=AuditLog.Action.SNAPSHOT_RUN_COMPLETED).delete()
        e = AuditLog.objects.create(
            action=AuditLog.Action.SNAPSHOT_RUN_COMPLETED, facility=None, target_type="SnapshotRun"
        )
        self._backdate(e, days=50)
        assert compliance._snapshot_checks()[0].status == compliance.ComplianceStatus.WARNING

    def test_snapshot_critical_over_65d(self):
        AuditLog.objects.filter(action=AuditLog.Action.SNAPSHOT_RUN_COMPLETED).delete()
        e = AuditLog.objects.create(
            action=AuditLog.Action.SNAPSHOT_RUN_COMPLETED, facility=None, target_type="SnapshotRun"
        )
        self._backdate(e, days=80)
        assert compliance._snapshot_checks()[0].status == compliance.ComplianceStatus.CRITICAL

    # --- Breach-Scan (stündlich) ---
    def test_breach_unknown_when_no_entry(self):
        AuditLog.objects.filter(action=AuditLog.Action.BREACH_SCAN_COMPLETED).delete()
        assert compliance._breach_scan_checks()[0].status == compliance.ComplianceStatus.UNKNOWN

    def test_breach_ok_within_3h(self):
        AuditLog.objects.filter(action=AuditLog.Action.BREACH_SCAN_COMPLETED).delete()
        AuditLog.objects.create(
            action=AuditLog.Action.BREACH_SCAN_COMPLETED, facility=None, target_type="BreachScanRun"
        )
        assert compliance._breach_scan_checks()[0].status == compliance.ComplianceStatus.OK

    def test_breach_warning_3_to_24h(self):
        AuditLog.objects.filter(action=AuditLog.Action.BREACH_SCAN_COMPLETED).delete()
        e = AuditLog.objects.create(
            action=AuditLog.Action.BREACH_SCAN_COMPLETED, facility=None, target_type="BreachScanRun"
        )
        self._backdate(e, hours=10)
        assert compliance._breach_scan_checks()[0].status == compliance.ComplianceStatus.WARNING

    def test_breach_critical_over_24h(self):
        AuditLog.objects.filter(action=AuditLog.Action.BREACH_SCAN_COMPLETED).delete()
        e = AuditLog.objects.create(
            action=AuditLog.Action.BREACH_SCAN_COMPLETED, facility=None, target_type="BreachScanRun"
        )
        self._backdate(e, hours=30)
        assert compliance._breach_scan_checks()[0].status == compliance.ComplianceStatus.CRITICAL

    # --- MV-Refresh (stündlich) ---
    def test_mv_unknown_when_no_entry(self):
        AuditLog.objects.filter(action=AuditLog.Action.MV_REFRESH_COMPLETED).delete()
        assert compliance._mv_refresh_checks()[0].status == compliance.ComplianceStatus.UNKNOWN

    def test_mv_ok_within_2h(self):
        AuditLog.objects.filter(action=AuditLog.Action.MV_REFRESH_COMPLETED).delete()
        AuditLog.objects.create(action=AuditLog.Action.MV_REFRESH_COMPLETED, facility=None, target_type="MVRefreshRun")
        assert compliance._mv_refresh_checks()[0].status == compliance.ComplianceStatus.OK

    def test_mv_warning_2_to_6h(self):
        AuditLog.objects.filter(action=AuditLog.Action.MV_REFRESH_COMPLETED).delete()
        e = AuditLog.objects.create(
            action=AuditLog.Action.MV_REFRESH_COMPLETED, facility=None, target_type="MVRefreshRun"
        )
        self._backdate(e, hours=4)
        assert compliance._mv_refresh_checks()[0].status == compliance.ComplianceStatus.WARNING

    def test_mv_critical_over_6h(self):
        AuditLog.objects.filter(action=AuditLog.Action.MV_REFRESH_COMPLETED).delete()
        e = AuditLog.objects.create(
            action=AuditLog.Action.MV_REFRESH_COMPLETED, facility=None, target_type="MVRefreshRun"
        )
        self._backdate(e, hours=10)
        assert compliance._mv_refresh_checks()[0].status == compliance.ComplianceStatus.CRITICAL
