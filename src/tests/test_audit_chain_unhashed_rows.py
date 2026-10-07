"""Unhashed AuditLog rows after the chain start (Refs #2449, #1070).

``entry_hash``/``prev_hash`` are nullable so that rows written before the hash
chain existed stay readable. That must not open a second write path: a role that
can run SQL as the runtime app role -- but does not know ``AUDIT_HASH_KEY`` --
could otherwise append unhashed rows at the end of a chain, ``verify_chain``
would skip them, and the head lookup would let the next legitimate row start a
fresh chain behind them.

Covered here:

- the database rejects an INSERT without a hash as the app role;
- ``verify_chain`` reports an unhashed row after the first hashed row of the
  same chain (facility chain and system chain) with its own reason;
- unhashed rows before the chain start stay accepted;
- the head lookup (``_latest_link`` / ``_system_chain_latest_link_raw``) only
  considers hashed rows.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from core.models import AuditLog
from core.services.audit import audit_event
from core.services.audit import chain as chain_mod
from core.services.audit.chain import verify_chain
from core.services.system import bypass_replication_triggers
from tests.test_rls_functional import as_rls_role, rls_test_role  # noqa: F401

pytestmark = pytest.mark.django_db

UNHASHED_REASON = "unhashed row after chain start"


def _require_postgres():
    assert connection.vendor == "postgresql", "AuditLog chain/trigger/RLS requires PostgreSQL"


def _legit(facility, note):
    """A regular audit row through the hash-chained write path."""
    row = audit_event(AuditLog.Action.SECURITY_VIOLATION, user=None, facility=facility, detail={"reason": note})
    row.refresh_from_db()
    return row


def _strip_hashes(pk):
    """Clear the hash fields of an existing row past the immutability trigger.

    Models a row without a hash at a chosen position: a row from before the
    chain existed, or one an owner-level attacker slipped in. The INSERT path
    itself is closed by the database (see ``TestAppRoleInsert``).
    """
    with transaction.atomic(), bypass_replication_triggers():
        AuditLog.objects.filter(pk=pk).update(prev_hash=None, entry_hash=None)


def _chain_facility(request, fixture_name):
    return None if fixture_name == "system" else request.getfixturevalue(fixture_name)


CHAINS = pytest.mark.parametrize("chain", ["facility", "system"])


# ---------------------------------------------------------------------------
# Database: no INSERT without a hash
# ---------------------------------------------------------------------------


class TestAppRoleInsert:
    def setup_method(self):
        _require_postgres()

    def _insert_as_app_role(self, role, facility, **hash_fields):
        with as_rls_role(role, facility_id=facility.pk):
            AuditLog.objects.bulk_create(
                [
                    AuditLog(
                        action=AuditLog.Action.SECURITY_VIOLATION,
                        facility=facility,
                        user=None,
                        timestamp=timezone.now() + timedelta(seconds=1),
                        detail={"reason": "forged by app role"},
                        target_type="Probe",
                        **hash_fields,
                    )
                ]
            )

    def test_unhashed_row_appended_by_the_app_role_is_rejected(self, facility, rls_test_role):  # noqa: F811
        """The scenario of the finding: an app-role INSERT without a hash at the
        chain end. It must fail in the database; the chain stays intact and the
        next legitimate row links to the last legitimate one."""
        _legit(facility, "a")
        b = _legit(facility, "b")

        with pytest.raises(IntegrityError), transaction.atomic():
            self._insert_as_app_role(rls_test_role, facility)

        assert not AuditLog.objects.filter(facility=facility, detail__reason="forged by app role").exists()
        c = _legit(facility, "c")
        assert c.prev_hash == b.entry_hash
        result = verify_chain(facility)
        assert result.ok, result
        assert result.rows_checked == 3

    @pytest.mark.parametrize(
        "hash_fields",
        [
            {"entry_hash": "", "prev_hash": ""},
            {"entry_hash": None, "prev_hash": ""},
            {"entry_hash": "f" * 64, "prev_hash": None},
        ],
        ids=["empty-entry-hash", "null-entry-hash", "null-prev-hash"],
    )
    def test_insert_with_missing_hash_field_is_rejected(self, facility, rls_test_role, hash_fields):  # noqa: F811
        with pytest.raises(IntegrityError), transaction.atomic():
            self._insert_as_app_role(rls_test_role, facility, **hash_fields)

    def test_insert_with_a_forged_hash_passes_the_database_but_not_the_check(self, facility, rls_test_role):  # noqa: F811
        """The database only demands that a hash is present; without the key the
        attacker can only write a wrong one, which ``verify_chain`` reports."""
        b = _legit(facility, "b")
        self._insert_as_app_role(rls_test_role, facility, entry_hash="0" * 64, prev_hash=b.entry_hash)

        result = verify_chain(facility)
        assert not result.ok
        assert result.reason == "entry_hash mismatch (in-place tampering)"

    def test_regular_write_path_works_as_app_role(self, facility, rls_test_role):  # noqa: F811
        with as_rls_role(rls_test_role, facility_id=facility.pk):
            row = audit_event(
                AuditLog.Action.SECURITY_VIOLATION, user=None, facility=facility, detail={"reason": "app role"}
            )
        row.refresh_from_db()
        assert row.entry_hash
        assert verify_chain(facility).ok


# ---------------------------------------------------------------------------
# verify_chain: unhashed rows after the chain start are a break
# ---------------------------------------------------------------------------


class TestVerifyChainUnhashedRows:
    def setup_method(self):
        _require_postgres()

    @CHAINS
    def test_unhashed_row_at_the_end_is_reported(self, request, chain):
        fac = _chain_facility(request, chain)
        _legit(fac, "a")
        tail = _legit(fac, "b")
        _strip_hashes(tail.pk)

        result = verify_chain(fac)

        assert not result.ok
        assert result.first_break_id == str(tail.pk)
        assert result.reason.startswith(UNHASHED_REASON)

    @CHAINS
    def test_unhashed_row_in_the_middle_is_reported_at_that_row(self, request, chain):
        fac = _chain_facility(request, chain)
        _legit(fac, "a")
        middle = _legit(fac, "b")
        _legit(fac, "c")
        _strip_hashes(middle.pk)

        result = verify_chain(fac)

        assert not result.ok
        assert result.first_break_id == str(middle.pk)
        assert result.reason.startswith(UNHASHED_REASON)

    @CHAINS
    def test_unhashed_rows_before_the_chain_start_stay_accepted(self, request, chain):
        fac = _chain_facility(request, chain)
        # Only rows of this test may precede the chain start.
        existing = AuditLog.objects.filter(facility=fac).values_list("pk", flat=True)
        for pk in list(existing):
            _strip_hashes(pk)
        first_old = _legit(fac, "old-1")
        second_old = _legit(fac, "old-2")
        _strip_hashes(first_old.pk)
        _strip_hashes(second_old.pk)

        start = _legit(fac, "start")
        _legit(fac, "next")

        assert start.prev_hash == ""
        result = verify_chain(fac)
        assert result.ok, result
        assert result.rows_checked == 2

    def test_chain_of_only_unhashed_rows_is_ok_without_tail(self, facility):
        old = _legit(facility, "old")
        _strip_hashes(old.pk)

        result = verify_chain(facility)

        assert result.ok
        assert result.rows_checked == 0
        assert result.last_entry_hash is None

    def test_reported_break_carries_the_last_verified_tail(self, facility):
        _legit(facility, "a")
        b = _legit(facility, "b")
        tail = _legit(facility, "c")
        _strip_hashes(tail.pk)

        result = verify_chain(facility)

        assert result.rows_checked == 2
        assert result.last_entry_hash == b.entry_hash


# ---------------------------------------------------------------------------
# Head lookup: only hashed rows
# ---------------------------------------------------------------------------


class TestHeadLookupIgnoresUnhashedRows:
    def setup_method(self):
        _require_postgres()

    @CHAINS
    def test_next_row_links_to_the_last_hashed_row(self, request, chain):
        fac = _chain_facility(request, chain)
        a = _legit(fac, "a")
        b = _legit(fac, "b")
        _strip_hashes(b.pk)

        c = _legit(fac, "c")

        assert c.prev_hash == a.entry_hash

    def test_facility_lookup_returns_the_last_hashed_row(self, facility):
        a = _legit(facility, "a")
        b = _legit(facility, "b")
        _strip_hashes(b.pk)

        assert chain_mod._latest_link(facility.pk) == (a.timestamp, a.entry_hash)

    def test_system_lookup_returns_the_last_hashed_row(self):
        a = _legit(None, "a")
        b = _legit(None, "b")
        _strip_hashes(b.pk)

        assert chain_mod._system_chain_latest_link_raw() == (a.timestamp, a.entry_hash)

    def test_empty_entry_hash_counts_as_unhashed(self, facility):
        a = _legit(facility, "a")
        b = _legit(facility, "b")
        with transaction.atomic(), bypass_replication_triggers():
            AuditLog.objects.filter(pk=b.pk).update(prev_hash="", entry_hash="")

        assert chain_mod._latest_link(facility.pk) == (a.timestamp, a.entry_hash)
        assert not verify_chain(facility).ok
