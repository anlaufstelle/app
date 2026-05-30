"""Follow-Up-Tests für Mutation-Survivors in ``core.services.compliance``.

Refs Welle 7 (#930). Ziel: Mutationen an den Branch-Grenzen von
``_audit_event_checks``, ``_mfa_checks``, ``_retention_checks`` killen.

Die Funktionen branchen auf festen Schwellwerten (Count/Age-Days/Percent).
Mutmut mutiert typischerweise ``<=``/`<`/``>``/``>=`` und Konstanten;
um diese Mutationen zu fangen, decken die Tests die exakten Boundary-
Werte ab (z.B. 7/8 Tage, 5/6 Events, 80/100 %).

AuditLog-Einträge sind per Datenbank-Trigger (Migration 0024) immutable —
``UPDATE timestamp`` ist nicht möglich. Stattdessen patchen wir
``core.services.compliance._clock.now`` so, dass die Check-Funktion eine
relative "jetzt"-Zeit sieht; reale ``auto_now_add``-Timestamps der
Audit-Entries bleiben unverändert.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from django_otp.plugins.otp_totp.models import TOTPDevice

from core.models import AuditLog, User
from core.services import compliance
from core.services.compliance import (
    ComplianceStatus,
    _audit_event_checks,
    _mfa_checks,
    _retention_checks,
)


def _patch_compliance_now(fixed: datetime):
    """Kontext-Manager: ``core.services.compliance._clock.now`` → ``fixed``.

    Refs #958-M3: ``_clock.now`` ist die zentrale Time-Source aller Submodule
    (backup, retention, audit_events). Ein Patch hier deckt alle Branches ab.
    """
    return patch("core.services.compliance._clock.now", return_value=fixed)


def _create_retention_audit(facility) -> AuditLog:
    """RETENTION_RUN_COMPLETED-Audit mit aktuellem (real) Timestamp."""
    return AuditLog.objects.create(
        action=AuditLog.Action.RETENTION_RUN_COMPLETED,
        facility=facility,
        detail={"event_count": 0},
    )


@pytest.mark.django_db
class TestRetentionChecks:
    """Refs Welle 7 Cluster ``DSGVO/Security`` — `_retention_checks`."""

    def test_no_audit_yields_unknown(self):
        """Ohne RETENTION_RUN_COMPLETED-Eintrag → UNKNOWN."""
        result = _retention_checks()
        assert len(result) == 1
        assert result[0].status == ComplianceStatus.UNKNOWN

    def test_age_zero_days_is_ok(self, facility):
        entry = _create_retention_audit(facility)
        with _patch_compliance_now(entry.timestamp):
            result = _retention_checks()
        assert result[0].status == ComplianceStatus.OK

    def test_age_seven_days_still_ok(self, facility):
        """Boundary: ``age_days <= 7`` ist OK. Mutation ``< 7`` würde failen."""
        entry = _create_retention_audit(facility)
        with _patch_compliance_now(entry.timestamp + timedelta(days=7, hours=1)):
            result = _retention_checks()
        assert result[0].status == ComplianceStatus.OK, f"Erwarte OK bei 7 Tagen, bekomme {result[0].status}"

    def test_age_eight_days_is_warning(self, facility):
        """Boundary: ``age_days > 7`` schaltet auf WARNING."""
        entry = _create_retention_audit(facility)
        with _patch_compliance_now(entry.timestamp + timedelta(days=8, hours=1)):
            result = _retention_checks()
        assert result[0].status == ComplianceStatus.WARNING

    def test_age_fourteen_days_still_warning(self, facility):
        """Boundary: ``age_days <= 14`` bleibt WARNING. Mutation ``< 14`` würde failen."""
        entry = _create_retention_audit(facility)
        with _patch_compliance_now(entry.timestamp + timedelta(days=14, hours=1)):
            result = _retention_checks()
        assert result[0].status == ComplianceStatus.WARNING

    def test_age_fifteen_days_is_critical(self, facility):
        """Boundary: ``age_days > 14`` schaltet auf CRITICAL."""
        entry = _create_retention_audit(facility)
        with _patch_compliance_now(entry.timestamp + timedelta(days=15, hours=1)):
            result = _retention_checks()
        assert result[0].status == ComplianceStatus.CRITICAL


# ---------------------------------------------------------------------------
# _audit_event_checks — Boundary 0 / 5 Events in 24h
# ---------------------------------------------------------------------------


def _create_security_violation(facility) -> AuditLog:
    """Single SECURITY_VIOLATION-Eintrag mit aktuellem (real) Timestamp."""
    return AuditLog.objects.create(
        action=AuditLog.Action.SECURITY_VIOLATION,
        facility=facility,
        detail={"reason": "test"},
    )


@pytest.mark.django_db
class TestAuditEventChecks:
    """Refs Welle 7 Cluster ``DSGVO/Security`` — `_audit_event_checks`."""

    def test_no_critical_events_is_ok(self):
        result = _audit_event_checks()
        assert result[0].status == ComplianceStatus.OK

    def test_one_critical_event_is_warning(self, facility):
        _create_security_violation(facility)
        result = _audit_event_checks()
        assert result[0].status == ComplianceStatus.WARNING
        assert "1 kritische Event" in result[0].message

    def test_five_critical_events_still_warning(self, facility):
        """Boundary: ``count <= 5`` bleibt WARNING. Mutation ``< 5`` würde failen."""
        for _ in range(5):
            _create_security_violation(facility)
        result = _audit_event_checks()
        assert result[0].status == ComplianceStatus.WARNING

    def test_six_critical_events_is_critical(self, facility):
        """Boundary: ``count > 5`` schaltet auf CRITICAL."""
        for _ in range(6):
            _create_security_violation(facility)
        result = _audit_event_checks()
        assert result[0].status == ComplianceStatus.CRITICAL

    def test_old_events_outside_24h_window_ignored(self, facility):
        """Cutoff: Events älter als 24h zählen nicht.

        Wir simulieren "Zukunft" via Mock auf ``compliance.datetime.now``:
        - Ein Event jetzt → außerhalb 24h aus Mock-Sicht.
        - Ein zweites Event direkt vor dem Mock-Now → innerhalb 24h.
        """
        old_entry = _create_security_violation(facility)
        # Mock-Now ist 25h nach ``old_entry.timestamp`` — old_entry liegt
        # also außerhalb des 24h-Fensters.
        mock_now = old_entry.timestamp + timedelta(hours=25)
        with _patch_compliance_now(mock_now):
            result = _audit_event_checks()
        assert result[0].status == ComplianceStatus.OK
        assert "Keine kritischen" in result[0].message

    def test_non_critical_action_not_counted(self, facility, staff_user):
        """Nur ``_CRITICAL_AUDIT_ACTIONS`` zählen — Standard-LOGIN ist nicht kritisch."""
        AuditLog.objects.create(
            action=AuditLog.Action.LOGIN,
            facility=facility,
            user=staff_user,
            detail={},
        )
        result = _audit_event_checks()
        assert result[0].status == ComplianceStatus.OK


# ---------------------------------------------------------------------------
# _mfa_checks — Boundary 80 / 100 % MFA-Quote
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMfaChecks:
    """Refs Welle 7 Cluster ``DSGVO/Security`` — `_mfa_checks`.

    Branch-Schwellen:
    - 0 privileged → "Keine privilegierten User" (UNKNOWN, kein Quote-Check)
    - 100 % MFA → OK
    - 80–99 % → WARNING
    - <80 % → CRITICAL
    """

    PRIVILEGED_ROLES = [
        User.Role.SUPER_ADMIN,
        User.Role.FACILITY_ADMIN,
        User.Role.LEAD,
    ]

    @staticmethod
    def _make_privileged_user(facility, *, with_mfa: bool, suffix: str) -> User:
        user = User.objects.create_user(
            username=f"mfa-test-{suffix}",
            password="x" * 24,
            role=User.Role.LEAD,
            facility=facility,
        )
        if with_mfa:
            TOTPDevice.objects.create(user=user, name="totp", confirmed=True)
        return user

    def _purge_privileged(self) -> None:
        User.objects.filter(role__in=self.PRIVILEGED_ROLES).delete()

    def test_no_privileged_users_returns_unknown(self, facility):
        """Edge-Case: keine privilegierten User → UNKNOWN-Status."""
        self._purge_privileged()
        result = _mfa_checks()
        assert len(result) == 1
        assert result[0].status == ComplianceStatus.UNKNOWN
        assert "Keine privilegierten" in result[0].message

    def test_hundred_percent_mfa_is_ok(self, facility):
        """Boundary: ``percent >= 100`` → OK."""
        self._purge_privileged()
        self._make_privileged_user(facility, with_mfa=True, suffix="a")
        self._make_privileged_user(facility, with_mfa=True, suffix="b")
        result = _mfa_checks()
        assert result[0].status == ComplianceStatus.OK

    def test_eighty_percent_mfa_is_warning(self, facility):
        """Boundary: ``percent >= 80`` aber < 100 → WARNING.

        4 von 5 privilegierten User = 80 %.
        """
        self._purge_privileged()
        for i in range(4):
            self._make_privileged_user(facility, with_mfa=True, suffix=f"y{i}")
        self._make_privileged_user(facility, with_mfa=False, suffix="z")
        result = _mfa_checks()
        assert result[0].status == ComplianceStatus.WARNING

    def test_below_eighty_percent_mfa_is_critical(self, facility):
        """Boundary: ``percent < 80`` → CRITICAL.

        3 von 5 = 60 %.
        """
        self._purge_privileged()
        for i in range(3):
            self._make_privileged_user(facility, with_mfa=True, suffix=f"c{i}")
        for i in range(2):
            self._make_privileged_user(facility, with_mfa=False, suffix=f"n{i}")
        result = _mfa_checks()
        assert result[0].status == ComplianceStatus.CRITICAL

    def test_inactive_users_excluded(self, facility):
        """Inaktive User zählen nicht zur Quote."""
        self._purge_privileged()
        self._make_privileged_user(facility, with_mfa=True, suffix="active")
        inactive = self._make_privileged_user(facility, with_mfa=False, suffix="dead")
        inactive.is_active = False
        inactive.save()
        # 1 aktiver privilegierter User, hat MFA → 100 %.
        result = _mfa_checks()
        assert result[0].status == ComplianceStatus.OK

    def test_unconfirmed_totp_does_not_count(self, facility):
        """Nur ``confirmed=True``-TOTPDevices zählen als MFA-aktiv."""
        self._purge_privileged()
        user = self._make_privileged_user(facility, with_mfa=False, suffix="unconf")
        TOTPDevice.objects.create(user=user, name="unconfirmed", confirmed=False)
        result = _mfa_checks()
        # 0 von 1 → 0 % → CRITICAL.
        assert result[0].status == ComplianceStatus.CRITICAL


# ============================================================================
# Welle 9 (#942): Follow-Up-Tests für Compliance-Logic-Survivors
#
# Pragmas auf Display-Strings (label/category/message/action_hint) haben
# ~260 UI-String-Mutations entfernt. Verbleibende Survivors sind echte
# Logic-Lücken, primär in den Status-Branches der Helper-Funktionen.
# ============================================================================


class _AdminRoleCheck:
    """Mini-Helper für `_db_role_admin_check`-Tests ohne Postgres-Roundtrip.

    Sieht aus wie ``core.management.commands.check_db_roles.RoleCheck``, hat
    die Felder, die ``compliance._db_role_admin_check`` liest: ``role``,
    ``actual_super``, ``actual_bypassrls``, ``ok``-Property, ``problems()``.
    """

    def __init__(self, role: str, actual_super: bool | None, actual_bypassrls: bool | None):
        self.role = role
        self.actual_super = actual_super
        self.actual_bypassrls = actual_bypassrls

    @property
    def ok(self) -> bool:
        return self.actual_super is False and self.actual_bypassrls is True

    def problems(self) -> list[str]:
        out: list[str] = []
        if self.actual_super is not False:
            out.append(f"actual_super={self.actual_super} (erwartet: False)")
        if self.actual_bypassrls is not True:
            out.append(f"actual_bypassrls={self.actual_bypassrls} (erwartet: True)")
        return out


class TestDbRoleAdminCheck:
    """Branch-Grenzen für `_db_role_admin_check` (Refs Welle 9 #942).

    Test_compliance_service.py:TestDbRoleChecks deckt den App-Pfad ab, aber
    nicht den Admin-Pfad in `_db_role_admin_check` direkt. Mutmut findet
    hier Logic-Survivors (z.B. ``actual_super is None`` → ``is not None``
    Negation), die durch fehlende Branch-Tests nicht gekillt werden.
    """

    def test_unknown_when_admin_role_not_in_pg_roles(self):
        """``actual_super is None`` → UNKNOWN (Admin-Rolle nicht angelegt)."""
        admin = _AdminRoleCheck(role="ghost_admin", actual_super=None, actual_bypassrls=None)
        result = compliance._db_role_admin_check(admin)
        assert result.status == ComplianceStatus.UNKNOWN
        assert result.key == "db_role_admin"
        assert "ghost_admin" in result.message

    def test_ok_when_admin_role_correct(self):
        """NOSUPERUSER + BYPASSRLS → OK."""
        admin = _AdminRoleCheck(role="anlaufstelle_admin", actual_super=False, actual_bypassrls=True)
        result = compliance._db_role_admin_check(admin)
        assert result.status == ComplianceStatus.OK
        assert "anlaufstelle_admin" in (result.detail or "")
        assert "rolsuper=False" in (result.detail or "")
        assert "rolbypassrls=True" in (result.detail or "")

    def test_critical_when_admin_is_superuser(self):
        """Admin mit ``rolsuper=True`` → CRITICAL."""
        admin = _AdminRoleCheck(role="bad_admin", actual_super=True, actual_bypassrls=True)
        result = compliance._db_role_admin_check(admin)
        assert result.status == ComplianceStatus.CRITICAL
        assert "actual_super=True" in (result.detail or "")

    def test_critical_when_admin_lacks_bypassrls(self):
        """Admin mit ``rolbypassrls=False`` → CRITICAL."""
        admin = _AdminRoleCheck(role="weak_admin", actual_super=False, actual_bypassrls=False)
        result = compliance._db_role_admin_check(admin)
        assert result.status == ComplianceStatus.CRITICAL
        assert "actual_bypassrls=False" in (result.detail or "")


class TestClamavSignatureBoundary:
    """Branch-Grenzen für `_clamav_checks` Signatur-Alter (Refs Welle 9 #942).

    Bestehende Tests in test_compliance_service.py decken `ping=True`+`age=3`
    (OK) und `age=30` (WARNING) ab, lassen aber die exakte Schwelle (`age=7`
    OK, `age=8` WARNING) und den `age_days is None`-Branch (UNKNOWN trotz
    vorhandener sig) offen.
    """

    def test_age_seven_days_is_still_ok(self):
        """Exakte Schwelle ``age <= 7`` → OK."""
        with (
            patch("core.services.file_vault.virus_scan.ping", return_value=True),
            patch(
                "core.services.file_vault.virus_scan.signature_info",
                return_value={"version": "1.4.0", "signature_date": None, "age_days": 7},
            ),
        ):
            checks = compliance._clamav_checks()
        sig = next(c for c in checks if c.key == "clamav_signature")
        assert sig.status == ComplianceStatus.OK

    def test_age_eight_days_is_warning(self):
        """Schwelle ``age > 7`` → WARNING (Boundary +1)."""
        with (
            patch("core.services.file_vault.virus_scan.ping", return_value=True),
            patch(
                "core.services.file_vault.virus_scan.signature_info",
                return_value={"version": "1.4.0", "signature_date": None, "age_days": 8},
            ),
        ):
            checks = compliance._clamav_checks()
        sig = next(c for c in checks if c.key == "clamav_signature")
        assert sig.status == ComplianceStatus.WARNING

    def test_age_days_is_none_yields_unknown(self):
        """sig vorhanden, aber ``age_days=None`` → UNKNOWN."""
        with (
            patch("core.services.file_vault.virus_scan.ping", return_value=True),
            patch(
                "core.services.file_vault.virus_scan.signature_info",
                return_value={"version": "1.4.0", "signature_date": None, "age_days": None},
            ),
        ):
            checks = compliance._clamav_checks()
        sig = next(c for c in checks if c.key == "clamav_signature")
        assert sig.status == ComplianceStatus.UNKNOWN
        assert "1.4.0" in (sig.detail or "")

    def test_version_unknown_when_missing(self):
        """``sig.get("version")`` returns None → Default-String ``"unbekannt"``."""
        with (
            patch("core.services.file_vault.virus_scan.ping", return_value=True),
            patch(
                "core.services.file_vault.virus_scan.signature_info",
                return_value={"version": None, "signature_date": None, "age_days": None},
            ),
        ):
            checks = compliance._clamav_checks()
        sig = next(c for c in checks if c.key == "clamav_signature")
        assert "unbekannt" in (sig.detail or "")


class TestBackupBoundary:
    """Branch-Grenzen für `_backup_checks` Age-Hours (Refs Welle 9 #942).

    Schwellen: ``<= 24`` OK, ``<= 72`` WARNING, ``> 72`` CRITICAL.
    """

    def test_age_24h_is_ok(self):
        """Exakte Schwelle ``age <= 24`` → OK."""
        with patch(
            "core.services.system_health.last_backup_info",
            return_value={"path": "/var/backups/x.sql", "mtime": None, "age_hours": 24.0, "is_stale": False},
        ):
            checks = compliance._backup_checks()
        assert checks[0].status == ComplianceStatus.OK

    def test_age_25h_is_warning(self):
        """``age > 24`` → WARNING (Boundary +1)."""
        with patch(
            "core.services.system_health.last_backup_info",
            return_value={"path": "/var/backups/x.sql", "mtime": None, "age_hours": 25.0, "is_stale": True},
        ):
            checks = compliance._backup_checks()
        assert checks[0].status == ComplianceStatus.WARNING

    def test_age_72h_is_still_warning(self):
        """Exakte Schwelle ``age <= 72`` → WARNING."""
        with patch(
            "core.services.system_health.last_backup_info",
            return_value={"path": "/var/backups/x.sql", "mtime": None, "age_hours": 72.0, "is_stale": True},
        ):
            checks = compliance._backup_checks()
        assert checks[0].status == ComplianceStatus.WARNING

    def test_age_73h_is_critical(self):
        """``age > 72`` → CRITICAL (Boundary +1)."""
        with patch(
            "core.services.system_health.last_backup_info",
            return_value={"path": "/var/backups/x.sql", "mtime": None, "age_hours": 73.0, "is_stale": True},
        ):
            checks = compliance._backup_checks()
        assert checks[0].status == ComplianceStatus.CRITICAL


class TestRestoreBoundary:
    """Branch-Grenzen für `_restore_checks` Age-Days (Refs Welle 9 #942).

    Schwellen: ``<= 90`` OK, ``<= 180`` WARNING, ``> 180`` CRITICAL.
    Da AuditLog-Timestamps via Migration-0024 immutable sind, patchen wir
    ``compliance.datetime`` für Boundary-präzise Tests.
    """

    @pytest.mark.django_db
    def test_age_90d_is_ok(self):
        AuditLog.objects.filter(action=AuditLog.Action.RESTORE_VERIFIED).delete()
        entry = AuditLog.objects.create(
            action=AuditLog.Action.RESTORE_VERIFIED,
            facility=None,
            target_type="RestoreVerification",
            detail={"note": "test"},
        )
        # 90 Tage nach Erstellung — `age_days <= 90` greift → OK
        fixed_now = entry.timestamp + timedelta(days=90)
        with _patch_compliance_now(fixed_now):
            checks = compliance._restore_checks()
        assert checks[0].status == ComplianceStatus.OK

    @pytest.mark.django_db
    def test_age_91d_is_warning(self):
        AuditLog.objects.filter(action=AuditLog.Action.RESTORE_VERIFIED).delete()
        entry = AuditLog.objects.create(
            action=AuditLog.Action.RESTORE_VERIFIED,
            facility=None,
            target_type="RestoreVerification",
            detail={"note": "test"},
        )
        fixed_now = entry.timestamp + timedelta(days=91)
        with _patch_compliance_now(fixed_now):
            checks = compliance._restore_checks()
        assert checks[0].status == ComplianceStatus.WARNING

    @pytest.mark.django_db
    def test_age_180d_is_still_warning(self):
        AuditLog.objects.filter(action=AuditLog.Action.RESTORE_VERIFIED).delete()
        entry = AuditLog.objects.create(
            action=AuditLog.Action.RESTORE_VERIFIED,
            facility=None,
            target_type="RestoreVerification",
            detail={"note": "test"},
        )
        fixed_now = entry.timestamp + timedelta(days=180)
        with _patch_compliance_now(fixed_now):
            checks = compliance._restore_checks()
        assert checks[0].status == ComplianceStatus.WARNING

    @pytest.mark.django_db
    def test_age_181d_is_critical(self):
        AuditLog.objects.filter(action=AuditLog.Action.RESTORE_VERIFIED).delete()
        entry = AuditLog.objects.create(
            action=AuditLog.Action.RESTORE_VERIFIED,
            facility=None,
            target_type="RestoreVerification",
            detail={"note": "test"},
        )
        fixed_now = entry.timestamp + timedelta(days=181)
        with _patch_compliance_now(fixed_now):
            checks = compliance._restore_checks()
        assert checks[0].status == ComplianceStatus.CRITICAL
