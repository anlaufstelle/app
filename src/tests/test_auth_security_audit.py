"""Authentication alarm contracts for known system administrators."""

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.test import override_settings

from core.models import AuditLog, User
from core.services.audit import audit_event
from core.services.security import is_locked

pytestmark = pytest.mark.django_db


def _system_user(username):
    return User.objects.create_user(username=username, password="valid-password", role=User.Role.SUPER_ADMIN)


@override_settings(RATELIMIT_ENABLE=False)
def test_failed_login_threshold_records_one_alarm_without_locking(client, super_admin_user):
    for attempt in range(12):
        response = client.post("/login/", {"username": super_admin_user.username, "password": "wrong"})
        assert response.status_code in (200, 429)
        alarms = AuditLog.objects.filter(user=super_admin_user, action=AuditLog.Action.SECURITY_VIOLATION)
        assert alarms.count() == (0 if attempt < 9 else 1)
    alarm = alarms.get()
    assert alarm.facility_id is None
    assert alarm.detail["reason"] == "super_admin_bruteforce"
    assert alarm.detail["count"] == 10
    assert is_locked(super_admin_user) is False
    assert client.post("/login/", {"username": super_admin_user.username, "password": "testpass123"}).status_code == 302


def test_scan_detects_each_known_facility_less_account_and_deduplicates(super_admin_user):
    other = _system_user("other-system-admin")
    for user in (super_admin_user, other):
        for _ in range(10):
            audit_event(AuditLog.Action.LOGIN_FAILED, user=user, facility=None)
    output = StringIO()
    with patch("core.management.commands.detect_breaches._has_rls_bypass_context", return_value=True):
        call_command("detect_breaches", stdout=output)
        call_command("detect_breaches", stdout=output)
    alarms = AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION, detail__reason="super_admin_bruteforce")
    assert set(alarms.values_list("user_id", flat=True)) == {super_admin_user.pk, other.pk}
    assert alarms.count() == 2
    assert "super_admin_bruteforce" in output.getvalue()


@override_settings(RATELIMIT_ENABLE=False)
def test_super_admin_backoff_is_bounded_retry_advice_and_never_sleeps(client, super_admin_user):
    with patch("time.sleep", side_effect=AssertionError("Workers must not sleep")):
        for attempt in range(15):
            response = client.post("/login/", {"username": super_admin_user.username, "password": "wrong"})
            if attempt >= 9:
                assert response.status_code == 429
                assert int(response["Retry-After"]) == min(2 ** (attempt - 9), 8)
        assert (
            client.post("/login/", {"username": super_admin_user.username, "password": "testpass123"}).status_code
            == 302
        )


@pytest.mark.django_db(transaction=True)
def test_concurrent_failed_logins_do_not_deadlock_alarm_and_audit_foreign_key(rf, super_admin_user):
    """Both authentication requests must finish while their audit writes overlap."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from django.contrib.auth import authenticate
    from django.db import connection, connections

    from core.services.compliance import breach_detection

    for _ in range(9):
        audit_event(AuditLog.Action.LOGIN_FAILED, user=super_admin_user, facility=None)
    alarm_started = Event()
    second_insert_started = Event()
    original_record = breach_detection.audit_security_violation

    def overlap_alarm(*args, **kwargs):
        alarm_started.set()
        assert second_insert_started.wait(5)
        return original_record(*args, **kwargs)

    def signal_insert(execute, sql, params, many, context):
        if sql.startswith('INSERT INTO "core_auditlog"'):
            second_insert_started.set()
        return execute(sql, params, many, context)

    def failed_login(*, second=False):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '3s'")
            http_request = rf.post("/login/")
            if second:
                with connection.execute_wrapper(signal_insert):
                    return authenticate(http_request, username=super_admin_user.username, password="wrong")
            return authenticate(http_request, username=super_admin_user.username, password="wrong")
        finally:
            connections.close_all()

    with patch.object(breach_detection, "audit_security_violation", side_effect=overlap_alarm):
        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(failed_login)
            assert alarm_started.wait(5)
            second = workers.submit(failed_login, second=True)
            assert first.result(timeout=8) is None
            assert second.result(timeout=8) is None
    assert AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION, user=super_admin_user).count() == 1
    assert AuditLog.objects.filter(action=AuditLog.Action.LOGIN_FAILED, user=super_admin_user).count() == 11
