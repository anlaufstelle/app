"""Event edit draft preservation and immutable case assignment."""

from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.models import Case
from tests import test_edit_error_preservation as shared

pytestmark = pytest.mark.django_db


@pytest.fixture(params=["event"])
def edit_target(request, client, staff_user):
    """Apply the same public edit contract to the event form."""
    return shared.edit_target.__wrapped__(request, client, staff_user)


test_conflict_keeps_draft_and_explicit_retry_saves_once = shared.test_conflict_keeps_draft_and_explicit_retry_saves_once
test_service_error_keeps_draft_and_original_guards = shared.test_service_error_keeps_draft_and_original_guards
test_invalid_or_stale_version_keeps_draft = shared.test_invalid_or_stale_version_keeps_draft
test_revoked_owner_does_not_render_draft_or_server = shared.test_revoked_owner_does_not_render_draft_or_server
test_uploaded_file_is_named_for_reselection_and_staging_is_cleaned = (
    shared.test_uploaded_file_is_named_for_reselection_and_staging_is_cleaned
)
test_conflict_server_state_obeys_role_visibility = shared.test_conflict_server_state_obeys_role_visibility
test_service_time_access_loss_is_not_rerendered = shared.test_service_time_access_loss_is_not_rerendered
test_field_restricted_during_submission_is_not_reflected = (
    shared.test_field_restricted_during_submission_is_not_reflected
)


@pytest.mark.parametrize("case_kind", ["foreign", "closed", "high"])
@pytest.mark.parametrize("failure", ["conflict", "service", "success"])
@pytest.mark.parametrize("case_field", ["case", "case_id", "case_slug"])
def test_event_edit_never_writes_or_renders_posted_case(
    client, staff_user, sample_event, case_open, failure, case_field, case_kind, second_facility
):
    from core.models import DocumentType

    incoming = Case.objects.create(
        facility=second_facility if case_kind == "foreign" else case_open.facility,
        client=case_open.client,
        title="Prohibited incoming case",
        status=Case.Status.CLOSED if case_kind == "closed" else Case.Status.OPEN,
    )
    if case_kind == "high":
        incoming.document_type = DocumentType.objects.create(
            facility=case_open.facility,
            name="Hidden case type",
            entity_type="case",
            sensitivity="high",
        )
        incoming.save(update_fields=["document_type", "data_json"])
    sample_event.case = case_open
    sample_event.save(update_fields=["case", "updated_at"])
    client.force_login(staff_user)
    url = reverse("core:event_update", kwargs={"pk": sample_event.pk})
    payload = shared.hidden_values(client.get(url))
    payload.update(dauer="20", notiz="Own draft")
    payload[case_field] = str(incoming.pk)
    if failure == "conflict":
        shared.change_server("event", sample_event, staff_user, "Server note")
        response = client.post(url, payload)
    elif failure == "success":
        response = client.post(url, payload)
    else:
        with patch("core.views.events.update_event", side_effect=ValidationError("Service rejected")):
            response = client.post(url, payload)
    assert response.status_code == (302 if failure == "success" else 200)
    sample_event.refresh_from_db()
    assert sample_event.case_id == case_open.pk
    assert case_open.title not in response.content.decode()
    assert str(case_open.pk) not in response.content.decode()
    assert incoming.title not in response.content.decode()
    assert str(incoming.pk) not in response.content.decode()


test_real_upload_failure_retains_text_without_encrypted_orphans = (
    shared.test_real_upload_failure_retains_text_without_encrypted_orphans
)
test_another_server_edit_after_comparison_still_conflicts = (
    shared.test_another_server_edit_after_comparison_still_conflicts
)
