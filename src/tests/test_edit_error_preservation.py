"""HTML edit failures retain permitted drafts and require an explicit retry."""

from html.parser import HTMLParser
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.models import AuditLog, Client, EventHistory
from core.services.case import update_case
from core.services.client import update_client
from core.services.events import update_event

pytestmark = pytest.mark.django_db


class HiddenInputs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("type") == "hidden" and attrs.get("name"):
            self.values[attrs["name"]] = attrs.get("value", "")


def hidden_values(response):
    parser = HiddenInputs()
    parser.feed(response.content.decode())
    return parser.values


@pytest.fixture(params=["client", "case"])
def edit_target(request, client, staff_user):
    return _prepare_edit_target(request, request.param, client, staff_user)


@pytest.fixture(params=["client", "case", "event"])
def any_edit_target(request, client, staff_user):
    return _prepare_edit_target(request, request.param, client, staff_user)


def _prepare_edit_target(request, kind, client, staff_user):
    target = request.getfixturevalue(
        {"client": "client_identified", "case": "case_open", "event": "sample_event"}[kind]
    )
    client.force_login(staff_user)
    url = reverse(f"core:{kind}_update", kwargs={"pk": target.pk})
    response = client.get(url)
    assert response.status_code == 200
    payload = hidden_values(response)
    if kind == "client":
        payload.update(
            pseudonym=target.pseudonym, contact_stage=target.contact_stage, age_cluster=target.age_cluster, notes=""
        )
        text_field = "notes"
    elif kind == "case":
        payload.update(title=target.title, description="", lead_user=str(target.lead_user_id or ""))
        text_field = "description"
    else:
        payload.update(dauer="15", notiz="")
        text_field = "notiz"
    return kind, target, url, payload, text_field


def change_server(kind, target, user, text):
    target = type(target).objects.get(pk=target.pk)
    if kind == "client":
        update_client(target, user, notes=text)
    elif kind == "case":
        update_case(target, user, description=text)
    else:
        update_event(target, user, {"dauer": 42, "notiz": text})


def saved_text(kind, target):
    target.refresh_from_db()
    return (
        target.data_json["notiz"]
        if kind == "event"
        else getattr(target, "notes" if kind == "client" else "description")
    )


def test_conflict_keeps_draft_and_explicit_retry_saves_once(client, staff_user, edit_target):
    kind, target, url, payload, field = edit_target
    draft = ("Own long draft <safe> & complete. " * 100).strip()
    change_server(kind, target, staff_user, "Server text from tab B")
    before = AuditLog.objects.filter(target_id=str(target.pk)).count()
    history_before = EventHistory.objects.filter(event_id=target.pk).count() if kind == "event" else None
    payload[field] = draft
    response = client.post(url, payload)
    assert response.status_code == 200
    assert not response.has_header("Location")
    own_form = response.context["data_form"] if kind == "event" else response.context["form"]
    assert own_form.is_bound
    assert own_form[field].value() == draft
    assert "Aktueller Stand" in response.content.decode()
    assert "Server text from tab B" in response.content.decode()
    assert 'data-conflict-difference="true"' in response.content.decode()
    assert saved_text(kind, target) == "Server text from tab B"
    assert AuditLog.objects.filter(target_id=str(target.pk)).count() == before
    if kind == "event":
        assert EventHistory.objects.filter(event_id=target.pk).count() == history_before
    assert hidden_values(response)["expected_updated_at"] == target.updated_at.isoformat()
    payload.update(hidden_values(response))
    retry = client.post(url, payload)
    assert retry.status_code == 302
    assert saved_text(kind, target) == draft
    assert AuditLog.objects.filter(target_id=str(target.pk)).count() == before + 1
    if kind == "event":
        assert EventHistory.objects.filter(event_id=target.pk).count() == history_before + 1


def test_service_error_keeps_draft_and_original_guards(client, edit_target):
    kind, target, url, payload, field = edit_target
    payload[field] = "Own text kept after service failure"
    with patch(
        f"core.views.{ {'client': 'clients', 'case': 'cases', 'event': 'events'}[kind] }.update_{kind}",
        side_effect=ValidationError("Service rejected"),
    ):
        response = client.post(url, payload)
    assert response.status_code == 200
    own_form = response.context["data_form"] if kind == "event" else response.context["form"]
    assert own_form[field].value() == payload[field]
    assert "Service rejected" in response.content.decode()
    assert "Aktueller Stand" not in response.content.decode()
    assert hidden_values(response)["expected_updated_at"] == payload["expected_updated_at"]
    if kind != "client":
        assert hidden_values(response)["expected_form_state"] == payload["expected_form_state"]


@pytest.mark.parametrize("token", ["invalid", "1900-01-01T00:00:00+00:00"])
def test_invalid_or_stale_version_keeps_draft(client, edit_target, token):
    kind, target, url, payload, field = edit_target
    payload["expected_updated_at"] = token
    # Do not provide the unchanged-state fallback for this version probe.
    payload["expected_form_state"] = "invalid"
    payload[field] = "Draft with invalid version"
    response = client.post(url, payload)
    assert response.status_code == 200
    assert response.context["has_edit_conflict"]
    own_form = response.context["data_form"] if kind == "event" else response.context["form"]
    assert own_form[field].value() == payload[field]


def test_revoked_owner_does_not_render_draft_or_server(client, edit_target):
    kind, target, url, payload, field = edit_target
    person = target if kind == "client" else target.client
    Client.objects.filter(pk=person.pk).update(is_active=False)
    payload[field] = "Forbidden draft"
    response = client.post(url, payload)
    assert response.status_code == 404
    assert "Forbidden draft" not in response.content.decode()
    assert "Aktueller Stand" not in response.content.decode()


@pytest.mark.parametrize("phase", ["upload", "service"])
def test_uploaded_file_is_named_for_reselection_and_staging_is_cleaned(client, edit_target, facility, tmp_path, phase):
    from types import SimpleNamespace

    from django.core.files.uploadedfile import SimpleUploadedFile

    from core.models import DocumentType, DocumentTypeField, FieldTemplate

    kind, target, url, payload, field = edit_target
    doc_type = target.document_type
    if doc_type is None:
        doc_type = DocumentType.objects.create(facility=facility, name="Attachment type", entity_type=kind)
        target.document_type = doc_type
        target.save(update_fields=["document_type", "data_json", "updated_at"])
    file_field = FieldTemplate.objects.create(facility=facility, name="Upload document", field_type="file")
    DocumentTypeField.objects.create(document_type=doc_type, field_template=file_field)
    text_field = FieldTemplate.objects.create(facility=facility, name="Extra text", field_type="textarea")
    DocumentTypeField.objects.create(document_type=doc_type, field_template=text_field, sort_order=1)
    payload.update(hidden_values(client.get(url)))
    payload["document_type"] = str(doc_type.pk)
    payload[field] = "Main draft"
    payload[text_field.slug] = "Dynamic draft"
    payload[file_field.slug] = SimpleUploadedFile("test.pdf", b"%PDF-1.4\n", content_type="application/pdf")
    module = f"core.views.{ {'client': 'clients', 'case': 'cases', 'event': 'events'}[kind] }"
    staged_path = tmp_path / "staged.enc"

    def stage(*args, **kwargs):
        if phase == "upload":
            raise ValidationError({file_field.slug: "Upload rejected"})
        staged_path.write_bytes(b"encrypted staging")
        return {}, [SimpleNamespace(output_path=staged_path)]

    with (
        patch(f"{module}.stage_attachment_uploads", side_effect=stage),
        patch(f"{module}.update_{kind}", side_effect=ValidationError("Service rejected")),
    ):
        response = client.post(url, payload)
    assert response.status_code == 200
    assert response.context["data_form"].is_bound
    assert response.context["data_form"][text_field.slug].value() == "Dynamic draft"
    own_form = response.context["data_form"] if kind == "event" else response.context["form"]
    assert own_form[field].value() == "Main draft"
    html = response.content.decode()
    assert "Datei bitte erneut auswählen:" in html
    assert "Upload document" in html
    assert not staged_path.exists()
    target.refresh_from_db()
    assert not target.attachments.exists()
    assert text_field.slug not in target.data_json


@pytest.mark.parametrize("role", ["staff_user", "lead_user", "assistant_user"])
def test_conflict_server_state_obeys_role_visibility(client, edit_target, facility, request, role):
    from core.models import DocumentType, DocumentTypeField, FieldTemplate

    kind, target, url, payload, field = edit_target
    user = request.getfixturevalue(role)
    doc_type = target.document_type
    if doc_type is None:
        doc_type = DocumentType.objects.create(facility=facility, name="Visible type", entity_type=kind)
        target.document_type = doc_type
    secret = FieldTemplate.objects.create(
        facility=facility,
        name="Restricted secret label",
        field_type="textarea",
        sensitivity="high",
        is_encrypted=True,
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=secret)
    target.data_json[secret.slug] = "Restricted server text"
    if kind == "event":
        target.created_by = user
    target.save()
    client.force_login(user)
    opened = client.get(url)
    if role == "assistant_user" and kind != "event":
        assert opened.status_code == 403
        return
    assert opened.status_code == 200
    payload.update(hidden_values(opened))
    payload["document_type"] = str(doc_type.pk)
    payload[field] = "Own allowed draft"
    payload[secret.slug] = "Restricted posted text"
    payload["expected_updated_at"] = "invalid"
    payload["expected_form_state"] = "invalid"
    response = client.post(url, payload)
    assert response.status_code == 200
    assert response.context["has_edit_conflict"]
    html = response.content.decode()
    if role == "lead_user":
        assert "Restricted server text" in html
        assert response.context["data_form"][secret.slug].value() == "Restricted posted text"
    else:
        assert "Restricted server text" not in html
        assert "Restricted posted text" not in html
        assert "Restricted secret label" not in html
        assert secret.slug not in html


@pytest.mark.parametrize("state", ["revoked", "trash", "restricted"])
def test_service_time_access_loss_is_not_rerendered(client, edit_target, state):
    kind, target, url, payload, field = edit_target
    person = target if kind == "client" else target.client
    payload[field] = "Draft after access loss"

    def refuse(*args, **kwargs):
        changes = {
            "revoked": {"is_active": False},
            "trash": {"is_deleted": True},
            "restricted": {"processing_restricted": True},
        }[state]
        Client.objects.filter(pk=person.pk).update(**changes)
        raise ValidationError("Access rejected")

    with patch(
        f"core.views.{ {'client': 'clients', 'case': 'cases', 'event': 'events'}[kind] }.stage_attachment_uploads",
        side_effect=refuse,
    ):
        response = client.post(url, payload)
    assert response.status_code in (302, 404)
    assert "Draft after access loss" not in response.content.decode()
    assert "Aktueller Stand" not in response.content.decode()


def test_field_restricted_during_submission_is_not_reflected(client, edit_target, facility):
    from core.models import DocumentType, DocumentTypeField, FieldTemplate

    kind, target, url, payload, field = edit_target
    doc_type = target.document_type
    if doc_type is None:
        doc_type = DocumentType.objects.create(facility=facility, name="Visible type", entity_type=kind)
        target.document_type = doc_type
        target.save(update_fields=["document_type", "data_json", "updated_at"])
    secret = FieldTemplate.objects.create(facility=facility, name="Changed secret label", field_type="textarea")
    DocumentTypeField.objects.create(document_type=doc_type, field_template=secret)
    payload.update(hidden_values(client.get(url)))
    payload["document_type"] = str(doc_type.pk)
    payload[secret.slug] = "Newly restricted posted value"
    payload[field] = "Still allowed text"

    def refuse(*args, **kwargs):
        FieldTemplate.objects.filter(pk=secret.pk).update(sensitivity="high", is_encrypted=True)
        raise ValidationError({secret.slug: "Secret minimum 99"})

    with patch(
        f"core.views.{ {'client': 'clients', 'case': 'cases', 'event': 'events'}[kind] }.stage_attachment_uploads",
        side_effect=refuse,
    ):
        response = client.post(url, payload)
    assert response.status_code == 200
    html = response.content.decode()
    assert "Newly restricted posted value" not in html
    assert "Changed secret label" not in html
    assert "Secret minimum 99" not in html
    assert secret.slug not in html
    assert "Still allowed text" in html


@pytest.mark.parametrize("failure", ["policy", "conflict"])
def test_real_upload_failure_retains_text_without_encrypted_orphans(
    client, edit_target, facility, settings, tmp_path, failure
):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from core.models import DocumentType, DocumentTypeField, FieldTemplate
    from tests.test_client_case_attachments_ui import PDF_HEADER

    kind, target, url, payload, field = edit_target
    settings.MEDIA_ROOT = str(tmp_path)
    doc_type = target.document_type
    if doc_type is None:
        doc_type = DocumentType.objects.create(facility=facility, name="Upload type", entity_type=kind)
        target.document_type = doc_type
        target.save(update_fields=["document_type", "data_json", "updated_at"])
    upload_field = FieldTemplate.objects.create(facility=facility, name="Upload file", field_type="file")
    extra_field = FieldTemplate.objects.create(facility=facility, name="Upload draft", field_type="textarea")
    DocumentTypeField.objects.create(document_type=doc_type, field_template=upload_field)
    DocumentTypeField.objects.create(document_type=doc_type, field_template=extra_field, sort_order=1)
    payload.update(hidden_values(client.get(url)))
    payload["document_type"] = str(doc_type.pk)
    payload[field] = "Own main upload draft"
    payload[extra_field.slug] = "Own dynamic upload draft"
    uploads = [SimpleUploadedFile("valid.pdf", PDF_HEADER, content_type="application/pdf")]
    if failure == "policy":
        uploads.append(
            SimpleUploadedFile("invalid.pdf", b"Plain text masquerading as PDF.\n" * 8, content_type="application/pdf")
        )
    else:
        payload["expected_updated_at"] = "1900-01-01T00:00:00+00:00"
        payload["expected_form_state"] = "invalid"
    payload[upload_field.slug] = uploads
    response = client.post(url, payload)
    assert response.status_code == 200
    assert response.context["data_form"][extra_field.slug].value() == payload[extra_field.slug]
    own_form = response.context["data_form"] if kind == "event" else response.context["form"]
    assert own_form[field].value() == payload[field]
    assert "Datei bitte erneut auswählen:" in response.content.decode()
    assert not list(tmp_path.rglob("*.enc"))
    target.refresh_from_db()
    assert not target.attachments.exists()
    assert extra_field.slug not in target.data_json


def test_another_server_edit_after_comparison_still_conflicts(client, staff_user, edit_target):
    kind, target, url, payload, field = edit_target
    payload[field] = "Own persistent draft"
    change_server(kind, target, staff_user, "First server version")
    first = client.post(url, payload)
    assert first.status_code == 200
    payload.update(hidden_values(first))
    change_server(kind, target, staff_user, "Second server version")
    second = client.post(url, payload)
    assert second.status_code == 200
    assert second.context["has_edit_conflict"]
    own_form = second.context["data_form"] if kind == "event" else second.context["form"]
    assert own_form[field].value() == "Own persistent draft"
    assert saved_text(kind, target) == "Second server version"
    assert "Second server version" in second.content.decode()


def test_invalid_form_with_rejected_upload_asks_for_file_reselection(client, any_edit_target, facility):
    """#2539: the plain form-invalid re-render names the file fields to select again."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from core.models import DocumentType, DocumentTypeField, FieldTemplate

    kind, target, url, payload, field = any_edit_target
    doc_type = target.document_type
    if doc_type is None:
        doc_type = DocumentType.objects.create(facility=facility, name="Upload type", entity_type=kind)
        target.document_type = doc_type
        target.save(update_fields=["document_type", "data_json", "updated_at"])
    upload_field = FieldTemplate.objects.create(facility=facility, name="Upload file", field_type="file")
    extra_field = FieldTemplate.objects.create(facility=facility, name="Upload draft", field_type="textarea")
    DocumentTypeField.objects.create(document_type=doc_type, field_template=upload_field)
    DocumentTypeField.objects.create(document_type=doc_type, field_template=extra_field, sort_order=1)
    payload.update(hidden_values(client.get(url)))
    payload["document_type"] = str(doc_type.pk)
    payload[field] = "Own main draft"
    payload[extra_field.slug] = "Own dynamic draft"
    payload[upload_field.slug] = SimpleUploadedFile(
        "malware.exe", b"MZ\x90\x00", content_type="application/x-msdownload"
    )

    response = client.post(url, payload)

    assert response.status_code == 200
    html = response.content.decode()
    assert not response.context["data_form"].is_valid()
    assert response.context["data_form"][extra_field.slug].value() == "Own dynamic draft"
    assert "Datei bitte erneut auswählen:" in html
    assert response.context["file_reselection_fields"] == ["Upload file"]
    target.refresh_from_db()
    assert not target.attachments.exists()
