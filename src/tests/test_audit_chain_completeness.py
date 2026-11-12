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
- **Pruning the successor of a row that is already missing.** Each deleted
  row authenticated on its own, but nothing checked that its predecessor was
  still there. A protected row deleted past the trigger left a dangling
  ``prev_hash``; the prune then removed the row carrying it, signed it as a
  boundary and the break was gone. The prune now walks the expired part of
  the chain and aborts unless every row links to the row before it or to a
  boundary of an existing checkpoint.
"""

from __future__ import annotations

import io
import logging
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.db import connection, transaction
from django.utils import timezone

from core.models import AuditLog
from core.retention.audit_pruning import prune_auditlog
from core.services.audit import chain as chain_module
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


def _pruned_anchored_chain(facility, tmp_path):
    row, store = _anchored_row(facility, tmp_path, AuditLog.Action.LOGIN)
    assert prune_auditlog(facility, PERIOD) == {"count": 1}
    checkpoint = _checkpoints(facility).get()
    assert checkpoint.detail["boundary_hash"] == row.entry_hash
    assert verify_chain(facility, hwm=store).ok
    return checkpoint, store


def _delete_after_checkpoint_read(monkeypatch, checkpoint_pk):
    """Delete the checkpoint right after ``verify_chain`` has read the checkpoints.

    ``_checkpoint_boundaries`` runs on the checkpoints of the first SELECT and
    before the rows are streamed, which makes the interleaving deterministic.
    Only the call that read this checkpoint triggers the deletion (the command
    verifies other chains as well).
    """
    original = chain_module._checkpoint_boundaries
    calls = []

    def boundaries_then_delete(rows):
        rows = list(rows)
        result = original(rows)
        if not calls and any(row.pk == checkpoint_pk for row in rows):
            calls.append(checkpoint_pk)
            with transaction.atomic(), bypass_replication_triggers():
                AuditLog.objects.filter(pk=checkpoint_pk).delete()
        return result

    monkeypatch.setattr(chain_module, "_checkpoint_boundaries", boundaries_then_delete)
    return calls


class TestCheckpointsBelongToTheCheckedRows:
    def test_a_checkpoint_deleted_between_reads_does_not_authorise_an_empty_chain(
        self, facility, tmp_path, monkeypatch
    ):
        checkpoint, store = _pruned_anchored_chain(facility, tmp_path)
        calls = _delete_after_checkpoint_read(monkeypatch, checkpoint.pk)

        result = verify_chain(facility, hwm=store)

        assert calls == [checkpoint.pk]
        assert not AuditLog.objects.filter(facility=facility).exists()
        assert not result.ok, f"Deleted checkpoint authorised a whole-chain wipe: {result!r}"
        assert result.first_break_id == str(checkpoint.pk)

    def test_the_command_keeps_the_anchor_when_the_checkpoint_vanishes_mid_run(
        self, facility, tmp_path, monkeypatch, settings
    ):
        # The anchor file still names the pruned row, as after the prune run.
        settings.AUDIT_HWM_PATH = str(tmp_path / "anchor.json")
        checkpoint, store = _pruned_anchored_chain(facility, tmp_path)
        pruned_anchor = store.get(facility.pk)["last_entry_hash"]
        anchor_before = (tmp_path / "anchor.json").read_bytes()
        calls = _delete_after_checkpoint_read(monkeypatch, checkpoint.pk)

        with pytest.raises(SystemExit):
            call_command("verify_audit_chain", stdout=io.StringIO(), stderr=io.StringIO())

        assert calls == [checkpoint.pk]
        assert (tmp_path / "anchor.json").read_bytes() == anchor_before
        assert HighWaterMarkStore.load(settings.AUDIT_HWM_PATH).get(facility.pk)["last_entry_hash"] == pruned_anchor

    def test_a_checkpoint_deleted_between_reads_does_not_cover_a_gap(self, facility, tmp_path, monkeypatch):
        old, _ = _anchored_row(facility, tmp_path, AuditLog.Action.LOGIN)
        survivor = AuditLog.objects.create(facility=facility, action=AuditLog.Action.LOGOUT)
        assert prune_auditlog(facility, PERIOD) == {"count": 1}
        checkpoint = _checkpoints(facility).get()
        assert old.entry_hash in checkpoint.detail["boundary_hashes"]
        assert verify_chain(facility).ok
        calls = _delete_after_checkpoint_read(monkeypatch, checkpoint.pk)

        result = verify_chain(facility)

        assert calls == [checkpoint.pk]
        assert AuditLog.objects.filter(pk=survivor.pk).exists()
        assert not result.ok, f"Deleted checkpoint covered the gap: {result!r}"

    def test_a_chain_without_checkpoints_is_unaffected(self, facility, tmp_path):
        _, store = _anchored_row(facility, tmp_path, AuditLog.Action.LOGIN, age_days=1)

        assert verify_chain(facility, hwm=store).ok


def _expired_chain(facility, tmp_path, actions):
    """Old rows in chain order, the newest one externally anchored."""
    old = timezone.now() - timedelta(days=1200)
    rows = [
        AuditLog.objects.create(facility=facility, action=action, timestamp=old + timedelta(seconds=index))
        for index, action in enumerate(actions)
    ]
    result = verify_chain(facility)
    assert result.ok, result
    store = HighWaterMarkStore(path=str(tmp_path / "anchor.json"))
    store.record(facility.pk, result.rows_checked, result.last_entry_hash, result.last_entry_at)
    store.save()
    return rows, HighWaterMarkStore.load(store.path)


def _delete_past_trigger(row_pk):
    with transaction.atomic(), bypass_replication_triggers():
        AuditLog.objects.filter(pk=row_pk).delete()


LOGIN_VIOLATION_LOGOUT = (AuditLog.Action.LOGIN, AuditLog.Action.SECURITY_VIOLATION, AuditLog.Action.LOGOUT)


class TestPruneKeepsEvidenceOfMissingRows:
    def test_a_deleted_protected_row_is_not_pruned_away(self, facility, tmp_path, caplog):
        rows, store = _expired_chain(facility, tmp_path, LOGIN_VIOLATION_LOGOUT)
        anchor_before = (tmp_path / "anchor.json").read_bytes()
        _delete_past_trigger(rows[1].pk)
        assert verify_chain(facility, hwm=store).reason == "prev_hash linkage break (row deleted/inserted)"

        with caplog.at_level(logging.ERROR, logger="core.retention.audit_pruning"):
            result = prune_auditlog(facility, PERIOD)

        assert result == {"count": 0, "integrity_break": str(rows[2].pk)}
        assert AuditLog.objects.filter(pk__in=[rows[0].pk, rows[2].pk]).count() == 2
        assert not _checkpoints(facility).exists()
        assert (tmp_path / "anchor.json").read_bytes() == anchor_before
        after = verify_chain(facility, hwm=HighWaterMarkStore.load(store.path))
        assert not after.ok, f"Missing row was concealed by pruning: {after!r}"
        messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        assert any(str(rows[2].pk) in m and "prev_hash linkage break" in m for m in messages), messages

    def test_a_deleted_middle_row_stays_a_break_for_a_surviving_successor(self, facility, tmp_path):
        rows, _ = _expired_chain(facility, tmp_path, LOGIN_VIOLATION_LOGOUT)
        survivor = AuditLog.objects.create(facility=facility, action=AuditLog.Action.LOGIN)
        _delete_past_trigger(rows[1].pk)

        assert prune_auditlog(facility, PERIOD)["count"] == 0

        assert AuditLog.objects.filter(facility=facility).count() == 3
        assert not _checkpoints(facility).exists()
        assert AuditLog.objects.filter(pk=survivor.pk).exists()
        assert not verify_chain(facility).ok

    def test_a_deleted_chain_head_stays_a_break(self, facility, tmp_path):
        rows, store = _expired_chain(facility, tmp_path, LOGIN_VIOLATION_LOGOUT)
        _delete_past_trigger(rows[0].pk)

        assert prune_auditlog(facility, PERIOD) == {"count": 0, "integrity_break": str(rows[1].pk)}

        assert AuditLog.objects.filter(pk__in=[rows[1].pk, rows[2].pk]).count() == 2
        assert not _checkpoints(facility).exists()
        assert not verify_chain(facility, hwm=store).ok

    def test_a_gap_covered_by_an_earlier_checkpoint_is_pruned(self, facility, tmp_path):
        now = timezone.now()
        first = AuditLog.objects.create(
            facility=facility, action=AuditLog.Action.LOGIN, timestamp=now - timedelta(days=1200)
        )
        second = AuditLog.objects.create(
            facility=facility, action=AuditLog.Action.LOGOUT, timestamp=now - timedelta(days=500)
        )
        survivor = AuditLog.objects.create(facility=facility, action=AuditLog.Action.LOGIN)
        assert prune_auditlog(facility, PERIOD) == {"count": 1}
        assert verify_chain(facility).ok

        # The second row now starts the chain at the boundary of the first
        # checkpoint; a shorter period lets it expire as well.
        assert prune_auditlog(facility, SimpleNamespace(auditlog_retention_months=12)) == {"count": 1}

        assert not AuditLog.objects.filter(pk__in=[first.pk, second.pk]).exists()
        assert AuditLog.objects.filter(pk=survivor.pk).exists()
        assert _checkpoints(facility).count() == 2
        assert verify_chain(facility).ok

    def test_an_intact_chain_around_a_protected_row_is_pruned(self, facility, tmp_path):
        rows, store = _expired_chain(facility, tmp_path, LOGIN_VIOLATION_LOGOUT)

        assert prune_auditlog(facility, PERIOD) == {"count": 2}

        assert list(AuditLog.objects.filter(pk__in=[row.pk for row in rows]).values_list("pk", flat=True)) == [
            rows[1].pk
        ]
        assert verify_chain(facility, hwm=store).ok


@pytest.mark.django_db(transaction=True)
def test_a_protected_row_stays_locked_until_the_prune_commits(facility, tmp_path, monkeypatch):
    """The link check and the deletion cover the same locked rows.

    A protected row is no deletion candidate; were it only read, a concurrent
    owner-level delete could still remove it between the check and the
    commit. A second connection must find it locked.
    """
    from django.db import OperationalError, connections

    from core.retention import audit_pruning

    rows, _ = _expired_chain(facility, tmp_path, LOGIN_VIOLATION_LOGOUT)
    protected = rows[1]
    original = audit_pruning._authenticate_candidates
    outcomes = []

    def probe_lock_then_authenticate(qs, boundaries):
        other = connections.create_connection("default")
        try:
            with other.cursor() as cursor:
                try:
                    cursor.execute("SELECT id FROM core_auditlog WHERE id = %s FOR UPDATE NOWAIT", [str(protected.pk)])
                    outcomes.append(("unlocked", cursor.fetchall()))
                except OperationalError as exc:
                    outcomes.append(("locked", type(exc.__cause__).__name__))
        finally:
            other.close()
        return original(qs, boundaries)

    monkeypatch.setattr(audit_pruning, "_authenticate_candidates", probe_lock_then_authenticate)

    assert prune_auditlog(facility, PERIOD) == {"count": 2}
    assert outcomes == [("locked", "LockNotAvailable")]
    assert AuditLog.objects.filter(pk=protected.pk).exists()
