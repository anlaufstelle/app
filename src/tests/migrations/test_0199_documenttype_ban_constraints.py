"""Forward and backward run of ``0199_documenttype_ban_constraints``.

The migration adds two CHECK constraints and no data step: a ban document type
needs a minimum contact stage and may only sit on event types. Rows that obey the
rule survive both directions; the constraints exist only after the forward run.
"""

import pytest
from django.db import IntegrityError, connection, transaction
from django_test_migrations.contrib.unittest_case import MigratorTestCase

CONSTRAINTS = {"documenttype_ban_requires_contact_stage", "documenttype_ban_only_on_events"}


def _present_constraints() -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT conname FROM pg_constraint WHERE conname = ANY(%s)", [sorted(CONSTRAINTS)])
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

    def test_rows_survive_the_backward_run(self):
        DocumentType = self.new_state.apps.get_model("core", "DocumentType")
        assert DocumentType.objects.filter(pk__in=[self.ban_pk, self.plain_pk]).count() == 2
