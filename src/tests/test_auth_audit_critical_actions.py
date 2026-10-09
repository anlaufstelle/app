"""Authentication anomalies affect the public compliance summary."""

import pytest

from core.models import AuditLog
from core.services.audit import audit_event
from core.services.compliance import ComplianceStatus, aggregate_checks

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("action", ["webauthn_failed", "mfa_backup_regenerate_denied", "mfa_verified", "rate_limited"])
def test_compliance_distinguishes_auth_anomalies_from_normal_operations(facility, staff_user, action):
    audit_event(action, user=staff_user, facility=facility)
    check = next(check for check in aggregate_checks() if check.key == "critical_audit_events_24h")
    expected = (
        ComplianceStatus.WARNING
        if action in ("webauthn_failed", "mfa_backup_regenerate_denied")
        else ComplianceStatus.OK
    )
    assert check.status == expected


def test_new_action_choices_round_trip(facility, staff_user):
    for action in ("mfa_verified", "mfa_backup_regenerate_denied", "rate_limited"):
        entry = audit_event(action, user=staff_user, facility=facility)
        entry.refresh_from_db()
        assert entry.action == action
        assert entry.action in AuditLog.Action.values
        assert entry.entry_hash
