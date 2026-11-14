"""Forward and backward run of ``0199_documenttype_ban_constraints``.

The migration adds two CHECK constraints and three triggers and no data step: a
ban document type needs a minimum contact stage and may only sit on event types,
and a ban event names a person. Rows that obey the rules survive both
directions; constraints, triggers and trigger functions exist only after the
forward run.
"""

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from django_test_migrations.contrib.unittest_case import MigratorTestCase

CONSTRAINTS = {"documenttype_ban_requires_contact_stage", "documenttype_ban_only_on_events"}
# Trigger name -> table, taken from the models' ``_meta.db_table``.
TRIGGERS = {
    "event_ban_requires_person_insert": "core_event",
    "event_ban_requires_person_update": "core_event",
    "documenttype_ban_without_personless_events": "core_documenttype",
}
FUNCTIONS = {"event_ban_requires_person", "documenttype_ban_rejects_personless_events"}


def _present_constraints() -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT conname FROM pg_constraint WHERE conname = ANY(%s)", [sorted(CONSTRAINTS)])
        return {row[0] for row in cursor.fetchall()}


def _present_triggers() -> dict[str, tuple[str, str]]:
    """Trigger name -> (table, ``tgenabled``) for the triggers of ``0199``."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT tgname, tgrelid::regclass::text, tgenabled FROM pg_trigger WHERE tgname = ANY(%s)",
            [sorted(TRIGGERS)],
        )
        return {name: (table, enabled) for name, table, enabled in cursor.fetchall()}


def _present_functions() -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT proname FROM pg_proc WHERE proname = ANY(%s)", [sorted(FUNCTIONS)])
        return {row[0] for row in cursor.fetchall()}


def _make_types(apps):
    Organization = apps.get_model("core", "Organization")
    Facility = apps.get_model("core", "Facility")
    DocumentType = apps.get_model("core", "DocumentType")
    facility = Facility.objects.create(organization=Organization.objects.create(name="Mig-0199-Org"), name="Mig-0199")
    ban = DocumentType.objects.create(
        facility=facility, name="Ban", slug="ban", system_type="ban", min_contact_stage="identified"
    )
    plain = DocumentType.objects.create(facility=facility, name="Plain", slug="plain")
    return facility.pk, ban.pk, plain.pk


class TestMigrate0199Forward(MigratorTestCase):
    migrate_from = ("core", "0197_drop_statistics_event_flat_view")
    migrate_to = ("core", "0199_documenttype_ban_constraints")

    def prepare(self):
        self.facility_pk, self.ban_pk, self.plain_pk = _make_types(self.old_state.apps)

    def test_constraints_exist_after_the_forward_run(self):
        assert _present_constraints() == CONSTRAINTS

    def test_triggers_exist_and_always_fire_after_the_forward_run(self):
        assert _present_triggers() == {name: (table, "A") for name, table in TRIGGERS.items()}
        assert _present_functions() == FUNCTIONS

    def test_the_trigger_tables_are_the_model_tables(self):
        from core.models import DocumentType, Event

        assert set(TRIGGERS.values()) == {Event._meta.db_table, DocumentType._meta.db_table}

    def test_a_personless_ban_event_is_rejected_by_the_database(self):
        Event = self.new_state.apps.get_model("core", "Event")
        with pytest.raises(IntegrityError, match="event_ban_requires_person"), transaction.atomic():
            Event.objects.create(
                facility_id=self.facility_pk, document_type_id=self.ban_pk, occurred_at=timezone.now(), data_json={}
            )

    def test_a_personless_event_of_another_type_is_accepted(self):
        Event = self.new_state.apps.get_model("core", "Event")
        Event.objects.create(
            facility_id=self.facility_pk, document_type_id=self.plain_pk, occurred_at=timezone.now(), data_json={}
        )
        assert Event.objects.filter(document_type_id=self.plain_pk).count() == 1

    def test_valid_rows_survive_unchanged(self):
        DocumentType = self.new_state.apps.get_model("core", "DocumentType")
        assert DocumentType.objects.get(pk=self.ban_pk).min_contact_stage == "identified"
        assert DocumentType.objects.get(pk=self.plain_pk).system_type is None

    def test_a_stageless_ban_is_rejected_by_the_database(self):
        DocumentType = self.new_state.apps.get_model("core", "DocumentType")
        with pytest.raises(IntegrityError), transaction.atomic():
            DocumentType.objects.create(facility_id=self.facility_pk, name="Bad", slug="bad", system_type="ban")

    def test_a_ban_on_a_client_type_is_rejected_by_the_database(self):
        DocumentType = self.new_state.apps.get_model("core", "DocumentType")
        with pytest.raises(IntegrityError), transaction.atomic():
            DocumentType.objects.create(
                facility_id=self.facility_pk,
                name="Bad client",
                slug="bad-client",
                system_type="ban",
                min_contact_stage="identified",
                entity_type="client",
            )


class TestMigrate0199Backward(MigratorTestCase):
    migrate_from = ("core", "0199_documenttype_ban_constraints")
    migrate_to = ("core", "0197_drop_statistics_event_flat_view")

    def prepare(self):
        self.facility_pk, self.ban_pk, self.plain_pk = _make_types(self.old_state.apps)

    def test_constraints_are_gone_after_the_backward_run(self):
        assert _present_constraints() == set()

    def test_triggers_and_their_functions_are_gone_after_the_backward_run(self):
        assert _present_triggers() == {}
        assert _present_functions() == set()

    def test_rows_survive_the_backward_run(self):
        DocumentType = self.new_state.apps.get_model("core", "DocumentType")
        assert DocumentType.objects.filter(pk__in=[self.ban_pk, self.plain_pk]).count() == 2
