"""Audit pruning decides on the period locked before its target rows."""

from datetime import timedelta

import pytest
from django.db import OperationalError, connection, connections
from django.utils import timezone

from core.models import AuditLog, Settings
from core.retention import audit_pruning
from core.services.audit.chain import verify_chain

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.postgres]


def _old_row(facility, now):
    return AuditLog.objects.create(facility=facility, action=AuditLog.Action.LOGIN, timestamp=now - timedelta(days=900))


@pytest.mark.parametrize(
    ("initial", "current", "expected"),
    [(24, 36, 0), (24, 0, 0), (36, 24, 1), (0, 24, 1)],
    ids=["extended", "disabled", "shortened", "enabled"],
)
def test_prune_uses_the_period_saved_after_the_run_started(facility, settings_obj, initial, current, expected):
    settings_obj.auditlog_retention_months = initial
    settings_obj.save()
    now = timezone.now()
    row = _old_row(facility, now)
    # The command retains this instance while earlier retention steps run.
    Settings.objects.filter(pk=settings_obj.pk).update(auditlog_retention_months=current)
    assert settings_obj.auditlog_retention_months == initial

    result = audit_pruning.prune_auditlog(facility, settings_obj, now=now)

    assert result == {"count": expected}
    assert AuditLog.objects.filter(pk=row.pk).exists() == (expected == 0)
    checkpoints = AuditLog.objects.filter(facility=facility, action=AuditLog.Action.AUDIT_PRUNE_CHECKPOINT)
    assert checkpoints.count() == expected
    if expected:
        assert checkpoints.get().detail["cutoff"] == audit_pruning._months_before(now, current).isoformat()
    assert verify_chain(facility).ok


def _assert_settings_locked(settings_obj):
    other = connections.create_connection("default")
    try:
        with other.cursor() as cursor:
            cursor.execute("SET statement_timeout = '3s'")
            with pytest.raises(OperationalError) as caught:
                cursor.execute(
                    "SELECT facility_id FROM core_settings WHERE facility_id = %s FOR UPDATE NOWAIT",
                    [settings_obj.facility_id],
                )
            assert type(caught.value.__cause__).__name__ == "LockNotAvailable"
    finally:
        other.close()


def test_settings_lock_precedes_targets_and_lasts_through_checkpoint(facility, settings_obj, monkeypatch):
    now = timezone.now()
    _old_row(facility, now)
    original_targets = audit_pruning._lock_audit_targets
    original_checkpoint = audit_pruning.audit_event
    stages = []

    def lock_targets(*args, **kwargs):
        _assert_settings_locked(settings_obj)
        stages.append("targets")
        return original_targets(*args, **kwargs)

    def checkpoint(*args, **kwargs):
        _assert_settings_locked(settings_obj)
        stages.append("checkpoint")
        return original_checkpoint(*args, **kwargs)

    def check_gate_before_settings(execute, sql, params, many, context):
        if '"core_settings"' in sql and "FOR UPDATE" in sql:
            other = connections.create_connection("default")
            try:
                with other.cursor() as cursor:
                    cursor.execute("SELECT pg_try_advisory_lock(2525, hashtext(%s))", [str(facility.pk)])
                    assert cursor.fetchone() == (False,)
            finally:
                other.close()
            stages.append("gate")
        return execute(sql, params, many, context)

    monkeypatch.setattr(audit_pruning, "_lock_audit_targets", lock_targets)
    monkeypatch.setattr(audit_pruning, "audit_event", checkpoint)
    with connection.execute_wrapper(check_gate_before_settings):
        assert audit_pruning.prune_auditlog(facility, settings_obj, now=now) == {"count": 1}
    assert stages == ["gate", "targets", "checkpoint"]


def test_dry_run_takes_no_locks(facility, settings_obj):
    now = timezone.now()
    row = _old_row(facility, now)

    def reject_locks(execute, sql, params, many, context):
        assert "FOR UPDATE" not in sql.upper()
        assert "PG_ADVISORY" not in sql.upper()
        return execute(sql, params, many, context)

    with connection.execute_wrapper(reject_locks):
        assert audit_pruning.prune_auditlog(facility, settings_obj, now=now, dry_run=True) == {"count": 1}
    assert AuditLog.objects.filter(pk=row.pk).exists()
    assert verify_chain(facility).ok
