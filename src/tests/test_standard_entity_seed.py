"""Seed and demo reset persist protected standard fields for all carriers."""

import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection

from core.models import Case, Client, DocumentType, Episode, Facility, WorkItem
from core.services.file_vault import is_encrypted_value, safe_decrypt


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
@pytest.mark.parametrize("scale", ["small", "demo"])
def test_seed_and_reset_create_encrypted_standard_data(scale):
    for reset in [False, True]:
        call_command("seed", scale=scale, flush=reset, stdout=StringIO())
        for facility in Facility.objects.all():
            for model, kind in [(Client, "client"), (Case, "case"), (Episode, "episode"), (WorkItem, "workitem")]:
                filters = {"case__facility": facility} if model is Episode else {"facility": facility}
                records = list(model.objects.filter(**filters))
                standard = DocumentType.objects.for_facility(facility).get(entity_type=kind, is_active=True)
                assert standard.slug == f"standard-{kind}"
                assert standard.fields.get().field_template.is_encrypted
                if scale == "small" and model is Episode:
                    assert not records
                    continue
                assert len(records) >= 2
                filled = 0
                for record in records:
                    record.refresh_from_db()
                    assert record.document_type is not None
                    assert record.document_type.entity_type == kind
                    field = record.document_type.fields.get(field_template__field_type="textarea").field_template
                    if not record.data_json:
                        continue
                    filled += 1
                    encrypted = record.data_json[field.slug]
                    assert is_encrypted_value(encrypted)
                    plaintext = safe_decrypt(encrypted)
                    assert plaintext
                    with connection.cursor() as cursor:
                        cursor.execute(
                            f'SELECT data_json FROM "{model._meta.db_table}" WHERE id = %s',
                            [record.pk],
                        )
                        raw = cursor.fetchone()[0]
                    stored = json.loads(raw) if isinstance(raw, str) else raw
                    assert plaintext not in str(raw)
                    assert stored[field.slug] == encrypted
                assert filled >= 2
