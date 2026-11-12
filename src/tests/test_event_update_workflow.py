"""Event update with attachments: database and disk state for every outcome.

Characterises the update choreography behind ``core:event_update`` from the
outside — through the view, with the stable hooks the file vault offers
(virus scan, encryption, the staged-upload commit). Every case checks both
sides:

* the database — event content, event history, attachment rows and audit rows
  are written together or not at all;
* the disk — files prepared before the write transaction disappear on every
  outcome that does not commit; files of persisted attachments stay.

The response side pins the HTML, HTMX and raw JSON contracts: status codes,
version token, conflict body, field errors and the offline success marker.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from core.models import Attachment, AuditLog, DocumentType, DocumentTypeField, Event, EventHistory, FieldTemplate
from core.services.attachments.crud import replace_upload_key
from core.services.events import decrypt_event_text_data

pytestmark = pytest.mark.usefixtures("_encryption_key")

PDF_MINIMAL = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)

JSON = {"HTTP_ACCEPT": "application/json"}
HTMX = {"HTTP_HX_REQUEST": "true"}
REPLAY_CONFIRMED = {**JSON, "HTTP_X_OFFLINE_REPLAY": "2"}
REPLAY_LEGACY = {**JSON, "HTTP_X_OFFLINE_REPLAY": "1"}

NO_SCAN = "core.services.file_vault.storage.run_virus_scan"
COMMIT_STAGED = "core.services.file_vault.commit_staged_upload"


def _pdf(name):
    return SimpleUploadedFile(name, PDF_MINIMAL, content_type="application/pdf")


class _Setup:
    """An event with one persisted attachment and one text field."""

    def __init__(self, facility, owner_client, user):
        from core.services.file_vault import store_encrypted_file

        self.facility = facility
        self.doc_type = DocumentType.objects.create(
            facility=facility, name="Update workflow", entity_type=DocumentType.EntityType.EVENT
        )
        self.ft_file = FieldTemplate.objects.create(
            facility=facility, name="Update attachment", field_type=FieldTemplate.FieldType.FILE
        )
        self.ft_text = FieldTemplate.objects.create(
            facility=facility, name="Update note", field_type=FieldTemplate.FieldType.TEXT
        )
        DocumentTypeField.objects.create(document_type=self.doc_type, field_template=self.ft_file, sort_order=0)
        DocumentTypeField.objects.create(document_type=self.doc_type, field_template=self.ft_text, sort_order=1)
        self.event = Event.objects.create(
            facility=facility,
            client=owner_client,
            document_type=self.doc_type,
            occurred_at=timezone.now(),
            data_json={self.ft_text.slug: "before"},
            created_by=user,
        )
        with patch(NO_SCAN, lambda *a, **kw: None):
            self.attachment = store_encrypted_file(facility, _pdf("original.pdf"), self.ft_file, self.event, user)
        data = dict(self.event.data_json)
        data[self.ft_file.slug] = {"__files__": True, "entries": [{"id": str(self.attachment.pk), "sort": 0}]}
        self.event.data_json = data
        self.event.save(update_fields=["data_json"])
        self.event.refresh_from_db()
        self.token = self.event.updated_at.isoformat()

    @property
    def url(self):
        return reverse("core:event_update", kwargs={"pk": self.event.pk})

    def payload(self, *, text="after", token=None, replace=True, add=False, **extra):
        data = {
            "document_type": str(self.doc_type.pk),
            "occurred_at": self.event.occurred_at.strftime("%Y-%m-%dT%H:%M"),
            self.ft_text.slug: text,
        }
        if token is not None:
            data["expected_updated_at"] = token
        if replace:
            data[replace_upload_key(self.ft_file.slug, self.attachment.entry_id)] = _pdf("replacement.pdf")
        if add:
            data[self.ft_file.slug] = _pdf("added.pdf")
        data.update(extra)
        return data

    def files_on_disk(self) -> set[str]:
        directory = Path(self._media_root()) / str(self.facility.pk)
        if not directory.exists():
            return set()
        return {path.name for path in directory.rglob("*") if path.is_file()}

    def files_in_db(self) -> set[str]:
        return set(Attachment._base_manager.filter(facility=self.facility).values_list("storage_filename", flat=True))

    @staticmethod
    def _media_root():
        from django.conf import settings

        return settings.MEDIA_ROOT

    def text(self) -> str:
        fresh = Event.objects.get(pk=self.event.pk)
        return decrypt_event_text_data(fresh).get(self.ft_text.slug)

    def snapshot(self):
        """Database and disk state that a rolled-back update must not change."""
        fresh = Event.objects.get(pk=self.event.pk)
        return {
            "updated_at": fresh.updated_at,
            "data_json": fresh.data_json,
            "history": EventHistory.objects.filter(event=fresh).count(),
            "attachments": sorted(
                Attachment._base_manager.filter(facility=self.facility).values_list(
                    "pk", "is_current", "deleted_at", "superseded_by"
                )
            ),
            "disk": self.files_on_disk(),
        }


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


@pytest.fixture
def setup(media_root, facility, client_identified, staff_user):
    return _Setup(facility, client_identified, staff_user)


def _assert_untouched(setup, before):
    assert setup.snapshot() == before
    assert setup.files_on_disk() == setup.files_in_db(), "prepared files must not outlive a rolled-back update"


def _post(http, setup, data, headers=None):
    with patch(NO_SCAN, lambda *a, **kw: None):
        return http.post(setup.url, data, **(headers or {}))


# ------------------------------------------------------------------ success --


@pytest.mark.django_db
class TestSuccess:
    def test_html_saves_event_and_attachments_together(self, client, staff_user, setup):
        client.force_login(staff_user)
        history_before = EventHistory.objects.filter(event=setup.event).count()

        response = _post(client, setup, setup.payload(add=True))

        assert response.status_code == 302
        assert response.url == reverse("core:event_detail", kwargs={"pk": setup.event.pk})
        assert setup.text() == "after"
        assert EventHistory.objects.filter(event=setup.event).count() == history_before + 1
        setup.attachment.refresh_from_db()
        assert not setup.attachment.is_current, "the replaced version is superseded"
        current = Attachment.objects.filter(event=setup.event, is_current=True, deleted_at__isnull=True)
        assert current.count() == 2, "replacement and added file are current"
        disk = setup.files_on_disk()
        assert disk == setup.files_in_db(), "every persisted attachment keeps its file, no orphan remains"
        assert len(disk) == 3

    def test_confirmed_offline_replay_gets_the_saved_marker(self, client, staff_user, setup):
        client.force_login(staff_user)

        response = _post(client, setup, setup.payload(token=setup.token), REPLAY_CONFIRMED)

        assert response.status_code == 200
        assert response.json() == {"status": "saved", "pk": str(setup.event.pk)}
        assert response["X-Offline-Replay-Result"] == "saved"
        assert setup.text() == "after"
        assert setup.files_on_disk() == setup.files_in_db()

    def test_legacy_offline_replay_keeps_the_redirect(self, client, staff_user, setup):
        client.force_login(staff_user)

        response = _post(client, setup, setup.payload(token=setup.token), REPLAY_LEGACY)

        assert response.status_code == 302
        assert "X-Offline-Replay-Result" not in response
        assert setup.text() == "after"

    @pytest.mark.django_db(transaction=True)
    def test_scan_and_encryption_run_outside_the_write_transaction(self, client, staff_user, setup):
        from core.services.file_vault import storage

        real_encrypt = storage.encrypt_file
        seen: dict[str, bool] = {}

        def probing_scan(*args, **kwargs):
            seen["scan"] = connection.in_atomic_block

        def probing_encrypt(*args, **kwargs):
            seen["encrypt"] = connection.in_atomic_block
            return real_encrypt(*args, **kwargs)

        client.force_login(staff_user)
        with patch(NO_SCAN, probing_scan), patch.object(storage, "encrypt_file", probing_encrypt):
            response = client.post(setup.url, setup.payload())

        assert response.status_code == 302
        assert seen == {"scan": False, "encrypt": False}


# ------------------------------------------- version conflict after staging --


@pytest.mark.django_db
class TestVersionConflictAfterStaging:
    def _bump(self, setup):
        Event.objects.filter(pk=setup.event.pk).update(updated_at=timezone.now() + timezone.timedelta(seconds=5))

    @pytest.mark.parametrize("headers", [JSON, HTMX, REPLAY_CONFIRMED], ids=["json", "htmx", "replay"])
    def test_stale_token_returns_conflict_and_rolls_back(self, client, staff_user, setup, headers):
        self._bump(setup)
        before = setup.snapshot()
        client.force_login(staff_user)

        response = _post(client, setup, setup.payload(token=setup.token, add=True), headers)

        assert response.status_code == 409
        body = response.json()
        current = Event.objects.get(pk=setup.event.pk)
        assert body["error"] == "conflict"
        assert body["client_expected"] == setup.token
        assert body["server_state"]["updated_at"] == current.updated_at.isoformat()
        assert body["server_state"]["document_type_name"] == setup.doc_type.name
        assert "X-Offline-Replay-Result" not in response
        _assert_untouched(setup, before)

    def test_missing_token_on_json_returns_missing_token(self, client, staff_user, setup):
        before = setup.snapshot()
        client.force_login(staff_user)

        response = _post(client, setup, setup.payload(add=True), JSON)

        assert response.status_code == 409
        body = response.json()
        assert body["error"] == "missing-token"
        assert body["client_expected"] is None
        assert body["server_state"]["updated_at"] == setup.token
        _assert_untouched(setup, before)

    def test_html_rerenders_comparison_with_the_current_token(self, client, staff_user, setup):
        self._bump(setup)
        before = setup.snapshot()
        client.force_login(staff_user)

        response = _post(client, setup, setup.payload(token=setup.token, add=True))

        assert response.status_code == 200
        current = Event.objects.get(pk=setup.event.pk)
        assert response.context["expected_updated_at"] == current.updated_at.isoformat()
        assert response.context["has_edit_conflict"] is True
        assert response.context["edit_conflict_rows"]
        assert response.context["form_state_token"]
        assert "Datei bitte erneut auswählen:" in response.content.decode()
        assert response.context["data_form"].is_bound
        assert response.context["data_form"].data[setup.ft_text.slug] == "after"
        _assert_untouched(setup, before)


# ---------------------------------------------- business error after staging --


@pytest.mark.django_db
class TestBusinessErrorAfterStaging:
    """The person is restricted while the files are prepared (before any lock)."""

    def _post_restricted(self, http, setup, headers=None):
        from core.models import Client

        def restrict_during_scan(*args, **kwargs):
            Client.objects.filter(pk=setup.event.client_id).update(processing_restricted=True)

        with patch(NO_SCAN, restrict_during_scan):
            return http.post(setup.url, setup.payload(token=setup.token, add=True), **(headers or {}))

    def test_raw_json_gets_422_with_the_message(self, client, staff_user, setup):
        from core.services.client.restriction import PROCESSING_RESTRICTED_MESSAGE

        before = setup.snapshot()
        client.force_login(staff_user)

        response = self._post_restricted(client, setup, JSON)

        assert response.status_code == 422
        body = response.json()
        assert body["error"] == "invalid"
        assert [e["message"] for e in body["errors"]["__all__"]] == [str(PROCESSING_RESTRICTED_MESSAGE)]
        _assert_untouched(setup, before)

    def test_htmx_redirects_back_to_the_form(self, client, staff_user, setup):
        before = setup.snapshot()
        client.force_login(staff_user)

        response = self._post_restricted(client, setup, HTMX)

        assert response.status_code == 302
        assert response.url == setup.url
        _assert_untouched(setup, before)

    def test_html_redirects_to_the_detail_page(self, client, staff_user, setup):
        before = setup.snapshot()
        client.force_login(staff_user)

        response = self._post_restricted(client, setup)

        assert response.status_code == 302
        assert response.url == reverse("core:event_detail", kwargs={"pk": setup.event.pk})
        _assert_untouched(setup, before)


# ------------------------------------------------- attachment write failures --


@pytest.mark.django_db
class TestAttachmentWriteFailure:
    def test_validation_error_rerenders_html_and_rolls_back(self, client, staff_user, setup):
        before = setup.snapshot()
        client.force_login(staff_user)

        with patch(COMMIT_STAGED, side_effect=ValidationError("write refused")):
            response = _post(client, setup, setup.payload(add=True))

        assert response.status_code == 200
        form = response.context["data_form"]
        assert form.is_bound
        assert "write refused" in form.non_field_errors()
        assert response.context["expected_updated_at"] == ""
        _assert_untouched(setup, before)

    def test_field_error_reaches_raw_json(self, client, staff_user, setup):
        before = setup.snapshot()
        client.force_login(staff_user)

        with patch(COMMIT_STAGED, side_effect=ValidationError({setup.ft_text.slug: "field refused"})):
            response = _post(client, setup, setup.payload(token=setup.token, add=True), JSON)

        assert response.status_code == 422
        body = response.json()
        assert body["error"] == "invalid"
        assert [e["message"] for e in body["errors"][setup.ft_text.slug]] == ["field refused"]
        _assert_untouched(setup, before)

    def test_unexpected_exception_propagates_and_rolls_back(self, client, staff_user, setup):
        before = setup.snapshot()
        client.force_login(staff_user)

        with patch(COMMIT_STAGED, side_effect=RuntimeError("disk full")), pytest.raises(RuntimeError, match="disk"):
            _post(client, setup, setup.payload(add=True))

        _assert_untouched(setup, before)


@pytest.mark.django_db
class TestUnexpectedException:
    def test_event_write_failure_removes_prepared_files(self, client, staff_user, setup):
        before = setup.snapshot()
        client.force_login(staff_user)

        with (
            patch.object(EventHistory.objects, "create", side_effect=RuntimeError("history down")),
            pytest.raises(RuntimeError, match="history down"),
        ):
            _post(client, setup, setup.payload(add=True))

        _assert_untouched(setup, before)


# ----------------------------------------------------- durable audit rows --


@pytest.mark.django_db(transaction=True)
class TestAuditDurability:
    def test_quota_backstop_audit_survives_the_rollback(self, client, staff_user, setup):
        from core.services.file_vault.policy import StorageQuotaBackstopError
        from core.services.file_vault.quota import StorageUsage

        before = setup.snapshot()
        client.force_login(staff_user)
        error = StorageQuotaBackstopError(
            facility=setup.facility,
            user=staff_user,
            owner=setup.event,
            usage=StorageUsage(used_bytes=10, trash_bytes=0, limit_bytes=10),
            requested_bytes=len(PDF_MINIMAL),
        )

        with patch(COMMIT_STAGED, side_effect=error):
            response = _post(client, setup, setup.payload(add=True))

        assert response.status_code == 200
        assert response.context["data_form"].is_bound
        assert (
            AuditLog.objects.filter(facility=setup.facility, action=AuditLog.Action.ATTACHMENT_QUOTA_EXCEEDED).count()
            == 1
        )
        _assert_untouched(setup, before)

    def test_security_violation_audit_survives_and_partial_staging_is_removed(self, client, staff_user, setup):
        from core.services.file_vault.virus_scan import ScanResult

        before = setup.snapshot()
        client.force_login(staff_user)

        def scan(uploaded_file):
            infected = uploaded_file.name == "added.pdf"
            return ScanResult(clean=not infected, infected=infected, signature="Probe-Signature")

        with patch("core.services.file_vault.policy.scan_file", scan):
            response = client.post(setup.url, setup.payload(add=True))

        assert response.status_code == 200
        assert response.context["data_form"].is_bound
        violation = AuditLog.objects.get(facility=setup.facility, action=AuditLog.Action.SECURITY_VIOLATION)
        assert violation.detail["reason"] == "virus_detected"
        _assert_untouched(setup, before)
