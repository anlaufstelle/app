"""Chain verification and audit pruning with bounded memory (Refs #2717).

``verify_chain`` used to load every row of a chain into a list, and
``prune_auditlog`` built Python sets of all deleted hashes and all survivor
predecessors. Memory grew with the chain length; the cron container is capped.

Covered here:

- characterisation: prune boundaries and the verify result of a chain with
  several gaps and an exempt survivor stay exactly as before;
- ``verify_chain`` keeps only a bounded number of row objects alive at once,
  independent of the chain length.
"""

from __future__ import annotations

import weakref
from datetime import timedelta

import pytest
from django.db import connection, transaction
from django.utils import timezone

from core.models import AuditLog
from core.retention.audit_pruning import prune_auditlog
from core.services.audit.chain import verify_chain
from core.services.system import bypass_replication_triggers

pytestmark = pytest.mark.django_db


def _require_postgres():
    assert connection.vendor == "postgresql", "AuditLog chain/trigger/RLS requires PostgreSQL"


def _row(facility, action, days_ago, now):
    row = AuditLog.objects.create(facility=facility, action=action, timestamp=now - timedelta(days=days_ago))
    row.refresh_from_db()
    return row


def _gapped_chain(facility, settings_obj, now):
    settings_obj.auditlog_retention_months = 12
    settings_obj.save()
    rows = {
        "old1": _row(facility, AuditLog.Action.LOGIN, 600, now),
        "old2": _row(facility, AuditLog.Action.LOGOUT, 599, now),
        "exempt": _row(facility, AuditLog.Action.SECURITY_VIOLATION, 598, now),
        "old3": _row(facility, AuditLog.Action.LOGIN, 597, now),
        "old4": _row(facility, AuditLog.Action.LOGOUT, 596, now),
        "young1": _row(facility, AuditLog.Action.LOGIN, 3, now),
        "young2": _row(facility, AuditLog.Action.LOGOUT, 2, now),
    }
    return rows


class TestCharacterisation:
    def setup_method(self):
        _require_postgres()

    def test_prune_records_exactly_the_boundaries_survivors_point_to(self, facility, settings_obj):
        now = timezone.now()
        rows = _gapped_chain(facility, settings_obj, now)

        result = prune_auditlog(facility, settings_obj, now=now)

        assert result == {"count": 4}
        checkpoint = AuditLog.objects.get(facility=facility, action=AuditLog.Action.AUDIT_PRUNE_CHECKPOINT)
        assert checkpoint.detail["pruned_count"] == 4
        assert checkpoint.detail["boundary_hash"] == rows["old4"].entry_hash
        assert checkpoint.detail["boundary_hashes"] == sorted([rows["old2"].entry_hash, rows["old4"].entry_hash])

    def test_prune_with_nothing_to_delete_writes_no_checkpoint(self, facility, settings_obj):
        now = timezone.now()
        settings_obj.auditlog_retention_months = 12
        settings_obj.save()
        _row(facility, AuditLog.Action.LOGIN, 2, now)

        assert prune_auditlog(facility, settings_obj, now=now) == {"count": 0}
        assert not AuditLog.objects.filter(facility=facility, action=AuditLog.Action.AUDIT_PRUNE_CHECKPOINT).exists()

    def test_verify_result_after_pruning(self, facility, settings_obj):
        now = timezone.now()
        _gapped_chain(facility, settings_obj, now)
        prune_auditlog(facility, settings_obj, now=now)
        checkpoint = AuditLog.objects.get(facility=facility, action=AuditLog.Action.AUDIT_PRUNE_CHECKPOINT)
        survivors = AuditLog.objects.filter(facility=facility).count()

        result = verify_chain(facility)

        assert result.ok, result
        assert result.rows_checked == survivors
        assert result.last_entry_hash == checkpoint.entry_hash
        assert result.last_entry_at == checkpoint.timestamp

    def test_verify_result_at_a_deleted_middle_row(self, facility, settings_obj):
        now = timezone.now()
        rows = _gapped_chain(facility, settings_obj, now)
        with transaction.atomic(), bypass_replication_triggers():
            AuditLog.objects.filter(pk=rows["old3"].pk).delete()

        result = verify_chain(facility)

        assert not result.ok
        assert result.first_break_id == str(rows["old4"].pk)
        assert result.reason == "prev_hash linkage break (row deleted/inserted)"
        assert result.rows_checked == 4


class TestVerifyChainMemory:
    def setup_method(self):
        _require_postgres()

    def test_verify_keeps_a_bounded_number_of_rows_alive(self, facility, monkeypatch):
        now = timezone.now()
        for index in range(300):
            AuditLog.objects.create(
                facility=facility, action=AuditLog.Action.LOGIN, timestamp=now - timedelta(seconds=300 - index)
            )
        alive: weakref.WeakSet = weakref.WeakSet()
        peak = 0
        original = AuditLog.from_db.__func__

        def tracking_from_db(cls, db, field_names, values, **kwargs):
            nonlocal peak
            instance = original(cls, db, field_names, values, **kwargs)
            alive.add(instance)
            peak = max(peak, len(alive))
            return instance

        monkeypatch.setattr(AuditLog, "from_db", classmethod(tracking_from_db))

        result = verify_chain(facility)

        assert result.ok, result
        assert result.rows_checked >= 300
        assert peak < 50, f"{peak} AuditLog rows alive at once"
