"""``seed --flush`` resets the external audit anchor with the AuditLog (Refs #2708).

The flush deletes every facility and empties the AuditLog, but the anchor file
lives in its own volume and survived. Whenever a green ``verify_audit_chain``
had anchored a chain with content -- on the demo: a visitor who signs in again
while the hourly reseed is still running -- the next flush left an anchor for a
facility that no longer exists, and every following run aborted as an orphaned
chain (Refs #2296).

Covered here:

- flush -> sign-in in a freshly seeded facility -> green verify anchors it ->
  next flush -> verify green again (facility chain and system chain);
- the flush leaves a valid, signed, empty anchor file;
- an anchor file that cannot be written rolls the flush back;
- without a flush an orphaned anchor still aborts the run (the #2296 check
  stays strict outside the flush path).
"""

from __future__ import annotations

import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, connection, transaction

from core.models import AuditLog, Facility, Organization
from core.seed.flush import flush_seed_data
from core.services.audit import audit_event
from core.services.audit.hwm import HighWaterMarkStore
from core.services.system import bypass_replication_triggers

# The flush runs in its own transaction in production; the ``ALTER TABLE`` it
# issues refuses to run while the test transaction has pending trigger events.
pytestmark = pytest.mark.django_db(transaction=True)

ORPHANED = "im aktuellen Datenbankbestand fehlen"


def _require_postgres():
    assert connection.vendor == "postgresql", "AuditLog chain/trigger/RLS requires PostgreSQL"


def _verify():
    out, err = io.StringIO(), io.StringIO()
    try:
        call_command("verify_audit_chain", stdout=out, stderr=err)
    except SystemExit:
        return False, err.getvalue()
    return True, out.getvalue()


def _seeded_facility(name):
    """A facility as the seed creates it, with a sign-in written into its chain."""
    facility = Facility.objects.create(organization=Organization.objects.create(name=f"Org {name}"), name=name)
    audit_event(AuditLog.Action.LOGIN, user=None, facility=facility)
    audit_event(AuditLog.Action.LOGOUT, user=None, facility=facility)
    return facility


class TestFlushResetsTheAnchor:
    def setup_method(self):
        _require_postgres()

    def test_sign_in_during_the_seed_window_does_not_break_the_next_reset(self, settings, tmp_path):
        settings.AUDIT_HWM_PATH = str(tmp_path / "audit_hwm.json")
        flush_seed_data()
        first = _seeded_facility("Seed A")
        ok, out = _verify()
        assert ok, out
        # The jobs after the reset write markers into the system chain; the
        # next green run anchors that chain as well.
        ok, out = _verify()
        assert ok, out
        anchored = HighWaterMarkStore.load(settings.AUDIT_HWM_PATH)
        assert anchored.get(first.pk)["last_entry_hash"]
        assert anchored.get(None)["last_entry_hash"]

        flush_seed_data()
        second = _seeded_facility("Seed B")

        ok, out = _verify()
        assert ok, out
        # The emptied system chain had an anchor before the flush; the run
        # stays green and anchors the new facility chain only.
        assert HighWaterMarkStore.load(settings.AUDIT_HWM_PATH).anchored_chain_keys() == [str(second.pk)]

    def test_flush_leaves_a_signed_empty_anchor_file(self, settings, tmp_path, facility):
        settings.AUDIT_HWM_PATH = str(tmp_path / "audit_hwm.json")
        audit_event(AuditLog.Action.LOGIN, user=None, facility=facility)
        ok, out = _verify()
        assert ok, out

        flush_seed_data()

        assert HighWaterMarkStore.load(settings.AUDIT_HWM_PATH).chains == {}

    def test_unwritable_anchor_rolls_the_flush_back(self, settings, tmp_path, facility):
        blocker = tmp_path / "not-a-directory"
        blocker.write_text("x", encoding="utf-8")
        settings.AUDIT_HWM_PATH = str(blocker / "audit_hwm.json")

        with pytest.raises(OSError):
            flush_seed_data()

        assert Facility.objects.filter(pk=facility.pk).exists()

    def test_failed_commit_puts_the_previous_anchor_back(self, settings, tmp_path, facility, monkeypatch):
        settings.AUDIT_HWM_PATH = str(tmp_path / "audit_hwm.json")
        audit_event(AuditLog.Action.LOGIN, user=None, facility=facility)
        ok, out = _verify()
        assert ok, out
        anchor_before = (tmp_path / "audit_hwm.json").read_bytes()

        def failing_commit():
            raise DatabaseError("commit failed")

        # The file is replaced before the commit; the commit then fails.
        monkeypatch.setattr(connection, "commit", failing_commit)
        with pytest.raises(DatabaseError):
            flush_seed_data()
        monkeypatch.undo()

        assert Facility.objects.filter(pk=facility.pk).exists()
        assert (tmp_path / "audit_hwm.json").read_bytes() == anchor_before
        ok, out = _verify()
        assert ok, out

    def test_flush_without_anchor_path_creates_no_file(self, settings, tmp_path):
        settings.AUDIT_HWM_PATH = ""

        flush_seed_data()

        assert list(tmp_path.iterdir()) == []


class TestOrphanCheckStaysStrictOutsideTheFlush:
    def setup_method(self):
        _require_postgres()

    def test_facility_removed_without_flush_still_aborts(self, settings, tmp_path):
        settings.AUDIT_HWM_PATH = str(tmp_path / "audit_hwm.json")
        gone = _seeded_facility("Removed")
        ok, out = _verify()
        assert ok, out
        anchor_before = (tmp_path / "audit_hwm.json").read_bytes()

        # The same deletions as the flush, but without its anchor reset.
        with transaction.atomic(), bypass_replication_triggers():
            AuditLog.objects.filter(facility=gone).delete()
        Facility.objects.filter(pk=gone.pk).delete()

        with pytest.raises(CommandError, match=ORPHANED):
            call_command("verify_audit_chain", stdout=io.StringIO(), stderr=io.StringIO())
        assert (tmp_path / "audit_hwm.json").read_bytes() == anchor_before
