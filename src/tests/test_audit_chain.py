"""Tests fuer die verkettete HMAC-Integritaet des AuditLog (Refs #1070).

Deckt ab:
- Determinismus/Stabilitaet von ``compute_entry_hash``.
- Aufbau der Kette ueber mehrere ``log_audit_event``/``audit_event``-Calls,
  pro Facility unabhaengig.
- Tamper-Erkennung: direkte ``QuerySet.update()`` (Trigger via
  ``bypass_replication_triggers`` umgangen — modelliert den DB-/Owner-Vektor
  aus #1070) wird von ``verify_chain`` / ``verify_audit_chain`` gemeldet.
- ``backfill_audit_chain`` berechnet korrekte Hashes fuer Bestandszeilen.
- ``prune_auditlog`` schreibt einen Checkpoint; ``verify_audit_chain`` bleibt
  danach gruen.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.db import connection, transaction
from django.utils import timezone

from core.models import AuditLog
from core.services.audit import audit_event, log_audit_event
from core.services.audit.chain import compute_entry_hash, verify_chain
from core.services.retention import prune_auditlog
from core.services.system import bypass_replication_triggers


def _tamper(pk, **fields):
    """Mutiert eine AuditLog-Zeile direkt (umgeht ``save()``-Guard UND den
    DB-Immutability-Trigger) — modelliert den Owner-/``replica``-Vektor."""
    if connection.vendor == "postgresql":
        with transaction.atomic(), bypass_replication_triggers():
            AuditLog.objects.filter(pk=pk).update(**fields)
    else:
        AuditLog.objects.filter(pk=pk).update(**fields)


# ---------------------------------------------------------------------------
# Hash-Determinismus / Stabilitaet
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestComputeEntryHash:
    def test_is_deterministic(self, facility, staff_user):
        row = AuditLog(
            facility=facility,
            user=staff_user,
            action=AuditLog.Action.LOGIN,
            target_type="Client",
            target_id="abc",
            detail={"a": 1, "b": [2, 3]},
            ip_address="10.0.0.1",
        )
        assert compute_entry_hash(row, "") == compute_entry_hash(row, "")

    def test_is_hex_sha256(self, facility):
        row = AuditLog(facility=facility, action=AuditLog.Action.LOGIN, detail={})
        h = compute_entry_hash(row, "")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_prev_hash_changes_result(self, facility):
        row = AuditLog(facility=facility, action=AuditLog.Action.LOGIN, detail={})
        assert compute_entry_hash(row, "") != compute_entry_hash(row, "deadbeef")

    def test_field_change_changes_result(self, facility, staff_user):
        base = AuditLog(facility=facility, user=staff_user, action=AuditLog.Action.LOGIN, detail={"x": 1})
        other = AuditLog(facility=facility, user=staff_user, action=AuditLog.Action.LOGIN, detail={"x": 2})
        other.timestamp = base.timestamp  # nur ``detail`` unterscheidet sich
        assert compute_entry_hash(base, "") != compute_entry_hash(other, "")


# ---------------------------------------------------------------------------
# Kettenaufbau
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestChainBuild:
    def test_first_row_has_empty_prev_hash(self, facility, staff_user):
        entry = audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        entry.refresh_from_db()
        assert entry.prev_hash == ""
        assert entry.entry_hash
        assert entry.entry_hash == compute_entry_hash(entry, "")

    def test_sequential_rows_are_linked(self, facility, staff_user):
        r1 = audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        r2 = audit_event(AuditLog.Action.VIEW_QUALIFIED, user=staff_user, facility=facility)
        r3 = audit_event(AuditLog.Action.LOGOUT, user=staff_user, facility=facility)
        for r in (r1, r2, r3):
            r.refresh_from_db()
        assert r1.prev_hash == ""
        assert r2.prev_hash == r1.entry_hash
        assert r3.prev_hash == r2.entry_hash
        # Jede Zeile reproduziert ihren eigenen entry_hash.
        for r in (r1, r2, r3):
            assert r.entry_hash == compute_entry_hash(r, r.prev_hash)

    def test_log_audit_event_path_is_chained(self, rf, staff_user, client_identified):
        request = rf.get("/")
        request.user = staff_user
        request.current_facility = staff_user.facility
        request.META["REMOTE_ADDR"] = "10.1.2.3"
        a = log_audit_event(request, AuditLog.Action.VIEW_QUALIFIED, target_obj=client_identified)
        b = log_audit_event(request, AuditLog.Action.EXPORT, target_obj=client_identified)
        a.refresh_from_db()
        b.refresh_from_db()
        assert b.prev_hash == a.entry_hash
        assert verify_chain(staff_user.facility).ok

    def test_chains_are_per_facility_independent(self, facility, second_facility, staff_user, second_facility_user):
        a1 = audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        b1 = audit_event(AuditLog.Action.LOGIN, user=second_facility_user, facility=second_facility)
        a2 = audit_event(AuditLog.Action.LOGOUT, user=staff_user, facility=facility)
        b2 = audit_event(AuditLog.Action.LOGOUT, user=second_facility_user, facility=second_facility)
        for r in (a1, b1, a2, b2):
            r.refresh_from_db()
        # Erste Zeile jeder Facility startet die Kette neu.
        assert a1.prev_hash == ""
        assert b1.prev_hash == ""
        # Verkettung bleibt innerhalb der Facility — B-Writes dazwischen
        # verschieben A's prev_hash nicht.
        assert a2.prev_hash == a1.entry_hash
        assert b2.prev_hash == b1.entry_hash
        assert verify_chain(facility).ok
        assert verify_chain(second_facility).ok

    def test_system_chain_facility_none(self, super_admin_user):
        s1 = audit_event(AuditLog.Action.SYSTEM_VIEW, user=super_admin_user, facility=None)
        s2 = audit_event(AuditLog.Action.AUDIT_EXPORT, user=super_admin_user, facility=None)
        s1.refresh_from_db()
        s2.refresh_from_db()
        assert s1.facility_id is None
        assert s2.prev_hash == s1.entry_hash
        assert verify_chain(None).ok


# ---------------------------------------------------------------------------
# Tamper-Erkennung
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTamperDetection:
    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("Tamper-Test umgeht den Immutability-Trigger (PostgreSQL).")

    def test_inplace_detail_mutation_breaks_chain(self, facility, staff_user):
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        victim = audit_event(
            AuditLog.Action.CLIENT_UPDATE,
            user=staff_user,
            facility=facility,
            target_type="Client",
            target_id="42",
            detail={"changed_fields": ["notes"]},
        )
        audit_event(AuditLog.Action.LOGOUT, user=staff_user, facility=facility)
        assert verify_chain(facility).ok

        # Direkte Manipulation des ``detail`` — entry_hash bleibt unveraendert.
        _tamper(victim.pk, detail={"changed_fields": ["evil"]})

        result = verify_chain(facility)
        assert not result.ok
        assert result.first_break_id == str(victim.pk)
        assert "hash" in result.reason.lower()

    def test_deleted_middle_row_breaks_chain(self, facility, staff_user):
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        middle = audit_event(AuditLog.Action.VIEW_QUALIFIED, user=staff_user, facility=facility)
        audit_event(AuditLog.Action.LOGOUT, user=staff_user, facility=facility)
        assert verify_chain(facility).ok
        # Mittlere Zeile lautlos loeschen (umgeht delete()-Guard + Trigger).
        with transaction.atomic(), bypass_replication_triggers():
            AuditLog.objects.filter(pk=middle.pk).delete()
        result = verify_chain(facility)
        assert not result.ok
        assert "linkage" in result.reason.lower() or "deleted" in result.reason.lower()

    def test_forged_checkpoint_does_not_mask_deletion(self, facility, staff_user):
        """Refs #1070 (CRITICAL): Ein GEFAELSCHTER Checkpoint darf eine Loeschung
        nicht legitimieren.

        Der Immutability-Trigger (Migration 0024) schuetzt nur UPDATE/DELETE —
        ein DB-Angreifer ohne ``AUDIT_HASH_KEY`` kann eine mittlere Zeile loeschen
        und einen Checkpoint EINFUEGEN, der den dann danglenden ``prev_hash`` des
        Nachfolgers als Prune-Grenze tarnt. Weil er den Schluessel nicht kennt,
        kann er fuer den Checkpoint keinen gueltigen ``entry_hash`` berechnen —
        ``verify_chain`` darf dessen Grenzen daher nicht vertrauen.
        """
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        r2 = audit_event(AuditLog.Action.VIEW_QUALIFIED, user=staff_user, facility=facility)
        r3 = audit_event(AuditLog.Action.LOGOUT, user=staff_user, facility=facility)
        r2.refresh_from_db()
        r3.refresh_from_db()
        assert verify_chain(facility).ok
        r2_hash = r2.entry_hash

        # r2 loeschen (Trigger umgangen) -> r3.prev_hash zeigt ins Leere.
        with transaction.atomic(), bypass_replication_triggers():
            AuditLog.objects.filter(pk=r2.pk).delete()
        # Gefaelschten Checkpoint einschieben: behauptet r2_hash als Prune-Grenze,
        # hat aber KEINEN gueltigen entry_hash (kein Key). ``bulk_create`` umgeht
        # ``save()`` und damit das Ketten-Sealing — modelliert den Raw-INSERT.
        forged = AuditLog(
            facility=facility,
            action=AuditLog.Action.AUDIT_PRUNE_CHECKPOINT,
            target_type="AuditLog",
            detail={"pruned_count": 1, "boundary_hashes": [r2_hash]},
            timestamp=r3.timestamp + timedelta(seconds=1),
            prev_hash="deadbeef",
            entry_hash=None,
        )
        AuditLog.objects.bulk_create([forged])

        result = verify_chain(facility)
        assert not result.ok
        assert result.first_break_id == str(r3.pk)
        assert "linkage" in result.reason.lower() or "deleted" in result.reason.lower()

    def test_verify_command_exit_code_nonzero_on_tamper(self, facility, staff_user):
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        victim = audit_event(AuditLog.Action.CLIENT_UPDATE, user=staff_user, facility=facility, detail={"k": "v"})
        _tamper(victim.pk, detail={"k": "tampered"})
        with pytest.raises(SystemExit) as exc:
            call_command("verify_audit_chain")
        assert exc.value.code != 0

    def test_verify_command_green_when_intact(self, facility, staff_user):
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        audit_event(AuditLog.Action.LOGOUT, user=staff_user, facility=facility)
        # Kein SystemExit -> exit code 0.
        call_command("verify_audit_chain")


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBackfill:
    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("Backfill nullt Hashes via Trigger-Bypass (PostgreSQL).")

    def test_backfill_recomputes_correct_hashes(self, facility, second_facility, staff_user, second_facility_user):
        rows = [
            audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility),
            audit_event(AuditLog.Action.VIEW_QUALIFIED, user=staff_user, facility=facility),
            audit_event(AuditLog.Action.LOGIN, user=second_facility_user, facility=second_facility),
            audit_event(AuditLog.Action.LOGOUT, user=staff_user, facility=facility),
        ]
        # Bestand simulieren: Hashes entfernen (wie vor der Migration).
        with transaction.atomic(), bypass_replication_triggers():
            AuditLog.objects.update(prev_hash="", entry_hash="")

        call_command("backfill_audit_chain")

        for r in rows:
            r.refresh_from_db()
            assert r.entry_hash, "Backfill hat keinen entry_hash gesetzt"
            assert r.entry_hash == compute_entry_hash(r, r.prev_hash)
        assert verify_chain(facility).ok
        assert verify_chain(second_facility).ok

    def test_backfill_is_idempotent(self, facility, staff_user):
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        audit_event(AuditLog.Action.LOGOUT, user=staff_user, facility=facility)
        before = {r.pk: r.entry_hash for r in AuditLog.objects.all()}
        call_command("backfill_audit_chain")  # nichts zu tun
        after = {r.pk: r.entry_hash for r in AuditLog.objects.all()}
        assert before == after
        assert verify_chain(facility).ok


# ---------------------------------------------------------------------------
# Prune-Checkpoint
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestPruneCheckpoint:
    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("prune_auditlog erfordert PostgreSQL.")

    def _old(self, facility, action, days_ago, now):
        return AuditLog.objects.create(facility=facility, action=action, timestamp=now - timedelta(days=days_ago))

    def test_prune_writes_checkpoint_and_chain_stays_valid(self, facility, settings_obj):
        settings_obj.auditlog_retention_months = 12
        settings_obj.save()
        now = timezone.now()
        # In chronologischer Reihenfolge anlegen (aelteste zuerst).
        old1 = self._old(facility, AuditLog.Action.LOGIN, 500, now)
        old2 = self._old(facility, AuditLog.Action.LOGOUT, 499, now)
        self._old(facility, AuditLog.Action.LOGIN, 3, now)
        self._old(facility, AuditLog.Action.LOGOUT, 2, now)
        old1.refresh_from_db()
        old2.refresh_from_db()
        assert verify_chain(facility).ok

        result = prune_auditlog(facility, settings_obj, now=now, dry_run=False)
        assert result["count"] == 2
        assert not AuditLog.objects.filter(pk=old1.pk).exists()

        cp = AuditLog.objects.filter(facility=facility, action=AuditLog.Action.AUDIT_PRUNE_CHECKPOINT).latest(
            "timestamp"
        )
        assert cp.detail["pruned_count"] == 2
        assert cp.detail["boundary_hash"] == old2.entry_hash

        chk = verify_chain(facility)
        assert chk.ok, chk.reason
        # Auch das Kommando bleibt gruen (kein SystemExit).
        call_command("verify_audit_chain")

    def test_prune_keeps_chain_valid_with_exempt_survivors(self, facility, settings_obj):
        """SECURITY_VIOLATION ist prune-exempt und ueberlebt zwischen geloeschten
        Zeilen — die Checkpoint-Boundaries muessen alle Schnittpunkte abdecken."""
        settings_obj.auditlog_retention_months = 12
        settings_obj.save()
        now = timezone.now()
        self._old(facility, AuditLog.Action.LOGIN, 500, now)  # geloescht
        self._old(facility, AuditLog.Action.SECURITY_VIOLATION, 499, now)  # exempt -> bleibt
        self._old(facility, AuditLog.Action.LOGIN, 498, now)  # geloescht
        self._old(facility, AuditLog.Action.LOGOUT, 2, now)  # bleibt
        assert verify_chain(facility).ok

        result = prune_auditlog(facility, settings_obj, now=now, dry_run=False)
        assert result["count"] == 2

        chk = verify_chain(facility)
        assert chk.ok, chk.reason

    def test_checkpoint_action_is_prune_exempt(self, facility, settings_obj):
        from core.retention.audit_pruning import PRUNE_EXEMPT_ACTIONS

        assert AuditLog.Action.AUDIT_PRUNE_CHECKPOINT in PRUNE_EXEMPT_ACTIONS


@pytest.fixture
def rf():
    from django.test import RequestFactory

    return RequestFactory()
