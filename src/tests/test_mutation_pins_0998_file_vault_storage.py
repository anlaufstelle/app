"""Pins for replacement positions and diagnostic storage failures.

Source: mutation delta of the release-0.99.8 checkpoint, 3 mutants.
Groups: explicit replacement sort order (1), strict deletion message (1), invalid owner diagnostic (1).
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.models import Attachment, FieldTemplate
from core.services.file_vault import storage

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


@pytest.fixture
def stored_attachment(client_identified, staff_user, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    field = FieldTemplate.objects.create(
        facility=client_identified.facility,
        name="Attachment",
        field_type=FieldTemplate.FieldType.FILE,
    )
    attachment = Attachment.objects.create(
        facility=client_identified.facility,
        client=client_identified,
        field_template=field,
        storage_filename="original.enc",
        original_filename_encrypted=storage.encrypt_field("original.pdf"),
        file_size=4,
        mime_type="application/pdf",
        created_by=staff_user,
        sort_order=7,
    )
    path = storage.get_attachment_path(attachment)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"encrypted contents")
    return attachment


def test_explicit_sort_order_overrides_the_replaced_position(stored_attachment, staff_user):
    """Replacement respects a caller's new position: x__commit_staged_upload__mutmut_26."""
    previous = stored_attachment
    path = storage.get_attachment_path(previous).with_name("replacement.enc")
    path.write_bytes(b"new encrypted contents")
    staged = storage.StagedUpload(
        storage_name=path.name,
        output_path=path,
        detected_mime="application/pdf",
        file_size=22,
        original_name="replacement.pdf",
        content_type="application/pdf",
    )

    replacement = storage.commit_staged_upload(
        previous.client,
        previous.field_template,
        staged,
        staff_user,
        supersedes=previous,
        sort_order=2,
    )

    replacement.refresh_from_db()
    previous.refresh_from_db()
    assert replacement.sort_order == 2
    assert previous.sort_order == 7
    assert replacement.entry_id == previous.entry_id
    assert previous.superseded_by_id == replacement.pk and not previous.is_current


def test_strict_file_deletion_preserves_message_and_cause(stored_attachment, monkeypatch):
    """Strict deletion explains a retryable filesystem failure: x_delete_attachment_file__mutmut_7."""
    cause = OSError("Injected unlink failure")
    path = storage.get_attachment_path(stored_attachment)
    original = Path.unlink

    def fail_target(self, *args, **kwargs):
        if self == path:
            raise cause
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_target)
    with pytest.raises(storage.AttachmentFileDeletionError) as error:
        storage.delete_attachment_file(stored_attachment, raise_on_error=True)

    assert str(error.value) == "Attachment file deletion failed"
    assert error.value.__cause__ is cause
    assert path.exists()


def test_owner_without_facility_has_a_diagnostic_value_error(facility, staff_user):
    """Invalid owners fail before upload processing: x_store_encrypted_file__mutmut_20."""
    with pytest.raises(ValueError) as error:
        storage.store_encrypted_file(facility, None, None, SimpleNamespace(), staff_user)

    assert str(error.value) == (
        "Facility des Owners weicht von der uebergebenen Facility ab "
        f"(owner.facility_id=None, facility={facility.pk!r})"
    )
