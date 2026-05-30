"""Follow-Up-Tests für Mutation-Survivors in ``core.services.compliance``.

Refs Welle 7 (#930). Ziel: Mutationen an den Branch-Grenzen von
``_audit_event_checks``, ``_mfa_checks``, ``_retention_checks`` killen.

Die Funktionen branchen auf festen Schwellwerten (Count/Age-Days/Percent).
Mutmut mutiert typischerweise ``<=``/`<`/``>``/``>=`` und Konstanten;
um diese Mutationen zu fangen, decken die Tests die exakten Boundary-
Werte ab (z.B. 7/8 Tage, 5/6 Events, 80/100 %).

AuditLog-Einträge sind per Datenbank-Trigger (Migration 0024) immutable —
``UPDATE timestamp`` ist nicht möglich. Stattdessen patchen wir
``core.services.compliance.datetime`` so, dass die Check-Funktion eine
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


class _FakeDatetime:
    """Mock-Hülle: ``.now(tz=...)`` liefert ``fixed``, alles andere delegiert
    an das echte ``datetime``."""

    def __init__(self, fixed: datetime):
        self._fixed = fixed

    def now(self, tz=None):  # noqa: D401, ARG002
        return self._fixed


def _patch_compliance_now(fixed: datetime):
    """Kontext-Manager: ``core.services.compliance.datetime.now`` → ``fixed``."""
    return patch.object(compliance, "datetime", _FakeDatetime(fixed))


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
