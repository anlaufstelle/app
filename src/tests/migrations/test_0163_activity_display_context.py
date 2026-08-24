"""Backfill/drop coverage for Activity cleartext removal (#1972)."""

import uuid

from django.db import connection
from django_test_migrations.contrib.unittest_case import MigratorTestCase


class TestMigrate0163ActivityDisplayContext(MigratorTestCase):
    migrate_from = ("core", "0162_alter_documenttype_report_key_and_more")
    migrate_to = ("core", "0163_activity_display_context")

    def prepare(self):
        apps = self.old_state.apps
        Organization = apps.get_model("core", "Organization")
        Facility = apps.get_model("core", "Facility")
        Activity = apps.get_model("core", "Activity")
        ContentType = apps.get_model("contenttypes", "ContentType")

        organization = Organization.objects.create(name="Mig-0163-Org")
        facility = Facility.objects.create(organization=organization, name="Mig-0163-Stelle")
        target_type, _created = ContentType.objects.get_or_create(app_label="core", model="client")
        self.activity_pk = Activity.objects.create(
            facility=facility,
            actor=None,
            verb="created",
            target_type=target_type,
            target_id=uuid.uuid4(),
            summary="Person MIG-0163-Klartext angelegt",
        ).pk

    def test_legacy_text_is_replaced_by_a_pii_free_marker(self):
        Activity = self.new_state.apps.get_model("core", "Activity")
        row = Activity.objects.get(pk=self.activity_pk)

        assert row.context == {"schema": 1, "action": "legacy_redacted"}
        assert "summary" not in {field.name for field in Activity._meta.get_fields()}

    def test_summary_column_is_physically_removed(self):
        with connection.cursor() as cursor:
            columns = {
                column.name for column in connection.introspection.get_table_description(cursor, "core_activity")
            }

        assert "summary" not in columns
        assert "context" in columns
