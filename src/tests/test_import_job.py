"""ImportJob-Modell: Statusmaschine, clean()-Guards, Facility-Scoping, RLS (Refs #1060).

M4 Strang A / P2. Der Wizard-State liegt bewusst in der DB (nicht in der
Session): der Import laeuft ueber mehrere Requests, muss abbrechbar sein und
hinterlaesst mit ``result_json`` eine nachvollziehbare, PII-freie Spur.

Der Cross-Tenant-Teil laeuft — wie ``test_rls_functional.py`` — unter einer
NOSUPERUSER-Rolle; nur dort greift die ``facility_isolation``-Policy
tatsaechlich (der Test-DB-User bypassed RLS per Postgres-Default).
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import connection

from core.models import DocumentType, ImportJob
from tests.test_rls_functional import (  # noqa: F401 — pytest-Fixtures sind modul-lokal
    as_rls_role,
    rls_test_role,
)


@pytest.fixture
def import_job_client(facility, admin_user):
    return ImportJob.objects.create(
        facility=facility,
        created_by=admin_user,
        target_type=ImportJob.TargetType.CLIENT,
    )


@pytest.mark.django_db
class TestImportJobDefaults:
    def test_new_job_starts_as_uploaded(self, import_job_client):
        assert import_job_client.status == ImportJob.Status.UPLOADED

    def test_json_fields_default_to_empty_dicts(self, import_job_client):
        assert import_job_client.mapping_json == {}
        assert import_job_client.result_json == {}
        assert import_job_client.original_filename_encrypted == {}
        assert import_job_client.storage_filename == ""

    def test_status_choices_cover_the_documented_state_machine(self):
        assert set(ImportJob.Status.values) == {
            "uploaded",
            "mapped",
            "validated",
            "committed",
            "aborted",
        }

    def test_target_type_values_match_document_type_entity_types(self):
        """Die Werte sind identisch zu ``DocumentType.EntityType`` — der
        ``clean()``-Guard vergleicht beide direkt (kein Mapping-Dict)."""
        assert ImportJob.TargetType.CLIENT.value == DocumentType.EntityType.CLIENT.value
        assert ImportJob.TargetType.EVENT.value == DocumentType.EntityType.EVENT.value

    def test_ordering_is_newest_first(self, facility, admin_user):
        older = ImportJob.objects.create(facility=facility, target_type=ImportJob.TargetType.CLIENT)
        newer = ImportJob.objects.create(facility=facility, target_type=ImportJob.TargetType.CLIENT)
        assert list(ImportJob.objects.for_facility(facility)) == [newer, older]


@pytest.mark.django_db
class TestImportJobCleanGuards:
    def test_foreign_document_type_is_rejected(self, facility, second_facility, admin_user):
        foreign_type = DocumentType.objects.create(
            facility=second_facility,
            entity_type=DocumentType.EntityType.CLIENT,
            name="Fremdprofil",
        )
        job = ImportJob(
            facility=facility,
            created_by=admin_user,
            target_type=ImportJob.TargetType.CLIENT,
            document_type=foreign_type,
        )
        with pytest.raises(ValidationError) as exc:
            job.clean()
        assert "document_type" in exc.value.message_dict

    def test_entity_type_must_match_target_type(self, facility, admin_user, doc_type_client_profile):
        job = ImportJob(
            facility=facility,
            created_by=admin_user,
            target_type=ImportJob.TargetType.EVENT,
            document_type=doc_type_client_profile,
        )
        with pytest.raises(ValidationError) as exc:
            job.clean()
        assert "document_type" in exc.value.message_dict

    def test_event_target_requires_a_document_type(self, facility, admin_user):
        """Ohne Typ gibt es fuer ein Ereignis weder Feldmenge noch
        ``create_event``-Aufruf — der Job waere unbenutzbar."""
        job = ImportJob(
            facility=facility,
            created_by=admin_user,
            target_type=ImportJob.TargetType.EVENT,
        )
        with pytest.raises(ValidationError) as exc:
            job.clean()
        assert "document_type" in exc.value.message_dict

    def test_client_target_without_document_type_is_allowed(self, facility, admin_user):
        job = ImportJob(
            facility=facility,
            created_by=admin_user,
            target_type=ImportJob.TargetType.CLIENT,
        )
        job.clean()  # kein Fehler: Stammdaten-Import ohne Zusatzfelder

    def test_matching_document_type_is_accepted(self, facility, admin_user, doc_type_client_profile):
        job = ImportJob(
            facility=facility,
            created_by=admin_user,
            target_type=ImportJob.TargetType.CLIENT,
            document_type=doc_type_client_profile,
        )
        job.clean()

    def test_matching_event_document_type_is_accepted(self, facility, admin_user, doc_type_contact):
        job = ImportJob(
            facility=facility,
            created_by=admin_user,
            target_type=ImportJob.TargetType.EVENT,
            document_type=doc_type_contact,
        )
        job.clean()


@pytest.mark.django_db
class TestImportJobFacilityScoping:
    def test_manager_scopes_to_facility(self, facility, second_facility, import_job_client):
        foreign = ImportJob.objects.create(facility=second_facility, target_type=ImportJob.TargetType.CLIENT)
        assert list(ImportJob.objects.for_facility(facility)) == [import_job_client]
        assert list(ImportJob.objects.for_facility(second_facility)) == [foreign]


@pytest.mark.django_db
@pytest.mark.postgres
class TestImportJobRLSPolicy:
    def test_policy_and_force_flags_exist(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'core_importjob'")
            enabled, forced = cursor.fetchone()
            cursor.execute(
                "SELECT qual FROM pg_policies WHERE tablename = 'core_importjob' AND policyname = 'facility_isolation'"
            )
            (qual,) = cursor.fetchone()
        assert enabled and forced
        assert "app.current_facility_id" in qual
        assert "app.is_super_admin" in qual, "Superadmin-Bypass-Branch fehlt (test_rls.py wuerde reissen)"


@pytest.mark.django_db(transaction=True)
class TestImportJobCrossTenantIsolation:
    """Fremde Facility sieht die Job-Zeile nicht und kann sie nicht aendern."""

    def test_cross_tenant_select_returns_zero_rows(self, rls_test_role, facility, second_facility, admin_user):  # noqa: F811
        ImportJob.objects.create(
            facility=second_facility,
            created_by=admin_user,
            target_type=ImportJob.TargetType.CLIENT,
            storage_filename="fremd.enc",
        )
        with as_rls_role(rls_test_role, facility_id=facility.pk) as cur:
            cur.execute("SELECT id FROM core_importjob WHERE storage_filename = 'fremd.enc'")
            assert cur.fetchall() == []

    def test_cross_tenant_update_touches_no_rows(self, rls_test_role, facility, second_facility, admin_user):  # noqa: F811
        job = ImportJob.objects.create(
            facility=second_facility,
            created_by=admin_user,
            target_type=ImportJob.TargetType.CLIENT,
            storage_filename="fremd.enc",
        )
        with as_rls_role(rls_test_role, facility_id=facility.pk) as cur:
            cur.execute("UPDATE core_importjob SET status = 'aborted' WHERE id = %s", [str(job.pk)])
            assert cur.rowcount == 0
        job.refresh_from_db()
        assert job.status == ImportJob.Status.UPLOADED
