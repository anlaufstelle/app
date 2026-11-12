"""Replayed audit tails cannot conceal a missing protected row during pruning."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import OperationalError, connections, transaction
from django.utils import timezone

from core.models import AuditLog
from core.retention import audit_pruning
from core.retention.audit_pruning import prune_auditlog
from core.services.audit.chain import verify_chain
from core.services.audit.hwm import HighWaterMarkStore
from core.services.system import bypass_replication_triggers

PERIOD = SimpleNamespace(auditlog_retention_months=24)


pytestmark = pytest.mark.postgres


def _chain(facility, tmp_path):
    old = timezone.now() - timedelta(days=1200)
    actions = (AuditLog.Action.LOGIN, AuditLog.Action.SECURITY_VIOLATION, AuditLog.Action.LOGOUT)
    rows = [
        AuditLog.objects.create(facility=facility, action=a, timestamp=old + timedelta(seconds=i))
        for i, a in enumerate(actions)
    ]
    for row in rows:
        row.refresh_from_db()
    result = verify_chain(facility)
    assert result.ok
    store = HighWaterMarkStore(path=str(tmp_path / "anchor.json"))
    store.record(facility.pk, result.rows_checked, result.last_entry_hash, result.last_entry_at)
    store.save()
    with transaction.atomic(), bypass_replication_triggers():
        AuditLog.objects.filter(pk__in=[rows[1].pk, rows[2].pk]).delete()
    assert not verify_chain(facility, hwm=HighWaterMarkStore.load(store.path)).ok
    return rows, store.path


def _replay(row):
    fields = AuditLog._meta.concrete_fields
    other = connections.create_connection("default")
    try:
        values = [f.get_db_prep_save(getattr(row, f.attname), other) for f in fields]
        with other.cursor() as cursor:
            cursor.execute("SET lock_timeout = '1s'")
            cursor.execute("SET statement_timeout = '3s'")
            cursor.execute(
                "INSERT INTO core_auditlog ({}) VALUES ({})".format(
                    ", ".join(other.ops.quote_name(f.column) for f in fields), ", ".join(["%s"] * len(fields))
                ),
                values,
            )
    finally:
        other.close()


@pytest.mark.django_db(transaction=True)
def test_replay_before_the_link_check_aborts(facility, tmp_path, monkeypatch):
    rows, path = _chain(facility, tmp_path)
    original = audit_pruning._check_expired_links

    def replay_then_check(*args, **kwargs):
        _replay(rows[2])
        return original(*args, **kwargs)

    monkeypatch.setattr(audit_pruning, "_check_expired_links", replay_then_check)
    result = prune_auditlog(facility, PERIOD)
    assert result["count"] == 0 and "integrity_break" in result, result
    assert AuditLog.objects.filter(pk=rows[0].pk).exists()
    after = verify_chain(facility, hwm=HighWaterMarkStore.load(path))
    assert not after.ok, after


@pytest.mark.django_db(transaction=True)
def test_no_insert_gets_in_after_the_deletion_started(facility, tmp_path, monkeypatch):
    """After the trigger bypass the table lock blocks every INSERT until commit."""
    rows, path = _chain(facility, tmp_path)
    original = audit_pruning.audit_event
    outcomes = []

    def replay_then_checkpoint(*args, **kwargs):
        try:
            _replay(rows[2])
            outcomes.append("inserted")
        except OperationalError as exc:
            outcomes.append(type(exc.__cause__).__name__)
        return original(*args, **kwargs)

    monkeypatch.setattr(audit_pruning, "audit_event", replay_then_checkpoint)
    result = prune_auditlog(facility, PERIOD)
    assert outcomes == ["LockNotAvailable"], outcomes
    assert result == {"count": 1}, result
    assert not AuditLog.objects.filter(pk=rows[2].pk).exists()
    after = verify_chain(facility, hwm=HighWaterMarkStore.load(path))
    assert not after.ok, after
