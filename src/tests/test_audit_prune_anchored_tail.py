"""Pruning may remove the externally anchored tail of a dormant chain (Refs #2717).

A facility that writes no audit row for longer than its retention period loses
every row to ``prune_auditlog`` -- including the newest one, whose hash the
external high-water mark anchors. The prune records that hash as a boundary in
its authenticated checkpoint. ``verify_audit_chain`` used to report the missing
anchor as tail truncation every night from then on.

The anchor may only count as legitimately pruned when the checkpoint names it
AND the anchored row was older than the checkpoint's cutoff according to the
signed anchor file. The database timestamp is not enough: an owner-level role
can backdate the newest rows, let the regular prune delete them and would
otherwise truncate the tail unnoticed.

Covered here:

- dormant chain, anchored tail pruned -> green, anchor moves to the checkpoint;
- backdated tail removed by a regular prune -> still tail truncation;
- checkpoint deleted after the prune -> still tail truncation;
- anchor file without the anchored row's time -> still tail truncation.
"""

from __future__ import annotations

import io
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.db import connection, transaction
from django.utils import timezone

from core.models import AuditLog
from core.retention.audit_pruning import prune_auditlog
from core.services.audit import audit_event
from core.services.audit.hwm import HighWaterMarkStore
from core.services.system import bypass_replication_triggers

pytestmark = pytest.mark.django_db

TRUNCATION = "tail truncation / whole-chain wipe"


def _require_postgres():
    assert connection.vendor == "postgresql", "AuditLog chain/trigger/RLS requires PostgreSQL"


def _verify():
    """Run the command; return ``(ok, stderr)``."""
    err = io.StringIO()
    try:
        call_command("verify_audit_chain", stdout=io.StringIO(), stderr=err)
    except SystemExit:
        return False, err.getvalue()
    return True, err.getvalue()


def _dormant_chain(facility, settings_obj):
    settings_obj.auditlog_retention_months = 24
    settings_obj.save()
    audit_event(AuditLog.Action.LOGIN, user=None, facility=facility)
    tail = audit_event(AuditLog.Action.LOGOUT, user=None, facility=facility)
    tail.refresh_from_db()
    return tail


def _anchor(settings, tmp_path, facility, tail):
    settings.AUDIT_HWM_PATH = str(tmp_path / "hwm.json")
    ok, err = _verify()
    assert ok, err
    assert HighWaterMarkStore.load(settings.AUDIT_HWM_PATH).get(facility.pk)["last_entry_hash"] == tail.entry_hash


def _checkpoint(facility):
    return AuditLog.objects.filter(facility=facility, action=AuditLog.Action.AUDIT_PRUNE_CHECKPOINT).latest(
        "timestamp", "id"
    )


class TestPrunedAnchoredTail:
    def setup_method(self):
        _require_postgres()

    def test_pruning_the_anchored_tail_of_a_dormant_chain_stays_green(self, settings, tmp_path, facility, settings_obj):
        tail = _dormant_chain(facility, settings_obj)
        _anchor(settings, tmp_path, facility, tail)

        result = prune_auditlog(facility, settings_obj, now=timezone.now() + timedelta(days=800))

        assert result["count"] >= 2
        assert not AuditLog.objects.filter(pk=tail.pk).exists()
        checkpoint = _checkpoint(facility)
        assert checkpoint.detail["boundary_hash"] == tail.entry_hash
        ok, err = _verify()
        assert ok, err
        # The next green run anchors the checkpoint, not the pruned row.
        store = HighWaterMarkStore.load(settings.AUDIT_HWM_PATH)
        assert store.get(facility.pk)["last_entry_hash"] == checkpoint.entry_hash

    def test_backdated_tail_removed_by_a_regular_prune_is_still_a_truncation(
        self, settings, tmp_path, facility, settings_obj
    ):
        tail = _dormant_chain(facility, settings_obj)
        _anchor(settings, tmp_path, facility, tail)
        # Owner-level role: backdate the anchored tail past the trigger so that
        # the regular prune (real ``now``) deletes it as expired.
        with transaction.atomic(), bypass_replication_triggers():
            AuditLog.objects.filter(pk=tail.pk).update(timestamp=timezone.now() - timedelta(days=1200))

        prune_auditlog(facility, settings_obj)

        assert not AuditLog.objects.filter(pk=tail.pk).exists()
        assert _checkpoint(facility).detail["boundary_hash"] == tail.entry_hash
        ok, err = _verify()
        assert not ok
        assert TRUNCATION in err

    def test_deleting_the_checkpoint_after_the_prune_is_still_a_truncation(
        self, settings, tmp_path, facility, settings_obj
    ):
        tail = _dormant_chain(facility, settings_obj)
        _anchor(settings, tmp_path, facility, tail)
        prune_auditlog(facility, settings_obj, now=timezone.now() + timedelta(days=800))

        with transaction.atomic(), bypass_replication_triggers():
            AuditLog.objects.filter(pk=_checkpoint(facility).pk).delete()

        ok, err = _verify()
        assert not ok
        assert TRUNCATION in err

    def test_anchor_without_the_anchored_rows_time_is_still_a_truncation(
        self, settings, tmp_path, facility, settings_obj
    ):
        tail = _dormant_chain(facility, settings_obj)
        _anchor(settings, tmp_path, facility, tail)
        store = HighWaterMarkStore.load(settings.AUDIT_HWM_PATH)
        store.chains[str(facility.pk)] = {"count": 2, "last_entry_hash": tail.entry_hash}
        store.save()

        prune_auditlog(facility, settings_obj, now=timezone.now() + timedelta(days=800))

        ok, err = _verify()
        assert not ok
        assert TRUNCATION in err
