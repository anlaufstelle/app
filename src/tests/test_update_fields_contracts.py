"""Lost-Update-Verträge für eingeschränkte Model-Saves (Refs #2158)."""

from contextlib import contextmanager

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.signals import post_save
from django.utils import timezone

from core.models import Client, Event, ImportJob
from core.models.retention import RetentionProposal
from core.services import import_engine
from core.services.client import main as client_main
from core.services.client.merge import merge_clients
from core.services.security.credentials import RevocationReason, revoke_credentials
from tests._factories import make_event

pytestmark = pytest.mark.django_db


@pytest.fixture
def source(facility, staff_user):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Update-Fields-Quelle",
        created_by=staff_user,
    )


@pytest.fixture
def target(facility, staff_user):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Update-Fields-Ziel",
        created_by=staff_user,
    )


@contextmanager
def _captured_update_fields(model):
    """Sammelt Djangos normalisierte ``update_fields``-Menge je Teil-Save."""
    seen = []

    def _receiver(sender, instance, update_fields=None, **kwargs):  # noqa: ARG001
        seen.append(update_fields)

    post_save.connect(_receiver, sender=model, weak=False)
    try:
        yield seen
    finally:
        post_save.disconnect(_receiver, sender=model)


def test_revoke_credentials_only_saves_the_forced_change_flag(staff_user):
    staff_user.must_change_password = True
    staff_user.save(update_fields=["must_change_password"])

    with _captured_update_fields(type(staff_user)) as seen:
        revoke_credentials(staff_user, reason=RevocationReason.PASSWORD_CHANGE)

    assert seen == [frozenset({"must_change_password"})]


def test_apply_mapping_only_saves_mapping_status_and_timestamp(facility, admin_user, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    client_job = import_engine.store_import_upload(
        facility,
        admin_user,
        SimpleUploadedFile(
            "update-fields.csv",
            b"Pseudonym;Kontaktstufe;Altersgruppe;Notizen\nA;;;\n",
            content_type="text/csv",
        ),
        target_type=ImportJob.TargetType.CLIENT,
    )
    mapping = {
        "Pseudonym": "pseudonym",
        "Kontaktstufe": "contact_stage",
        "Altersgruppe": "age_cluster",
        "Notizen": "notes",
    }

    with _captured_update_fields(ImportJob) as seen:
        import_engine.apply_mapping(client_job, mapping)

    assert seen == [frozenset({"mapping_json", "status", "updated_at"})]


def test_merge_saves_each_client_with_only_its_owned_columns(source, target, lead_user, doc_type_client_profile):
    source.document_type = doc_type_client_profile
    source.save(update_fields=["document_type"])

    with _captured_update_fields(Client) as seen:
        merge_clients(source, target, lead_user)

    assert seen == [
        frozenset({"document_type", "data_json", "updated_at"}),
        frozenset({"merged_into"}),
        frozenset({"is_deleted", "deleted_at", "deleted_by"}),
    ]


def test_redact_client_identity_limits_the_client_save(client_identified):
    with _captured_update_fields(Client) as seen:
        client_main._redact_client_identity(client_identified)

    assert seen == [
        frozenset(
            {
                "pseudonym",
                "notes",
                "age_cluster",
                "contact_stage",
                "is_active",
                "data_json",
                "document_type",
                "anonymized_at",
            }
        )
    ]


def test_redact_live_events_limits_each_event_save(facility, client_identified, staff_user, doc_type_contact):
    make_event(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={"notiz": "PII"},
        created_by=staff_user,
    )

    with _captured_update_fields(Event) as seen:
        client_main._redact_live_events(client_identified)

    assert seen == [frozenset({"data_json", "search_text", "updated_at"})]


def test_redact_retention_proposals_limits_each_proposal_save(
    facility, client_identified, staff_user, doc_type_contact
):
    event = make_event(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
    )
    RetentionProposal.objects.create(
        facility=facility,
        target_type=RetentionProposal.TargetType.EVENT,
        target_id=event.pk,
        deletion_due_at=timezone.localdate(),
        details={"pseudonym": client_identified.pseudonym},
        retention_category="identified",
    )

    with _captured_update_fields(RetentionProposal) as seen:
        client_main._redact_retention_proposals([event.pk])

    assert seen == [frozenset({"details"})]
