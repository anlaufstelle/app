"""Pins for anonymous request defaults and explicit audit context.

Source: mutation delta of the release-0.99.9 checkpoint, 3 mutants.
Groups: pre-authentication session context (1), missing authentication attribute (2).
"""

from types import SimpleNamespace

import pytest
from django.db import connection

from core.models import AuditLog
from core.services.audit import log_audit_event

pytestmark = pytest.mark.django_db


def test_override_synchronizes_context_before_insert(facility, staff_user, monkeypatch):
    """An explicit pre-authentication override sets context before writing: x_log_audit_event__mutmut_43."""
    request = SimpleNamespace(user=None, META={})
    original_create = AuditLog.objects.create

    def create_in_context(**values):
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('app.current_facility_id', true)")
            assert cursor.fetchone()[0] == str(facility.pk)
        return original_create(**values)

    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.current_facility_id', '', false)")
    monkeypatch.setattr(AuditLog.objects, "create", create_in_context)
    entry = log_audit_event(request, AuditLog.Action.LOGIN_FAILED, user=staff_user, facility=facility)
    entry.refresh_from_db()
    assert entry.user_id == staff_user.pk
    assert entry.facility_id == facility.pk


def test_missing_authentication_attribute_records_anonymous_actor(facility):
    """A request user without authentication metadata is anonymous.

    x_log_audit_event__mutmut_20, x_log_audit_event__mutmut_23.
    """
    request = SimpleNamespace(user=SimpleNamespace(), current_facility=facility, META={})
    entry = log_audit_event(request, AuditLog.Action.LOGIN_FAILED)
    entry.refresh_from_db()
    assert entry.user_id is None
    assert entry.facility_id == facility.pk
