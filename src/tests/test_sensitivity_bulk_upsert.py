"""Reject conflict updates through protected public managers (Refs #2525)."""

import pytest

from core.models import DocumentType
from core.services.file_vault import is_encrypted_value
from tests.test_sensitivity_partial_writes import high_target, stored_state
from tests.test_sensitivity_transitions import make_record

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("entity", ["event", "client", "case"])
def test_bulk_upsert_type_change_is_rejected(facility, lead_user, entity):
    record, field = make_record(facility, lead_user, entity, value="UpsertTypeSecret")
    target = high_target(facility, field, entity)
    before = stored_state(record)
    record.document_type = target
    with pytest.raises(ValueError, match="bulk_create.*update_conflicts"):
        type(record).objects.bulk_create(
            [record], update_conflicts=True, unique_fields=["pk"], update_fields=["document_type"]
        )
    assert stored_state(record) == before


@pytest.mark.parametrize("entity", ["event", "client", "case"])
def test_bulk_upsert_stale_type_data_is_rejected(facility, lead_user, entity):
    record, field = make_record(facility, lead_user, entity, value="Original")
    stale = type(record).objects.for_facility(facility).get(pk=record.pk)
    target = high_target(facility, field, entity)
    record.document_type = target
    record.save()
    before = stored_state(record)
    assert before.get("search_text", "") == ""
    stale.data_json = {field.slug: "UpsertDataSecret"}
    with pytest.raises(ValueError, match="bulk_create.*update_conflicts"):
        type(record).objects.bulk_create(
            [stale], update_conflicts=True, unique_fields=["pk"], update_fields=["data_json"]
        )
    assert stored_state(record) == before


@pytest.mark.parametrize("entity", ["event", "client", "case"])
def test_bulk_upsert_stale_type_ciphertext_is_rejected(facility, lead_user, entity):
    record, protected = make_record(facility, lead_user, entity, value="Original")
    stale = type(record).objects.for_facility(facility).get(pk=record.pk)
    record.document_type.fields.get(field_template=protected).delete()
    protected.sensitivity = "high"
    protected.save()
    target = high_target(facility, protected, entity)
    record.document_type = target
    record.save()
    before = stored_state(record)
    assert is_encrypted_value(before["data_json"][protected.slug])
    stale.data_json = {protected.slug: "UpsertCipherSecret"}
    with pytest.raises(ValueError, match="bulk_create.*update_conflicts"):
        type(record).objects.bulk_create(
            [stale], update_conflicts=True, unique_fields=["pk"], update_fields=["data_json"]
        )
    assert stored_state(record) == before


@pytest.mark.parametrize("entity", ["event", "client", "case"])
@pytest.mark.parametrize("mode", ["positional", "complete", "empty", "insert"])
def test_bulk_upsert_variants_are_rejected(facility, lead_user, entity, mode):
    record, field = make_record(facility, lead_user, entity)
    before = stored_state(record)
    record.data_json = {field.slug: "VariantSecret"}
    objects = [record]
    if mode == "empty":
        objects = []
    elif mode == "insert":
        record.pk = None
        record._state.adding = True
        if entity == "client":
            record.pseudonym += "-new"
    manager = type(record).objects.for_facility(facility)
    count = manager.count()
    with pytest.raises(ValueError, match="bulk_create.*update_conflicts"):
        if mode == "positional":
            manager.bulk_create(objects, None, False, True, ["data_json"], ["pk"])
        else:
            fields = ["document_type", "data_json"]
            if entity == "event":
                fields.append("search_text")
            manager.bulk_create(
                (obj for obj in objects), update_conflicts=True, update_fields=fields, unique_fields=["pk"]
            )
    assert manager.count() == count
    assert manager.values().get(pk=before["id"]) == before


def test_bulk_create_conflict_updates_on_unprotected_models_remain_available(facility):
    target = DocumentType.objects.create(facility=facility, name="Original type")
    target.name = "Updated type"
    DocumentType.objects.bulk_create([target], update_conflicts=True, update_fields=["name"], unique_fields=["pk"])
    target.refresh_from_db()
    assert target.name == "Updated type"


@pytest.mark.parametrize("entity", ["event", "client", "case"])
@pytest.mark.parametrize("ignore_conflicts", [False, True])
def test_insert_only_bulk_create_protects_values(facility, lead_user, entity, ignore_conflicts):
    record, protected = make_record(facility, lead_user, entity)
    protected.sensitivity = "high"
    protected.save()
    record.pk = None
    record._state.adding = True
    if entity == "client":
        record.pseudonym += "-new"
    record.data_json = {protected.slug: "InsertSecret"}
    type(record).objects.bulk_create([record], ignore_conflicts=ignore_conflicts)
    stored = type(record).objects.for_facility(facility).get(data_json__has_key=protected.slug, pk=record.pk)
    assert is_encrypted_value(stored.data_json[protected.slug])
    assert getattr(stored, "search_text", "") == ""


@pytest.mark.parametrize("entity", ["event", "client", "case"])
def test_ignored_bulk_conflicts_leave_persisted_protection_unchanged(facility, lead_user, entity):
    record, field = make_record(facility, lead_user, entity)
    stale = type(record).objects.for_facility(facility).get(pk=record.pk)
    record.document_type = high_target(facility, field, entity)
    record.save()
    before = stored_state(record)
    stale.data_json = {field.slug: "IgnoredSecret"}
    type(record).objects.bulk_create([stale], ignore_conflicts=True)
    assert stored_state(record) == before
