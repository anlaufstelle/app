"""Pins for event attachment purges and person activity timestamps.

Source: mutation delta of the release-0.99.7 checkpoint, 2 mutants.
Groups: deletion-only batches avoid insertion FK locks (1), event attachment
purges preserve the person's activity timestamp (1).
"""

from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from core.models import Attachment, Client, FieldTemplate
from core.services.attachments import purge_attachment_chain
from core.services.attachments.crud import lock_batch_attachment_rows
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db


@pytest.mark.postgres
def test_deletion_only_batch_does_not_key_share_lock_the_actor(sample_event, facility, staff_user):
    """A deletion-only batch does not acquire insertion FK locks on its actor.

    Kills x_lock_batch_attachment_rows__mutmut_17 through lock_batch_attachment_rows.
    Lock strengths and target keys are observed, with no statement counts.
    """
    field = FieldTemplate.objects.create(
        facility=facility, name="Removed attachment", field_type=FieldTemplate.FieldType.FILE
    )
    attachment = Attachment.objects.create(
        facility=facility,
        event=sample_event,
        field_template=field,
        storage_filename="removed.enc",
        original_filename_encrypted={},
        file_size=1,
        mime_type="application/pdf",
        created_by=staff_user,
    )
    with transaction.atomic(), mitschnitt() as queries:
        lock_batch_attachment_rows(sample_event, [attachment.entry_id], user=staff_user)
    assert not any(
        lock.sperrart == "KEY SHARE" and lock.traegt(id=[staff_user.pk]) for lock in queries.sperren("core_user")
    )


def test_event_attachment_purge_preserves_person_activity(sample_event, facility, client_identified, staff_user):
    """Purging an event attachment leaves the person's activity timestamp intact.

    Kills x_purge_attachment_chain__mutmut_51 through purge_attachment_chain.
    Only attachment rows are needed: on_commit file deletion is left queued.
    """
    field = FieldTemplate.objects.create(
        facility=facility, name="Event attachment", field_type=FieldTemplate.FieldType.FILE
    )
    attachment = Attachment.objects.create(
        facility=facility,
        event=sample_event,
        field_template=field,
        storage_filename="unused.enc",
        original_filename_encrypted={},
        file_size=1,
        mime_type="application/pdf",
        created_by=staff_user,
    )
    original = timezone.now() - timedelta(days=30)
    Client.objects.filter(pk=client_identified.pk).update(updated_at=original)

    assert purge_attachment_chain(sample_event, attachment.entry_id) == 1

    client_identified.refresh_from_db()
    assert client_identified.updated_at == original
    assert not Attachment.objects.filter(pk=attachment.pk).exists()
