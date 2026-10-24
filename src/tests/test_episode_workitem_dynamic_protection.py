"""Dynamic field contracts for episodes and work items."""

from io import StringIO

import pytest
from cryptography.fernet import Fernet
from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from core.models import FieldTemplate
from core.models.mixins import DynamicDataEncryptionMixin
from core.services.file_vault import is_encrypted_value, rotation, safe_decrypt
from tests.test_episode_workitem_dynamic_data import carrier as carrier
from tests.test_episode_workitem_dynamic_data import make_record


@pytest.mark.django_db
def test_rotation_rewraps_live_and_deleted_records(carrier):
    first = make_record(carrier)
    second = make_record(carrier, is_deleted=True)
    field = carrier[4]
    before = first.data_json[field.slug]
    from django.conf import settings

    new_key = Fernet.generate_key().decode()
    with override_settings(ENCRYPTION_KEYS=f"{new_key},{settings.ENCRYPTION_KEYS}"):
        dry = rotation.rotate_all(dry_run=True)
        first.refresh_from_db()
        assert first.data_json[field.slug] == before
        assert dry[f"{carrier[0]}s"] == 2
        assert rotation.rotate_all(dry_run=False)[f"{carrier[0]}s"] == 2
    with override_settings(ENCRYPTION_KEYS=new_key):
        for record in (first, second):
            record.refresh_from_db()
            assert record.data_json[field.slug] != before
            assert safe_decrypt(record.data_json[field.slug]) == "Secret detail"


@pytest.mark.django_db
def test_template_change_encrypts_existing_values(carrier):
    field = carrier[4]
    field.is_encrypted = False
    field.save()
    record = make_record(carrier)
    record.refresh_from_db()
    before = record.data_json[field.slug]
    assert before == "Secret detail"
    field.is_encrypted = True
    field.save()
    record.refresh_from_db()
    assert record.data_json[field.slug] != before
    assert is_encrypted_value(record.data_json[field.slug])
    assert safe_decrypt(record.data_json[field.slug]) == before


@pytest.mark.architecture
def test_dynamic_carrier_registry_is_complete():
    from core.services.dynamic_records import DYNAMIC_DATA_MODELS

    expected = {model for model in apps.get_models() if issubclass(model, DynamicDataEncryptionMixin)}
    assert set(DYNAMIC_DATA_MODELS) == expected
    assert len(DYNAMIC_DATA_MODELS) == len(expected)


@pytest.mark.django_db
def test_option_removal_and_field_type_changes_are_protected(carrier):
    field = carrier[4]
    field.field_type = FieldTemplate.FieldType.SELECT
    field.options_json = [{"slug": "used", "label": "Used"}, {"slug": "unused", "label": "Unused"}]
    field.save()
    make_record(carrier, data_json={field.slug: "used"})
    field.options_json = [{"slug": "unused", "label": "Unused"}]
    with pytest.raises(ValidationError):
        field.save()
    field.refresh_from_db()
    field.field_type = FieldTemplate.FieldType.NUMBER
    with pytest.raises(ValidationError):
        field.save()


@pytest.mark.django_db
def test_removal_impact_and_orphan_cleanup(carrier):
    from core.services.config.mutations import count_field_removal_impact

    record = make_record(carrier, data_json={carrier[4].slug: "Secret detail", "orphan": "Remove me"})
    assert count_field_removal_impact(carrier[5]) == 1
    call_command("purge_orphan_field_data", stdout=StringIO())
    record.refresh_from_db()
    assert "orphan" not in record.data_json
    assert safe_decrypt(record.data_json[carrier[4].slug]) == "Secret detail"


@pytest.mark.django_db
def test_numeric_inventory_includes_new_carriers(carrier):
    field = carrier[4]
    field.field_type = FieldTemplate.FieldType.NUMBER
    field.max_value = 10
    field.save()
    make_record(carrier, data_json={field.slug: 11})
    output = StringIO()
    call_command("inventory_number_ranges", facility=str(carrier[3].facility_id), stdout=output)
    assert field.slug in output.getvalue()
    assert "11" not in output.getvalue()


@pytest.mark.django_db
@pytest.mark.parametrize("hold_scope", ["direct", "parent"])
def test_audit_pruning_preserves_held_carrier_targets(carrier, staff_user, hold_scope):
    from datetime import timedelta
    from types import SimpleNamespace

    from core.models import AuditLog, LegalHold
    from core.services.audit import audit_event
    from core.services.retention import prune_auditlog

    kwargs = {"client": carrier[2]["facility"].clients.first()} if carrier[0] == "workitem" else {}
    record = make_record(carrier, **kwargs)
    sibling = make_record(carrier, **kwargs)
    now = timezone.now() + timedelta(days=800)
    kept = audit_event(AuditLog.Action.CONFIG_UPDATE, user=staff_user, facility=record.facility, target_obj=record)
    removed = audit_event(AuditLog.Action.CONFIG_UPDATE, user=staff_user, facility=record.facility, target_obj=sibling)
    target = record if hold_scope == "direct" else record.case if carrier[0] == "episode" else record.client
    LegalHold.objects.create(
        facility=record.facility,
        target_type=type(target).__name__,
        target_id=target.pk,
        reason="Keep evidence",
        created_by=staff_user,
    )
    prune_auditlog(record.facility, SimpleNamespace(auditlog_retention_months=12), now=now)
    assert AuditLog.objects.filter(pk=kept.pk).exists()
    assert AuditLog.objects.filter(pk=removed.pk).exists() is (hold_scope == "parent")


@pytest.mark.django_db
def test_bulk_updates_encrypt_dynamic_fields(carrier):
    record = make_record(carrier)
    field = carrier[4]
    record.data_json = {field.slug: "Bulk secret"}
    carrier[1].objects.bulk_update([record], ["data_json"])
    record.refresh_from_db()
    assert is_encrypted_value(record.data_json[field.slug])
    assert safe_decrypt(record.data_json[field.slug]) == "Bulk secret"


@pytest.mark.django_db
def test_field_deletion_keeps_orphan_semantics(carrier):
    from core.services.config.delete_blockers import field_template_delete_blockers

    make_record(carrier)
    carrier[5].delete()
    assert field_template_delete_blockers(carrier[4])
