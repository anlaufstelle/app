"""Pins for event payload preservation and deletion metadata.

Source: mutation delta of the release-0.99.8 checkpoint, 13 reviewed mutants.
Groups: missing binding (2), inactive values (2), anonymous audit (1),
missing in-memory data (1). Seven search precomputations are result-equivalent
because the registered pre_save receiver rebuilds the search index.
"""

from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError

from core.models import AuditLog, Event, EventHistory
from core.services.events import crud

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("has_client", [False, True], ids=["anonymous", "linked"])
def test_missing_row_binding_has_a_defined_result(sample_event, has_client):
    """A missing row yields an empty binding or the domain conflict message.

    x__assert_current_client_binding__mutmut_13,
    x__assert_current_client_binding__mutmut_19.
    The public deletion path rejects missing rows before this defensive branch.
    """
    sample_event.pk = uuid4()
    if has_client:
        with pytest.raises(ValidationError) as error:
            crud._assert_current_client_binding(sample_event)
        assert error.value.messages == [str(crud.CLIENT_BINDING_CHANGED_MESSAGE)]
    else:
        sample_event.client = None
        assert crud._assert_current_client_binding(sample_event) == (None, None)


def test_merge_retains_assigned_inactive_text(sample_event, doc_type_field):
    """Merging a visible draft retains inactive assigned values verbatim.

    x_merge_update_payload__mutmut_6, x_merge_update_payload__mutmut_9.
    """
    template = doc_type_field.field_template
    template.is_active = False
    template.save(update_fields=["is_active"])
    sample_event.data_json = {template.slug: "Retained text"}
    payload = {}
    result = crud.merge_update_payload(sample_event, payload, set(), sample_event.document_type)
    assert result == {template.slug: "Retained text"}
    assert payload == result


def test_anonymous_deletion_audit_records_null_client(sample_event, staff_user):
    """Deleting an anonymous event records a JSON null client in its audit.

    x_soft_delete_event__mutmut_102.
    """
    Event.objects.filter(pk=sample_event.pk).update(client=None, is_anonymous=True)
    sample_event.refresh_from_db()
    crud.soft_delete_event(sample_event, staff_user)
    audit = AuditLog.objects.get(action=AuditLog.Action.DELETE, target_id=sample_event.pk)
    assert "client_id" in audit.detail
    assert audit.detail["client_id"] is None
    sample_event.refresh_from_db()
    assert sample_event.is_deleted


def test_update_accepts_missing_in_memory_data(sample_event, staff_user):
    """An update with missing in-memory data persists a fresh draft and empty history baseline.

    x_update_event__mutmut_49.
    """
    sample_event.data_json = None
    updated = crud.update_event(sample_event, staff_user, {})
    updated.refresh_from_db()
    assert updated.data_json == {}
    history = EventHistory.objects.get(event=sample_event, action=EventHistory.Action.UPDATE)
    assert history.data_before == {}
    assert history.data_after == {}
