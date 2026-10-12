"""System login detection, account locks and audit metadata contracts.

Source: mutation delta of the release-0.99.7 checkpoint, 15 mutants.
Groups: eligible failure rows (4), immediate account scope and locks (4),
system finding identity (1), alarm metadata (3), silent immediate alarm (2),
empty burst result (1). Public detector/recorder APIs use real audit rows.
SQL observation checks the account lock's scope without query-count budgets.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.utils import timezone

from core.models import AuditLog, User
from core.services.compliance import breach_detection
from core.services.security.login_lockout import LOCKOUT_THRESHOLD, LOCKOUT_WINDOW

pytestmark = pytest.mark.django_db


def _failures(user, count, *, facility=None, timestamp=None):
    for _ in range(count):
        AuditLog.objects.create(
            action=AuditLog.Action.LOGIN_FAILED,
            user=user,
            facility=facility,
            timestamp=timestamp or timezone.now(),
        )


def _alarms():
    return AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION)


@pytest.mark.parametrize("excluded", ["old", "facility-event", "assigned-admin", "other-role"])
def test_system_scan_excludes_ineligible_failures(super_admin_user, facility, excluded):
    """Only recent global failures of facility-less system admins count.

    x_detect_super_admin_login_bursts__mutmut_10,
    x_detect_super_admin_login_bursts__mutmut_7,
    x_detect_super_admin_login_bursts__mutmut_8,
    x_detect_super_admin_login_bursts__mutmut_9.
    """
    # Leave fixture audit rows behind the replayed timeline; append old rows first.
    now = timezone.now() + LOCKOUT_WINDOW + timedelta(minutes=5)
    if excluded in {"old", "facility-event"}:
        _failures(
            super_admin_user,
            1,
            facility=facility if excluded == "facility-event" else None,
            timestamp=now - LOCKOUT_WINDOW - timedelta(seconds=1) if excluded == "old" else now,
        )
        _failures(super_admin_user, LOCKOUT_THRESHOLD - 1, timestamp=now)
    else:
        user = User.objects.create_user(
            username="excluded-alarm-account",
            role=User.Role.SUPER_ADMIN if excluded == "assigned-admin" else User.Role.STAFF,
            facility=facility if excluded == "assigned-admin" else None,
        )
        _failures(user, LOCKOUT_THRESHOLD, timestamp=now)

    with patch.object(breach_detection.timezone, "now", return_value=now):
        assert breach_detection.detect_super_admin_login_bursts() == []


def test_immediate_alarm_rejects_a_facility_less_non_admin():
    """A facility-less non-admin gets no alarm result or account row lock.

    x_record_super_admin_login_alarm__mutmut_1.
    """
    user = User.objects.create_user(username="non-system-alarm-account", role=User.Role.STAFF)
    locks = []

    def observe(execute, sql, params, many, context):
        if '"core_user"' in sql and "FOR " in sql:
            locks.append(sql)
        return execute(sql, params, many, context)

    with connection.execute_wrapper(observe):
        assert breach_detection.record_super_admin_login_alarm(user) is None
    assert locks == []


@pytest.mark.postgres
def test_immediate_alarm_locks_only_the_requested_account():
    """The row lock targets the requested ID even when another account sorts first.

    x_record_super_admin_login_alarm__mutmut_6.
    """
    other = User.objects.create_user(username="first-alarm-account", role=User.Role.SUPER_ADMIN)
    target = User.objects.create_user(username="second-alarm-account", role=User.Role.SUPER_ADMIN)
    assert User.objects.filter(facility=None).order_by("pk").first() == other
    locks = []

    def observe(execute, sql, params, many, context):
        if '"core_user"' in sql and ("FOR UPDATE" in sql or "FOR NO KEY UPDATE" in sql):
            locks.append((sql, params))
        return execute(sql, params, many, context)

    with connection.execute_wrapper(observe):
        assert breach_detection.record_super_admin_login_alarm(target) == 0
    assert locks
    for sql, params in locks:
        assert '"core_user"."id" = %s' in sql
        assert '"core_user"."facility_id" IS NULL' in sql
        assert target.pk in params
        assert other.pk not in params


def test_immediate_alarm_rechecks_facility_on_the_stored_account(super_admin_user, facility):
    """A stale facility-less instance cannot alarm after its stored account is assigned.

    x_record_super_admin_login_alarm__mutmut_7.
    """
    User.objects.filter(pk=super_admin_user.pk).update(facility=facility)
    assert super_admin_user.facility_id is None

    assert breach_detection.record_super_admin_login_alarm(super_admin_user) is None
    assert not _alarms().exists()


def test_immediate_alarm_does_not_scan_another_accounts_burst(super_admin_user):
    """A different account's burst neither creates an alarm nor changes this return value.

    x_record_super_admin_login_alarm__mutmut_12,
    x_record_super_admin_login_alarm__mutmut_21.
    """
    other = User.objects.create_user(username="other-burst-account", role=User.Role.SUPER_ADMIN)
    _failures(other, LOCKOUT_THRESHOLD)
    _failures(super_admin_user, LOCKOUT_THRESHOLD - 1)

    assert breach_detection.record_super_admin_login_alarm(super_admin_user) == 0
    assert not _alarms().exists()


def test_system_finding_does_not_associate_a_facility_bound_account(staff_user):
    """A global finding naming a facility account records no account association.

    x_record_system_finding__mutmut_6.
    """
    finding = {
        "kind": "super_admin_bruteforce",
        "user_id": staff_user.pk,
        "count": LOCKOUT_THRESHOLD,
        "threshold": LOCKOUT_THRESHOLD,
        "window_minutes": int(LOCKOUT_WINDOW.total_seconds() / 60),
    }

    entry = breach_detection.record_system_finding(finding, notify=False)

    assert entry is not None
    assert entry.user_id is None
    assert entry.facility_id is None
    assert _alarms().get().user_id is None


def test_immediate_alarm_retains_detection_metadata_without_a_webhook(super_admin_user, settings):
    """The detected and stored alarm retain threshold/window and never notify during login.

    x_detect_super_admin_login_bursts__mutmut_19,
    x_detect_super_admin_login_bursts__mutmut_20,
    x_detect_super_admin_login_bursts__mutmut_32,
    x_record_super_admin_login_alarm__mutmut_16,
    x_record_super_admin_login_alarm__mutmut_17.
    """
    _failures(super_admin_user, LOCKOUT_THRESHOLD)
    expected_minutes = int(LOCKOUT_WINDOW.total_seconds() / 60)
    finding = breach_detection.detect_super_admin_login_bursts(super_admin_user)[0]
    assert finding["threshold"] == LOCKOUT_THRESHOLD
    assert finding["window_minutes"] == expected_minutes
    settings.BREACH_NOTIFICATION_WEBHOOK_URL = "https://notifications.example/hook"

    with patch.object(breach_detection, "_post_webhook") as send:
        assert breach_detection.record_super_admin_login_alarm(super_admin_user) == LOCKOUT_THRESHOLD
        send.assert_not_called()

    alarm = _alarms().get()
    assert alarm.user == super_admin_user
    assert alarm.detail["threshold"] == LOCKOUT_THRESHOLD
    assert alarm.detail["window_minutes"] == expected_minutes
