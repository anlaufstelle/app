"""Audit-Abdeckung an den File-Vault-Choke-Points (Refs #1960)."""

from __future__ import annotations

import ast
import inspect
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, DocumentType, DocumentTypeField, Event, FieldTemplate
from core.models.attachment import Attachment
from core.seed import attachments as seed_attachments
from core.services.attachments import discard_type_switch_file_chains, replace_upload_key
from core.services.audit import hmac_hash_value
from core.services.file_vault import filename_audit_fields, filename_fingerprint
from core.services.file_vault.storage import StagedUpload, commit_staged_upload, soft_delete_attachment_chain

PDF_HEADER = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


@pytest.fixture
def file_template(facility):
    return FieldTemplate.objects.create(
        facility=facility,
        name="Audit-Anhang",
        field_type=FieldTemplate.FieldType.FILE,
    )


@pytest.fixture
def document_type(facility, file_template):
    document_type = DocumentType.objects.create(facility=facility, name="Audit-Dateityp")
    DocumentTypeField.objects.create(document_type=document_type, field_template=file_template, sort_order=0)
    return document_type


def _staged(tmp_path, *, name="Bericht-Klartext-Person.pdf") -> StagedUpload:
    output_path = tmp_path / "staged.enc"
    output_path.write_bytes(b"encrypted")
    return StagedUpload(
        storage_name="staged.enc",
        output_path=output_path,
        detected_mime="application/pdf",
        file_size=9,
        original_name=name,
        content_type="application/pdf",
    )


def _pdf(name: str, marker: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, PDF_HEADER + marker, content_type="application/pdf")


def test_public_filename_fingerprint_keeps_existing_violation_semantics():
    filename = "Bericht-Klartext-Person.PDF"

    assert (
        filename_fingerprint(filename)
        == filename_audit_fields(filename)
        == {
            "filename_hash": hmac_hash_value(filename),
            "filename_ext": "pdf",
        }
    )


def test_seed_uploads_bypass_audit_only_via_explicit_false():
    """Der freigegebene Seed-Pfad dokumentiert jeden Audit-Bypass am Aufruf."""
    tree = ast.parse(inspect.getsource(seed_attachments))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "store_encrypted_file"
    ]

    assert len(calls) == 2
    for call in calls:
        audit_keywords = [keyword for keyword in call.keywords if keyword.arg == "audit"]
        assert len(audit_keywords) == 1
        assert isinstance(audit_keywords[0].value, ast.Constant)
        assert audit_keywords[0].value.value is False


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestUploadAudit:
    def test_add_writes_owner_scoped_pii_free_detail(self, tmp_path, sample_event, file_template, staff_user):
        staged = _staged(tmp_path)
        attachment = commit_staged_upload(sample_event, file_template, staged, staff_user)

        entry = AuditLog.objects.get(action=AuditLog.Action.ATTACHMENT_UPLOAD)
        assert entry.target_type == "Attachment"
        assert entry.target_id == str(sample_event.pk)
        assert entry.user == staff_user
        assert entry.detail == {
            "owner_type": "event",
            "owner_id": str(sample_event.pk),
            "field": file_template.slug,
            "attachment_id": str(attachment.pk),
            "entry_id": str(attachment.entry_id),
            "mode": "add",
            "filename_hash": hmac_hash_value(staged.original_name),
            "filename_ext": "pdf",
            "file_size": 9,
            "mime": "application/pdf",
        }
        assert staged.original_name not in str(entry.detail)

    def test_replace_writes_replace_mode_and_keeps_entry_id(self, tmp_path, sample_event, file_template, staff_user):
        first = commit_staged_upload(sample_event, file_template, _staged(tmp_path), staff_user)
        replacement = _staged(tmp_path, name="Ersatz-Klartext-Person.pdf")
        second = commit_staged_upload(
            sample_event,
            file_template,
            replacement,
            staff_user,
            supersedes=first,
        )

        entries = AuditLog.objects.filter(action=AuditLog.Action.ATTACHMENT_UPLOAD).order_by("timestamp")
        assert entries.count() == 2
        assert entries[0].detail["mode"] == "add"
        assert entries[1].detail["mode"] == "replace"
        assert entries[1].detail["entry_id"] == str(first.entry_id)
        assert entries[1].detail["attachment_id"] == str(second.pk)
        assert replacement.original_name not in str(entries[1].detail)

    def test_failed_audit_write_rolls_back_row_and_removes_staged_file(
        self, tmp_path, sample_event, file_template, staff_user
    ):
        staged = _staged(tmp_path)
        with patch("core.services.file_vault.audit.audit_event", side_effect=RuntimeError("audit failed")):
            with pytest.raises(RuntimeError, match="audit failed"):
                commit_staged_upload(sample_event, file_template, staged, staff_user)

        assert not staged.output_path.exists()
        assert not Attachment.objects.filter(event=sample_event).exists()
        assert not AuditLog.objects.filter(action=AuditLog.Action.ATTACHMENT_UPLOAD).exists()


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestSoftDeleteAudit:
    def test_chain_delete_writes_exactly_one_owner_scoped_row_with_version_count(
        self, tmp_path, sample_event, file_template, staff_user
    ):
        first = commit_staged_upload(sample_event, file_template, _staged(tmp_path), staff_user)
        head = commit_staged_upload(
            sample_event,
            file_template,
            _staged(tmp_path, name="Ersatz.pdf"),
            staff_user,
            supersedes=first,
        )

        assert soft_delete_attachment_chain(sample_event, head.entry_id, staff_user) == 2

        entry = AuditLog.objects.get(action=AuditLog.Action.ATTACHMENT_DELETE)
        assert entry.target_type == "Attachment"
        assert entry.target_id == str(sample_event.pk)
        assert entry.detail == {
            "owner_type": "event",
            "owner_id": str(sample_event.pk),
            "entry_id": str(head.entry_id),
            "field": file_template.slug,
            "versions": 2,
        }
        assert "filename" not in str(entry.detail)

    def test_delete_without_match_writes_nothing(self, tmp_path, sample_event, file_template, staff_user):
        attachment = commit_staged_upload(sample_event, file_template, _staged(tmp_path), staff_user)
        assert soft_delete_attachment_chain(sample_event, attachment.entry_id, staff_user) == 1
        assert soft_delete_attachment_chain(sample_event, attachment.entry_id, staff_user) == 0
        assert AuditLog.objects.filter(action=AuditLog.Action.ATTACHMENT_DELETE).count() == 1


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestTypeSwitchBulkAudit:
    def test_type_switch_writes_one_bulk_row_instead_of_one_per_chain(
        self, tmp_path, sample_event, file_template, staff_user
    ):
        first = commit_staged_upload(sample_event, file_template, _staged(tmp_path), staff_user)
        commit_staged_upload(
            sample_event,
            file_template,
            _staged(tmp_path, name="Version.pdf"),
            staff_user,
            supersedes=first,
        )
        commit_staged_upload(
            sample_event,
            file_template,
            _staged(tmp_path, name="Zweite-Kette.pdf"),
            staff_user,
        )

        assert discard_type_switch_file_chains(sample_event, staff_user) == 2

        entries = AuditLog.objects.filter(action=AuditLog.Action.ATTACHMENT_DELETE)
        assert entries.count() == 1
        entry = entries.get()
        assert entry.target_type == "Attachment"
        assert entry.target_id == str(sample_event.pk)
        assert entry.detail == {"chains": 2, "bulk": True, "reason": "document_type_switch"}


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestUploadViewPaths:
    def test_create_add_and_replace_each_reach_the_audit_choke_point(
        self, client, staff_user, document_type, file_template
    ):
        client.force_login(staff_user)
        occurred_at = timezone.now().replace(second=0, microsecond=0)
        create_response = client.post(
            reverse("core:event_create"),
            {
                "document_type": str(document_type.pk),
                "occurred_at": occurred_at.strftime("%Y-%m-%dT%H:%M"),
                file_template.slug: _pdf("Create-Klartext-Person.pdf", b"create"),
            },
        )
        assert create_response.status_code == 302
        event = Event.objects.get(document_type=document_type)

        add_response = client.post(
            reverse("core:event_update", kwargs={"pk": event.pk}),
            {
                "document_type": str(document_type.pk),
                "occurred_at": occurred_at.strftime("%Y-%m-%dT%H:%M"),
                file_template.slug: _pdf("Add-Klartext-Person.pdf", b"add"),
            },
        )
        assert add_response.status_code == 302
        first = event.attachments.filter(is_current=True).order_by("created_at").first()

        replace_response = client.post(
            reverse("core:event_update", kwargs={"pk": event.pk}),
            {
                "document_type": str(document_type.pk),
                "occurred_at": occurred_at.strftime("%Y-%m-%dT%H:%M"),
                replace_upload_key(file_template.slug, first.entry_id): _pdf("Replace-Klartext-Person.pdf", b"replace"),
            },
        )
        assert replace_response.status_code == 302

        entries = AuditLog.objects.filter(
            action=AuditLog.Action.ATTACHMENT_UPLOAD,
            target_id=str(event.pk),
        ).order_by("timestamp")
        assert [entry.detail["mode"] for entry in entries] == ["add", "add", "replace"]
        assert all(entry.target_id == str(event.pk) for entry in entries)
        assert all("Klartext-Person" not in str(entry.detail) for entry in entries)
