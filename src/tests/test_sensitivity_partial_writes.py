"""Reject incomplete type changes through public model saves (Refs #2525)."""

from uuid import uuid4

import pytest

from core.models import DocumentType, DocumentTypeField
from core.services.file_vault import is_encrypted_value, safe_decrypt
from tests.test_sensitivity_transitions import make_record

pytestmark = pytest.mark.django_db


def high_target(facility, field, entity="event"):
    target = DocumentType.objects.create(
        facility=facility, name=f"High {uuid4()}", sensitivity="high", entity_type=entity
    )
    DocumentTypeField.objects.create(document_type=target, field_template=field)
    return target


def stored_state(record):
    return type(record).objects.for_facility(record.facility_id).values().get(pk=record.pk)


@pytest.mark.parametrize("fields", [["document_type"], ["document_type", "data_json"]])
def test_partial_type_change_with_plaintext_index_is_rejected(facility, lead_user, fields):
    record, field = make_record(facility, lead_user, "event", value="PartialSecret")
    target = high_target(facility, field)
    before = stored_state(record)
    record.document_type = target
    with pytest.raises(ValueError, match="document_type.*data_json.*search_text"):
        record.save(update_fields=fields)
    assert stored_state(record) == before


@pytest.mark.parametrize("entity", ["event", "client", "case"])
@pytest.mark.parametrize("type_field", ["document_type", "document_type_id"])
def test_partial_type_change_with_retained_protected_value_is_rejected(facility, lead_user, entity, type_field):
    record, protected = make_record(facility, lead_user, entity, value="RetainedProtectedSecret")
    record.document_type.fields.get(field_template=protected).delete()
    protected.sensitivity = "high"
    protected.save()
    target = high_target(facility, protected, entity)
    before = stored_state(record)
    record.document_type = target
    with pytest.raises(ValueError, match="document_type.*data_json"):
        record.save(update_fields=[type_field])
    assert stored_state(record) == before


@pytest.mark.parametrize("entity", ["event", "client", "case"])
@pytest.mark.parametrize("payload", [{}, {"file": {"__files__": True, "entries": []}}])
def test_partial_type_change_is_rejected_even_without_text_values(facility, lead_user, entity, payload):
    record, field = make_record(facility, lead_user, entity)
    target = high_target(facility, field, entity)
    record.data_json = payload
    record.save()
    before = stored_state(record)
    record.document_type_id = target.pk
    with pytest.raises(ValueError, match="document_type.*data_json"):
        record.save(update_fields=frozenset(["document_type_id"]))
    assert stored_state(record) == before


def test_partial_event_type_change_without_payload_is_rejected(facility, lead_user):
    record, field = make_record(facility, lead_user, "event")
    target = high_target(facility, field)
    before = stored_state(record)
    record.document_type = target
    record.search_text = ""
    with pytest.raises(ValueError, match="document_type.*data_json.*search_text"):
        record.save(update_fields=["document_type_id", "search_text"])
    assert stored_state(record) == before


@pytest.mark.parametrize("entity", ["event", "client", "case"])
@pytest.mark.parametrize("type_field", ["document_type", "document_type_id"])
def test_complete_type_change_persists_protected_values(facility, lead_user, entity, type_field):
    record, protected = make_record(facility, lead_user, entity, value="CompleteSecret")
    record.document_type.fields.get(field_template=protected).delete()
    protected.sensitivity = "high"
    protected.save()
    target = high_target(facility, protected, entity)
    record.document_type = target
    fields = [type_field, "data_json"]
    if entity == "event":
        fields.append("search_text")
    record.save(update_fields=fields)
    record.refresh_from_db()
    assert record.document_type_id == target.pk
    assert is_encrypted_value(record.data_json[protected.slug])
    assert safe_decrypt(record.data_json[protected.slug]) == "CompleteSecret"
    assert getattr(record, "search_text", "") == ""


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Deferred type changes can persist a stale plaintext search index (Refs #2525).",
)
@pytest.mark.parametrize("loading", ["defer_index", "only_type"])
def test_implicit_partial_type_write_never_retains_plaintext_index(facility, lead_user, loading):
    record, field = make_record(facility, lead_user, "event", value="DeferredSecret")
    target = high_target(facility, field)
    before = stored_state(record)
    assert before["search_text"] == "DeferredSecret"
    query = type(record).objects.for_facility(facility)
    if loading == "defer_index":
        query = query.defer("search_text")
    else:
        query = query.only("document_type")
    deferred = query.get(pk=record.pk)
    assert "search_text" in deferred.get_deferred_fields()
    deferred.document_type = target
    try:
        deferred.save()
    except ValueError:
        assert stored_state(record) == before
    else:
        after = stored_state(record)
        assert after["document_type_id"] == target.pk
        assert after["search_text"] == ""
