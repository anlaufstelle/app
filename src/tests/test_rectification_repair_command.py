"""Repair of rectification requests of anonymised persons (Refs #2188, related #2177).

The anonymisation of a person empties the four value columns of every Art. 16
rectification request that targets the person (``old_value``, ``new_value``,
``reason``, ``review_note`` set to NULL, ``_redact_rectification_requests``).
Until v0.96.0 the key rotation read those columns without a row lock and wrote
the re-encrypted values back without comparing them with what it had read.
When the anonymisation ran in between, the rotation restored the values. The
person stays anonymised and no current run looks at its requests again.

``repair_eventhistory_redaction --target rectification`` finds such requests
(dry run by default) and, with ``--apply``, puts them back into exactly the
state the anonymisation leaves: under the person's lock chain, honouring legal
holds on the person, with one audit entry per request, never touching a
correctly emptied request or a request of a person that is not anonymised.
"""

import pytest
from django.core.management import CommandError
from django.db import connection
from django.db.models.signals import post_save
from django.utils import timezone

from core.models import AuditLog, Case, Client, Facility, RectificationRequest
from core.models.retention import LegalHold
from core.services.client import anonymize_client
from core.services.events import create_event
from core.services.file_vault.encryption import encrypt_field
from tests._row_lock_helpers import mitschnitt
from tests.test_eventhistory_repair_command import _non_owner_with_rls_bypass
from tests.test_redaction_repair_command import _count, _line, _reads_any, _row_state, _run, _table_snapshot
from tests.test_rls_functional import as_rls_role, rls_test_role  # noqa: F401

TARGET = "rectification"
TABLE = "core_rectificationrequest"
CATEGORY = "rectification_repaired"
COLUMNS = ("old_value", "new_value", "reason", "review_note")
WATCHED_TABLES = (TABLE, "core_client", "core_case", "core_event", "core_eventhistory")
SECRETS = ("Altwert", "Wunschwert", "Begründung vertraulich", "Prüfvermerk vertraulich")


def _rotated_values():
    """The four columns as a rotation left them: re-encrypted old values instead of NULL."""
    return dict(
        zip(
            COLUMNS,
            (encrypt_field('"Altwert"'), encrypt_field('"Wunschwert"'), *map(encrypt_field, SECRETS[2:])),
            strict=True,
        )
    )


class _Stock:
    """Persons with one rectification request each, built like the service stores them."""

    def __init__(self, facility, staff, lead):
        self.facility = facility
        self.staff = staff
        self.lead = lead
        self.reference = {}
        self._persons = 0

    def person(self, facility=None):
        self._persons += 1
        return Client.objects.create(
            facility=facility or self.facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym=f"Person-rr-{self._persons:02d}",
            created_by=self.staff,
        )

    def request(self, client, *, reviewed=True):
        values = _rotated_values()
        fields = {
            "facility_id": client.facility_id,
            "target_client": client,
            "field_path": "notes",
            "old_value": values["old_value"],
            "new_value": values["new_value"],
            "reason": values["reason"],
            "requested_by": self.staff,
        }
        if reviewed:
            fields.update(
                status=RectificationRequest.Status.REJECTED,
                reviewed_by=self.lead,
                reviewed_at=timezone.now(),
                review_note=values["review_note"],
            )
        return RectificationRequest.objects.create(**fields).pk

    def damage(self, pk, columns=COLUMNS):
        """Write re-encrypted values back into *columns* the way the rotation did."""
        self.reference[pk] = _row_state(TABLE, pk)
        values = _rotated_values()
        RectificationRequest.objects.filter(pk=pk).update(**{column: values[column] for column in columns})
        return pk

    def build(self, state, *, facility=None, columns=COLUMNS, reviewed=True):
        """``damaged``: anonymised, then written back; ``cleared``: anonymised; ``live``: never anonymised."""
        client = self.person(facility)
        pk = self.request(client, reviewed=reviewed)
        if state == "live":
            return pk
        anonymize_client(client, user=None)
        return self.damage(pk, columns) if state == "damaged" else pk


@pytest.fixture
def stock(facility, staff_user, lead_user):
    return _Stock(facility, staff_user, lead_user)


@pytest.fixture
def rows(stock):
    """Three requests: damaged, correctly emptied by the anonymisation, of a live person."""
    return {state: stock.build(state) for state in ("damaged", "cleared", "live")}


def _audits():
    return AuditLog.objects.filter(action=AuditLog.Action.DELETE, detail__category=CATEGORY)


def _repair_service():
    from core.retention import redaction_repair

    return redaction_repair.REPAIRS[TARGET]


@pytest.mark.django_db
class TestRepair:
    @pytest.mark.parametrize("columns", [COLUMNS, *((column,) for column in COLUMNS)], ids=["all", *COLUMNS])
    def test_damaged_request_gets_back_the_state_of_the_anonymisation(self, stock, facility, columns):
        """The rotation wrote back only the columns that carried a value; any
        of them left non-empty is damage. The repaired row equals the row the
        anonymisation produced — byte for byte, status, reviewer and
        timestamps included."""
        pk = stock.build("damaged", columns=columns)
        audits = AuditLog.objects.count()

        output = _run("--target", TARGET, "--apply")

        repaired = _row_state(TABLE, pk)
        assert repaired == stock.reference[pk]
        assert all(repaired[column] is None for column in COLUMNS)
        assert AuditLog.objects.count() == audits + 1
        (proof,) = _audits()
        assert proof.facility_id == facility.pk
        assert proof.target_type == "RectificationRequest"
        assert proof.target_id == str(pk)
        assert proof.detail["count"] == 1
        assert proof.detail["command"] == "repair_eventhistory_redaction"
        line = _line(output, TARGET, facility)
        assert (_count(line, "found"), _count(line, "repaired"), _count(line, "skipped")) == (1, 1, 0)

    def test_cleared_request_and_request_of_a_live_person_stay_byte_identical(self, rows, facility):
        before = _table_snapshot(TABLE)

        line = _line(_run("--target", TARGET, "--apply"), TARGET, facility)

        after = _table_snapshot(TABLE)
        assert after[str(rows["cleared"])] == before[str(rows["cleared"])]
        assert after[str(rows["live"])] == before[str(rows["live"])]
        assert all(_row_state(TABLE, rows["live"])[column] is not None for column in COLUMNS)
        assert after[str(rows["damaged"])] != before[str(rows["damaged"])]
        assert (_count(line, "found"), _count(line, "repaired")) == (1, 1)
        assert _audits().count() == 1

    def test_pending_request_without_review_note_is_repaired(self, stock, facility):
        pk = stock.build("damaged", reviewed=False, columns=("old_value", "new_value", "reason"))

        _run("--target", TARGET, "--apply")

        assert _row_state(TABLE, pk) == stock.reference[pk]
        assert _row_state(TABLE, pk)["status"] == RectificationRequest.Status.PENDING

    def test_second_run_finds_nothing_and_writes_nothing(self, rows, facility):
        _run("--target", TARGET, "--apply")
        before = _table_snapshot(TABLE)
        audits = AuditLog.objects.count()

        line = _line(_run("--target", TARGET, "--apply"), TARGET, facility)

        assert (_count(line, "found"), _count(line, "repaired")) == (0, 0)
        assert _table_snapshot(TABLE) == before
        assert AuditLog.objects.count() == audits

    def test_facility_option_repairs_only_that_facility(self, stock, facility):
        other = Facility.objects.create(organization=facility.organization, name="Zweitstelle")
        own = stock.build("damaged")
        foreign = stock.build("damaged", facility=other)
        before = _table_snapshot(TABLE)

        output = _run("--target", TARGET, "--apply", "--facility", other.name)

        assert _count(_line(output, TARGET, other), "repaired") == 1
        assert f"Facility '{facility.name}'" not in output
        assert _table_snapshot(TABLE)[str(own)] == before[str(own)]
        assert _row_state(TABLE, foreign) == stock.reference[foreign]
        (proof,) = _audits()
        assert proof.facility_id == other.pk


@pytest.mark.django_db
class TestDryRun:
    @pytest.mark.parametrize("args", [(), ("--dry-run",)], ids=["default", "dry-run"])
    def test_reports_what_apply_then_repairs_and_writes_nothing(self, stock, rows, facility, args):
        stock.build("damaged", columns=("review_note",))
        before = _table_snapshot(TABLE)
        audits = AuditLog.objects.count()

        preview = _run("--target", TARGET, *args)

        line = _line(preview, TARGET, facility)
        assert (_count(line, "found"), _count(line, "deferred (legal hold)")) == (2, 0)
        assert "Dry run — nothing changed" in preview
        assert _table_snapshot(TABLE) == before
        assert AuditLog.objects.count() == audits

        applied = _line(_run("--target", TARGET, "--apply"), TARGET, facility)
        assert _count(applied, "repaired") == _count(line, "found")


@pytest.mark.django_db
class TestLegalHold:
    """A hold on the person blocks its anonymisation — and this repair as well."""

    def test_request_of_a_held_person_is_counted_but_left_untouched(self, stock, rows, facility):
        client_id = RectificationRequest.objects.get(pk=rows["damaged"]).target_client_id
        LegalHold.objects.create(
            facility=facility, target_type="Client", target_id=client_id, reason="Verfahren", created_by=stock.staff
        )
        before = _table_snapshot(TABLE)
        audits = AuditLog.objects.count()

        preview = _line(_run("--target", TARGET), TARGET, facility)
        applied = _line(_run("--target", TARGET, "--apply"), TARGET, facility)

        assert (_count(preview, "found"), _count(preview, "deferred (legal hold)")) == (1, 1)
        assert (_count(applied, "found"), _count(applied, "repaired")) == (1, 0)
        assert _count(applied, "deferred (legal hold)") == 1
        assert _table_snapshot(TABLE) == before
        assert AuditLog.objects.count() == audits


@pytest.mark.django_db
class TestLocksAndRaces:
    def test_request_cleared_between_detection_and_lock_is_left_alone(self, rows, facility):
        """Detection runs without locks; only what is still damaged under the
        lock is repaired, and no audit entry claims a repair that did not happen."""
        pk = rows["damaged"]

        def regular_anonymisation():
            RectificationRequest.objects.filter(pk=pk).update(**dict.fromkeys(COLUMNS))

        with mitschnitt() as recorded:
            recorded.nach_sperre(TABLE, regular_anonymisation)
            output = _run("--target", TARGET, "--apply")

        assert recorded.offene_schranken() == []
        assert all(_row_state(TABLE, pk)[column] is None for column in COLUMNS)
        assert _audits().count() == 0
        line = _line(output, TARGET, facility)
        assert (_count(line, "repaired"), _count(line, "skipped")) == (0, 1)

    def test_lock_order_person_case_event_request_before_the_first_audit(self, stock, facility, doc_type_contact):
        """The chain of the anonymisation: person, its cases, its events —
        then the request."""
        client = stock.person()
        Case.objects.create(
            facility=facility, client=client, title="Fall", status=Case.Status.OPEN, created_by=stock.staff
        )
        create_event(
            facility=facility,
            user=stock.staff,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
            client=client,
        )
        pk = stock.request(client)
        anonymize_client(client, user=None)
        stock.damage(pk)
        audit_positions = []

        with mitschnitt() as recorded:

            def on_audit(sender, instance, created, **kwargs):
                if created:
                    audit_positions.append(len(recorded))

            post_save.connect(on_audit, sender=AuditLog, weak=False)
            try:
                _run("--target", TARGET, "--apply")
            finally:
                post_save.disconnect(on_audit, sender=AuditLog)

        chain = ("core_client", "core_case", "core_event", TABLE)
        assert [table for table in recorded.sperrfolge() if table in chain] == list(chain)
        lock_positions = [i for i, query in enumerate(recorded) if any(query.sperrt(t) for t in chain)]
        assert audit_positions, "no audit entry written"
        assert max(lock_positions) < min(audit_positions)


@pytest.mark.django_db
class TestOutput:
    def test_counts_and_facility_names_only(self, rows, facility):
        for args in ((), ("--apply",)):
            output = _run("--target", TARGET, *args)
            assert "gAAAA" not in output
            assert "__encrypted__" not in output
            for secret in SECRETS:
                assert secret not in output
            for pk in rows.values():
                assert str(pk) not in output
            assert _line(output, TARGET, facility)


@pytest.mark.django_db
class TestTargetSelection:
    @pytest.mark.parametrize(
        "args", [(), ("--target", "client"), ("--target", "event")], ids=["default", "client", "event"]
    )
    def test_other_calls_leave_rectification_requests_alone(self, rows, args):
        before = _table_snapshot(TABLE)

        output = _run(*args, "--apply")

        assert "[rectification]" not in output
        assert _table_snapshot(TABLE) == before
        assert _audits().count() == 0

    def test_all_runs_the_requests_right_after_the_persons(self, rows, facility):
        output = _run("--target", "all", "--apply")

        totals = [line.split("]")[0] + "]" for line in output.splitlines() if "] Total:" in line]
        assert totals == ["[client]", "[rectification]", "[case]", "[event]", "[eventhistory]"]
        assert _count(_line(output, TARGET, facility), "repaired") == 1
        assert _audits().count() == 1


def _snapshots():
    return {table: _table_snapshot(table) for table in WATCHED_TABLES}


def _refusing_context(role, facility, via):
    if via == "no_bypass":
        return as_rls_role(role, facility_id=facility.pk)
    return _non_owner_with_rls_bypass(role, facility, via)


@pytest.mark.django_db
class TestWithoutTableOwnership:
    """Only the owner of the tables may repair the requests.

    Under row level security the app role sees no row of another facility —
    detection would report a clean stock that is not. The session variable
    ``app.is_super_admin`` (which any role may set) or a BYPASSRLS role lets a
    connection read every row, but that is still not the maintenance role.
    Every form refuses before it reads a single request or person.
    """

    @pytest.mark.parametrize("args", [(), ("--apply",)], ids=["dry-run", "apply"])
    @pytest.mark.parametrize("via", ["no_bypass", "session_variable", "bypassrls"])
    def test_command_refuses_before_reading(self, rows, facility, rls_test_role, args, via):  # noqa: F811
        before = _snapshots()
        audits = AuditLog.objects.count()
        expected = "bypass" if via == "no_bypass" else f"does not own {TABLE}"

        with _refusing_context(rls_test_role, facility, via):
            with mitschnitt() as recorded:
                with pytest.raises(CommandError, match=expected):
                    _run("--target", TARGET, *args)

        assert not _reads_any(recorded, WATCHED_TABLES)
        assert _snapshots() == before
        assert AuditLog.objects.count() == audits

    @pytest.mark.parametrize("dry_run", [True, False], ids=["dry-run", "apply"])
    @pytest.mark.parametrize("via", ["no_bypass", "session_variable", "bypassrls"])
    def test_service_refuses_before_reading(self, rows, facility, rls_test_role, dry_run, via):  # noqa: F811
        from core.retention.eventhistory import OwnerContextMissingError

        repair = _repair_service()
        before = _snapshots()
        audits = AuditLog.objects.count()

        with _refusing_context(rls_test_role, facility, via):
            with mitschnitt() as recorded:
                with pytest.raises(OwnerContextMissingError):
                    repair(facility, dry_run=dry_run)

        assert not _reads_any(recorded, (TABLE, "core_client"))
        assert _snapshots() == before
        assert AuditLog.objects.count() == audits

    def _own(self, table, role):
        with connection.cursor() as cur:
            # Deferred foreign-key checks of the setup must fire before the DDL.
            cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cur.execute(f"ALTER TABLE {table} OWNER TO {role}")

    @pytest.mark.parametrize("dry_run", [True, False], ids=["dry-run", "apply"])
    def test_owning_the_requests_but_not_the_persons_is_not_enough(
        self,
        rows,
        facility,
        rls_test_role,  # noqa: F811
        dry_run,
    ):
        """Detection reads the persons' anonymisation state; a connection that
        owns the requests but not the persons refuses as well."""
        from core.retention.eventhistory import OwnerContextMissingError

        self._own(TABLE, rls_test_role)
        before = _snapshots()
        args = () if dry_run else ("--apply",)

        with _non_owner_with_rls_bypass(rls_test_role, facility, "bypassrls"):
            with mitschnitt() as recorded:
                with pytest.raises(CommandError, match="does not own core_client"):
                    _run("--target", TARGET, *args)
                with pytest.raises(OwnerContextMissingError, match="core_client"):
                    _repair_service()(facility, dry_run=dry_run)

        assert not _reads_any(recorded, (TABLE, "core_client"))
        assert _snapshots() == before

    def test_owner_without_bypass_refuses_before_reading(self, rows, facility, rls_test_role):  # noqa: F811
        """Ownership without a bypass is not enough either: under FORCE ROW
        LEVEL SECURITY the owner sees only its facility and would report the
        others clean. Ownership moves for the test transaction only."""
        from core.retention.eventhistory import OwnerContextMissingError
        from core.services.system import current_connection_owns, has_rls_bypass_context

        self._own(TABLE, rls_test_role)
        self._own("core_client", rls_test_role)
        before = _snapshots()

        with as_rls_role(rls_test_role, facility_id=facility.pk):
            assert current_connection_owns(TABLE)
            assert not has_rls_bypass_context()
            with mitschnitt() as recorded:
                with pytest.raises(OwnerContextMissingError, match="bypass"):
                    _repair_service()(facility, dry_run=True)

        assert not _reads_any(recorded, (TABLE, "core_client"))
        assert _snapshots() == before
