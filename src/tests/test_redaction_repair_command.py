"""Repair of events, persons and cases whose redaction was undone (Refs #2188, related #2177).

Until the rotation paths took a row lock and compared the value they had read
(v0.96.0), key rotation wrote ``data_json`` back without either. When the
retention run, a manual soft delete or an anonymisation emptied a row while a
rotation was re-encrypting it, the rotation replaced the emptied payload with a
freshly encrypted copy of the old values. The row kept its "done" state —
``is_deleted`` for an event, ``anonymized_at`` for a person or a case — and
every current run skips rows in that state, so the values stay for good.

``repair_eventhistory_redaction --target {event,client,case,all}`` finds such
rows (dry run by default) and, with ``--apply``, puts them back into exactly
the state the regular redaction leaves: under a row lock, honouring legal
holds, with one audit entry per row, never touching a correctly redacted row.
The call without ``--target`` keeps repairing the event history only.
"""

import io
import json
import re
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core.management import CommandError, call_command
from django.db import connection
from django.db.models.signals import post_save
from django.utils import timezone

from core.models import AuditLog, Case, Client, Event, Facility
from core.models.retention import LegalHold
from core.services.case.deletion import redact_case_content
from core.services.client import anonymize_client
from core.services.events import create_event, soft_delete_event
from core.services.file_vault.encryption import encrypt_field
from tests._row_lock_helpers import mitschnitt
from tests.test_eventhistory_repair_command import _non_owner_with_rls_bypass
from tests.test_rls_functional import as_rls_role, rls_test_role  # noqa: F401

COMMAND = "repair_eventhistory_redaction"
DATA_TABLES = ("core_client", "core_case", "core_event")
ALL_TABLES = (*DATA_TABLES, "core_eventhistory")


def _rotated_payload():
    """``data_json`` as a rotation left it: re-encrypted old values instead of ``{}``."""
    return {
        "notiz": encrypt_field("Erstgespräch vertraulich"),
        "dauer": 15,
        "ort": encrypt_field("Bahnhof"),
    }


def _table_snapshot(table):
    """Byte-exact copy of every row of *table* (``row_to_json`` as text)."""
    with connection.cursor() as cur:
        cur.execute(f"SELECT id::text, row_to_json(t)::text FROM {table} t ORDER BY id")  # noqa: S608
        return dict(cur.fetchall())


def _row_state(table, pk):
    return json.loads(_table_snapshot(table)[str(pk)])


def _run(*args):
    out, err = io.StringIO(), io.StringIO()
    call_command(COMMAND, *args, stdout=out, stderr=err)
    return out.getvalue() + err.getvalue()


def _line(output, target, facility):
    prefix = f"[{target}] Facility '{facility.name}':"
    lines = [line for line in output.splitlines() if line.startswith(prefix)]
    assert len(lines) == 1, output
    return lines[0]


def _count(line, label):
    match = re.search(rf"{re.escape(label)} (\d+)", line)
    assert match, (label, line)
    return int(match.group(1))


class _Builder:
    """Creates rows of one table through the regular services, then damages them.

    ``redacted`` rows went through today's redaction; ``damaged`` rows went
    through it too and were then overwritten the way a rotation did before
    v0.96.0 (``data_json`` plus ``updated_at``). ``reference`` keeps the state
    right after the redaction, so a test can compare the repaired row with it.
    """

    table = ""
    target = ""
    target_type = ""
    category = ""
    model = None
    done_field = ""

    def __init__(self, ctx):
        self.ctx = ctx
        self.reference = {}
        self._persons = 0

    def person(self):
        self._persons += 1
        return Client.objects.create(
            facility=self.ctx.facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym=f"Person-{self.table}-{self._persons:02d}",
            created_by=self.ctx.user,
        )

    def event(self, client):
        return create_event(
            facility=self.ctx.facility,
            user=self.ctx.user,
            document_type=self.ctx.doc_type,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
            client=client,
        )

    def damage(self, pk):
        self.reference[pk] = _row_state(self.table, pk)
        self.model.objects.filter(pk=pk).update(
            data_json=_rotated_payload(), updated_at=timezone.now() + timedelta(seconds=5)
        )
        return pk

    def hold(self, pk):
        LegalHold.objects.create(
            facility=self.ctx.facility,
            target_type=self.target_type,
            target_id=pk,
            reason="Verfahren",
            created_by=self.ctx.user,
        )

    def build(self, state):
        raise NotImplementedError


class _EventBuilder(_Builder):
    table = "core_event"
    target = "event"
    target_type = "Event"
    category = "event_repaired"
    model = Event
    done_field = "deleted_at"

    def build(self, state):
        event = self.event(self.person())
        if state == "unredacted":
            return event.pk
        soft_delete_event(event, self.ctx.user)
        return self.damage(event.pk) if state == "damaged" else event.pk


class _ClientBuilder(_Builder):
    table = "core_client"
    target = "client"
    target_type = "Client"
    category = "client_repaired"
    model = Client
    done_field = "anonymized_at"

    def build(self, state):
        client = self.person()
        Client.objects.filter(pk=client.pk).update(data_json={"notiz": encrypt_field("Anamnese vertraulich")})
        if state == "unredacted":
            return client.pk
        client.refresh_from_db()
        anonymize_client(client, user=None)
        return self.damage(client.pk) if state == "damaged" else client.pk


class _CaseBuilder(_Builder):
    table = "core_case"
    target = "case"
    target_type = "Case"
    category = "case_repaired"
    model = Case
    done_field = "anonymized_at"

    def build(self, state):
        case = Case.objects.create(
            facility=self.ctx.facility,
            client=self.person(),
            title="Fallverlauf",
            status=Case.Status.OPEN,
            created_by=self.ctx.user,
        )
        Case.objects.filter(pk=case.pk).update(data_json={"notiz": encrypt_field("Hilfeplan vertraulich")})
        if state == "unredacted":
            return case.pk
        case.refresh_from_db()
        redact_case_content(case)
        return self.damage(case.pk) if state == "damaged" else case.pk


BUILDERS = {"event": _EventBuilder, "client": _ClientBuilder, "case": _CaseBuilder}
TARGETS = list(BUILDERS)


@pytest.fixture
def ctx(facility, staff_user, doc_type_contact):
    return SimpleNamespace(facility=facility, user=staff_user, doc_type=doc_type_contact)


@pytest.fixture(params=TARGETS)
def stock(request, ctx):
    """Three rows of one table: damaged, correctly redacted, never redacted."""
    builder = BUILDERS[request.param](ctx)
    rows = {state: builder.build(state) for state in ("damaged", "redacted", "unredacted")}
    return SimpleNamespace(builder=builder, rows=rows)


def _audits(builder):
    return AuditLog.objects.filter(action=AuditLog.Action.DELETE, detail__category=builder.category)


@pytest.mark.django_db
class TestDryRun:
    @pytest.mark.parametrize("args", [(), ("--dry-run",)], ids=["default", "dry-run"])
    def test_reports_what_apply_then_repairs_and_writes_nothing(self, stock, facility, args):
        builder = stock.builder
        before = _table_snapshot(builder.table)
        audits = AuditLog.objects.count()

        preview = _run("--target", builder.target, *args)

        line = _line(preview, builder.target, facility)
        assert _count(line, "found") == 1
        assert _count(line, "deferred (legal hold)") == 0
        assert "Dry run — nothing changed" in preview
        assert "--apply" in preview
        assert _table_snapshot(builder.table) == before
        assert AuditLog.objects.count() == audits

        applied = _line(_run("--target", builder.target, "--apply"), builder.target, facility)
        assert _count(applied, "repaired") == _count(line, "found")


@pytest.mark.django_db
class TestRepair:
    def test_damaged_row_gets_back_the_state_of_the_regular_redaction(self, stock, facility):
        builder = stock.builder
        pk = stock.rows["damaged"]
        before = _table_snapshot(builder.table)
        audits = AuditLog.objects.count()

        output = _run("--target", builder.target, "--apply")

        repaired = _row_state(builder.table, pk)
        reference = builder.reference[pk]
        damaged = json.loads(before[str(pk)])
        # Exactly the redaction's result; ``updated_at`` stays as the rotation left it.
        assert {k: v for k, v in repaired.items() if k != "updated_at"} == {
            k: v for k, v in reference.items() if k != "updated_at"
        }
        assert repaired["updated_at"] == damaged["updated_at"]
        # The redaction timestamp dates the first erasure — the repair keeps it.
        assert repaired[builder.done_field] == reference[builder.done_field] is not None

        after = _table_snapshot(builder.table)
        for state in ("redacted", "unredacted"):
            other = str(stock.rows[state])
            assert after[other] == before[other], state

        assert AuditLog.objects.count() == audits + 1
        (proof,) = _audits(builder)
        assert proof.facility_id == facility.pk
        assert proof.target_type == builder.target_type
        assert proof.target_id == str(pk)
        assert proof.detail["count"] == 1

        line = _line(output, builder.target, facility)
        assert (_count(line, "found"), _count(line, "repaired"), _count(line, "skipped")) == (1, 1, 0)
        assert "Dry run" not in output

    def test_second_run_finds_nothing_and_writes_nothing(self, stock, facility):
        builder = stock.builder
        _run("--target", builder.target, "--apply")
        before = _table_snapshot(builder.table)
        audits = AuditLog.objects.count()

        line = _line(_run("--target", builder.target, "--apply"), builder.target, facility)

        assert (_count(line, "found"), _count(line, "repaired")) == (0, 0)
        assert _table_snapshot(builder.table) == before
        assert AuditLog.objects.count() == audits

    def test_other_facility_is_not_touched(self, stock, facility):
        builder = stock.builder
        other = Facility.objects.create(organization=facility.organization, name="Zweitstelle")
        before = _table_snapshot(builder.table)

        output = _run("--target", builder.target, "--apply", "--facility", other.name)

        assert _count(_line(output, builder.target, other), "found") == 0
        assert f"Facility '{facility.name}'" not in output
        assert _table_snapshot(builder.table) == before

    def test_live_event_of_an_anonymised_person_is_repaired(self, ctx, facility):
        """The anonymisation empties the person's live events too
        (``_redact_live_events``); a rotation could undo that the same way."""
        builder = _EventBuilder(ctx)
        client = builder.person()
        event = builder.event(client)
        anonymize_client(client, user=None)
        builder.damage(event.pk)

        _run("--target", "event", "--apply")

        repaired = _row_state("core_event", event.pk)
        assert repaired["is_deleted"] is False
        assert repaired["data_json"] == {} == builder.reference[event.pk]["data_json"]
        assert repaired["search_text"] == builder.reference[event.pk]["search_text"]
        assert _audits(builder).count() == 1


@pytest.mark.django_db
class TestLegalHold:
    """An active hold orders the current state to be kept — this one as well."""

    def test_held_row_is_counted_but_left_untouched(self, stock, facility):
        builder = stock.builder
        builder.hold(stock.rows["damaged"])
        before = _table_snapshot(builder.table)
        audits = AuditLog.objects.count()

        preview = _line(_run("--target", builder.target), builder.target, facility)
        applied = _line(_run("--target", builder.target, "--apply"), builder.target, facility)

        assert (_count(preview, "found"), _count(preview, "deferred (legal hold)")) == (1, 1)
        assert (_count(applied, "found"), _count(applied, "repaired")) == (1, 0)
        assert _count(applied, "deferred (legal hold)") == 1
        assert _table_snapshot(builder.table) == before
        assert AuditLog.objects.count() == audits


@pytest.mark.django_db
class TestLocksAndRaces:
    def test_row_redacted_between_detection_and_lock_is_left_alone(self, stock, facility):
        """Detection runs without locks; only what is still damaged under the
        lock is repaired. A regular redaction in between wins, and no audit
        entry claims a repair that did not happen."""
        builder = stock.builder
        pk = stock.rows["damaged"]

        def regular_redaction():
            builder.model.objects.filter(pk=pk).update(data_json={})

        with mitschnitt() as recorded:
            recorded.nach_sperre(builder.table, regular_redaction)
            output = _run("--target", builder.target, "--apply")

        assert recorded.offene_schranken() == []
        assert _row_state(builder.table, pk)["data_json"] == {}
        assert _audits(builder).count() == 0
        line = _line(output, builder.target, facility)
        assert (_count(line, "repaired"), _count(line, "skipped")) == (0, 1)

    @pytest.mark.parametrize(
        ("target", "expected"),
        [
            ("client", ["core_client", "core_case", "core_event"]),
            ("case", ["core_client", "core_case", "core_event"]),
            ("event", ["core_client", "core_event"]),
        ],
    )
    def test_lock_order_person_case_event_before_the_first_audit(self, ctx, target, expected):
        builder = BUILDERS[target](ctx)
        client = builder.person()
        case = Case.objects.create(
            facility=ctx.facility, client=client, title="Fall", status=Case.Status.OPEN, created_by=ctx.user
        )
        event = builder.event(client)
        Event.objects.filter(pk=event.pk).update(case=case)
        if target == "client":
            Client.objects.filter(pk=client.pk).update(data_json={"notiz": encrypt_field("vertraulich")})
            client.refresh_from_db()
            anonymize_client(client, user=None)
            builder.damage(client.pk)
        elif target == "case":
            case.refresh_from_db()
            redact_case_content(case)
            builder.damage(case.pk)
        else:
            event.refresh_from_db()
            soft_delete_event(event, ctx.user)
            builder.damage(event.pk)
        audit_positions = []

        with mitschnitt() as recorded:

            def on_audit(sender, instance, created, **kwargs):
                if created:
                    audit_positions.append(len(recorded))

            post_save.connect(on_audit, sender=AuditLog, weak=False)
            try:
                _run("--target", target, "--apply")
            finally:
                post_save.disconnect(on_audit, sender=AuditLog)

        assert [t for t in recorded.sperrfolge() if t in DATA_TABLES] == expected
        lock_positions = [i for i, query in enumerate(recorded) if any(query.sperrt(t) for t in DATA_TABLES)]
        assert audit_positions, "no audit entry written"
        assert max(lock_positions) < min(audit_positions)


@pytest.mark.django_db
class TestOutput:
    def test_counts_and_facility_names_only(self, stock, facility):
        """A maintenance log ends up in journals and tickets — no field values,
        no encrypted tokens, no row ids."""
        builder = stock.builder
        for args in ((), ("--apply",)):
            output = _run("--target", builder.target, *args)
            assert "gAAAA" not in output
            assert "__encrypted__" not in output
            for secret in ("vertraulich", "Bahnhof"):
                assert secret not in output
            for pk in stock.rows.values():
                assert str(pk) not in output
            assert _line(output, builder.target, facility)


@pytest.mark.django_db
class TestTargetSelection:
    def test_call_without_target_repairs_the_event_history_only(self, ctx, facility):
        builders = [cls(ctx) for cls in BUILDERS.values()]
        damaged = [b.build("damaged") for b in builders]
        before = {b.table: _table_snapshot(b.table) for b in builders}

        output = _run("--apply")

        assert "Einrichtung 'Teststelle'" in output
        assert "[" not in output
        for builder, pk in zip(builders, damaged, strict=True):
            assert _table_snapshot(builder.table)[str(pk)] == before[builder.table][str(pk)]
            assert _audits(builder).count() == 0

    def test_all_repairs_person_case_event_then_history(self, ctx, facility):
        builders = [cls(ctx) for cls in BUILDERS.values()]
        for builder in builders:
            builder.build("damaged")

        output = _run("--target", "all", "--apply")

        totals = [line.split("]")[0] + "]" for line in output.splitlines() if "] Total:" in line]
        assert totals == ["[client]", "[case]", "[event]", "[eventhistory]"]
        for builder in builders:
            assert _count(_line(output, builder.target, facility), "repaired") == 1
            assert _audits(builder).count() == 1


def _reads_any(recorded, tables):
    return [query for query in recorded if any(query.liest(table) for table in tables)]


def _snapshots():
    return {table: _table_snapshot(table) for table in ALL_TABLES}


@pytest.mark.django_db
class TestWithoutTableOwnership:
    """Only the owner of a table may repair it.

    Under row level security the app role sees no row of another facility —
    detection would report a clean stock that is not. The session variable
    ``app.is_super_admin`` (which any role may set) or a BYPASSRLS role lets a
    connection read every row, but that is still not the maintenance role.
    Every form refuses before it reads a single row of the repaired tables.
    """

    @pytest.fixture
    def all_tables_damaged(self, ctx):
        return {target: BUILDERS[target](ctx).build("damaged") for target in TARGETS}

    @pytest.mark.parametrize("target", [*TARGETS, "all"])
    @pytest.mark.parametrize("args", [(), ("--apply",)], ids=["dry-run", "apply"])
    @pytest.mark.parametrize("via", ["no_bypass", "session_variable", "bypassrls"])
    def test_command_refuses_before_reading(
        self,
        all_tables_damaged,
        facility,
        rls_test_role,  # noqa: F811
        target,
        args,
        via,
    ):
        before = _snapshots()
        audits = AuditLog.objects.count()
        expected = "bypass" if via == "no_bypass" else "does not own"

        if via == "no_bypass":
            context = as_rls_role(rls_test_role, facility_id=facility.pk)
        else:
            context = _non_owner_with_rls_bypass(rls_test_role, facility, via)
        with context:
            with mitschnitt() as recorded:
                with pytest.raises(CommandError, match=expected):
                    _run("--target", target, *args)

        assert not _reads_any(recorded, ALL_TABLES)
        assert _snapshots() == before
        assert AuditLog.objects.count() == audits

    @pytest.mark.parametrize("target", TARGETS)
    @pytest.mark.parametrize("dry_run", [True, False], ids=["dry-run", "apply"])
    @pytest.mark.parametrize("via", ["no_bypass", "session_variable", "bypassrls"])
    def test_service_refuses_before_reading(
        self,
        all_tables_damaged,
        facility,
        rls_test_role,  # noqa: F811
        target,
        dry_run,
        via,
    ):
        from core.retention import eventhistory, redaction_repair

        repair = redaction_repair.REPAIRS[target]
        table = BUILDERS[target].table
        before = _snapshots()
        audits = AuditLog.objects.count()

        if via == "no_bypass":
            context = as_rls_role(rls_test_role, facility_id=facility.pk)
        else:
            context = _non_owner_with_rls_bypass(rls_test_role, facility, via)
        with context:
            with mitschnitt() as recorded:
                with pytest.raises(eventhistory.OwnerContextMissingError):
                    repair(facility, dry_run=dry_run)

        assert not _reads_any(recorded, (table,))
        assert _snapshots() == before
        assert AuditLog.objects.count() == audits

    @pytest.mark.parametrize("target", TARGETS)
    def test_owner_without_bypass_refuses_before_reading(
        self,
        all_tables_damaged,
        facility,
        rls_test_role,  # noqa: F811
        target,
    ):
        """Ownership without a bypass is not enough either: under FORCE ROW
        LEVEL SECURITY the owner sees only its facility and would report the
        others clean. Ownership moves for the test transaction only."""
        from core.retention import eventhistory, redaction_repair
        from core.services.system import current_connection_owns, has_rls_bypass_context

        table = BUILDERS[target].table
        with connection.cursor() as cur:
            # Deferred foreign-key checks of the setup must fire before the DDL.
            cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cur.execute(f"ALTER TABLE {table} OWNER TO {rls_test_role}")
        before = _snapshots()

        with as_rls_role(rls_test_role, facility_id=facility.pk):
            assert current_connection_owns(table)
            assert not has_rls_bypass_context()
            with mitschnitt() as recorded:
                with pytest.raises(eventhistory.OwnerContextMissingError, match="bypass"):
                    redaction_repair.REPAIRS[target](facility, dry_run=True)

        assert not _reads_any(recorded, (table,))
        assert _snapshots() == before
