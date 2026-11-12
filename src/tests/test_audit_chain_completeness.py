"""A chain must never pass as complete when rows are missing (Refs #2717).

Two paths used to end in a green ``verify_chain`` although the chain had lost
rows:

- **Pruning a row that does not authenticate.** An owner-level role rewrites
  the deletion criteria of an old row (its action or its timestamp) past the
  trigger. The regular prune trusted those fields, deleted the row and signed
  its now invalid hash as a prune boundary; a protected, externally anchored
  ``SECURITY_VIOLATION`` row thereby turned into a "legitimately pruned"
  anchor. The prune now authenticates every row it deletes and aborts the
  facility's run on the first row that fails.
- **A checkpoint deleted between two reads of ``verify_chain``.** The
  checkpoints were read first, the rows streamed afterwards. A checkpoint
  removed in between still legitimised the missing anchor of a chain that
  had meanwhile been emptied. Every checkpoint used as evidence must now be
  part of the streamed, fully checked rows.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import connection, transaction
from django.utils import timezone

from core.models import AuditLog
from core.retention.audit_pruning import prune_auditlog
from core.services.audit.chain import verify_chain
from core.services.audit.hwm import HighWaterMarkStore
from core.services.system import bypass_replication_triggers

pytestmark = pytest.mark.django_db

PERIOD = SimpleNamespace(auditlog_retention_months=24)


@pytest.fixture(autouse=True)
def _require_postgres():
    assert connection.vendor == "postgresql", "AuditLog chain/trigger/RLS requires PostgreSQL"


def _anchored_row(facility, tmp_path, action, *, age_days=1200):
    row = AuditLog.objects.create(facility=facility, action=action, timestamp=timezone.now() - timedelta(days=age_days))
    row.refresh_from_db()
    result = verify_chain(facility)
    assert result.ok, result
    store = HighWaterMarkStore(path=str(tmp_path / "anchor.json"))
    store.record(facility.pk, result.rows_checked, result.last_entry_hash, result.last_entry_at)
    store.save()
    return row, HighWaterMarkStore.load(store.path)


def _tamper(row_pk, **fields):
    with transaction.atomic(), bypass_replication_triggers():
        AuditLog.objects.filter(pk=row_pk).update(**fields)


def _checkpoints(facility):
    return AuditLog.objects.filter(facility=facility, action=AuditLog.Action.AUDIT_PRUNE_CHECKPOINT)


class TestPruneOnlyDeletesAuthenticRows:
    def test_an_exempt_anchored_row_with_a_rewritten_action_is_not_pruned(self, facility, tmp_path, caplog):
        row, store = _anchored_row(facility, tmp_path, AuditLog.Action.SECURITY_VIOLATION)
        assert prune_auditlog(facility, PERIOD) == {"count": 0}
        anchor_before = (tmp_path / "anchor.json").read_bytes()
        # Only the action changes; hash, timestamp and anchor file stay.
        _tamper(row.pk, action=AuditLog.Action.LOGIN)
        assert verify_chain(facility, hwm=store).reason == "entry_hash mismatch (in-place tampering)"

        with caplog.at_level(logging.ERROR, logger="core.retention.audit_pruning"):
            result = prune_auditlog(facility, PERIOD)

        assert result["count"] == 0
        assert AuditLog.objects.filter(pk=row.pk).exists()
        assert not _checkpoints(facility).exists()
        assert (tmp_path / "anchor.json").read_bytes() == anchor_before
        after = verify_chain(facility, hwm=HighWaterMarkStore.load(store.path))
        assert not after.ok, f"Protected anchor was laundered through pruning: {after!r}"
        messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        assert any(str(row.pk) in m and "entry_hash mismatch" in m for m in messages), messages

    def test_a_backdated_row_is_not_pruned(self, facility, tmp_path):
        row, store = _anchored_row(facility, tmp_path, AuditLog.Action.LOGIN, age_days=1)
        # The timestamp moves before the cutoff; the stored hash stays.
        _tamper(row.pk, timestamp=timezone.now() - timedelta(days=1200))

        assert prune_auditlog(facility, PERIOD)["count"] == 0

        assert AuditLog.objects.filter(pk=row.pk).exists()
        assert not _checkpoints(facility).exists()
        assert not verify_chain(facility, hwm=store).ok

    def test_one_tampered_row_keeps_every_other_expired_row(self, facility, tmp_path):
        old = timezone.now() - timedelta(days=1200)
        rows = [
            AuditLog.objects.create(facility=facility, action=AuditLog.Action.LOGIN, timestamp=old) for _ in range(3)
        ]
        _tamper(rows[1].pk, action=AuditLog.Action.LOGOUT)

        assert prune_auditlog(facility, PERIOD)["count"] == 0

        assert AuditLog.objects.filter(facility=facility).count() == 3
        assert not _checkpoints(facility).exists()

    def test_an_unhashed_expired_row_is_not_pruned(self, facility):
        old = timezone.now() - timedelta(days=1200)
        row = AuditLog.objects.create(facility=facility, action=AuditLog.Action.LOGIN, timestamp=old)
        _tamper(row.pk, entry_hash=None)

        assert prune_auditlog(facility, PERIOD)["count"] == 0

        assert AuditLog.objects.filter(pk=row.pk).exists()
        assert not _checkpoints(facility).exists()

    def test_authentic_expired_rows_are_still_pruned(self, facility, tmp_path):
        row, store = _anchored_row(facility, tmp_path, AuditLog.Action.LOGIN)

        assert prune_auditlog(facility, PERIOD) == {"count": 1}

        assert not AuditLog.objects.filter(pk=row.pk).exists()
        assert _checkpoints(facility).get().detail["boundary_hash"] == row.entry_hash
        assert verify_chain(facility, hwm=store).ok
