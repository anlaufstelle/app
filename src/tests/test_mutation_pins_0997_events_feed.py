"""Pins for public event preview enrichment.

Source: mutation delta of the release-0.99.7 checkpoint, 3 mutants.
Groups: encrypted selections without options (1); legacy list-valued
single selections with and without options (2).
"""

import pytest
from django.utils import timezone

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate
from core.services.events.feed import enrich_events_with_preview
from core.services.file_vault import encrypt_field, generate_key

pytestmark = pytest.mark.django_db


def preview(facility, staff_user, field_type, options, value):
    """Run the public enrichment path with a persisted field assignment."""
    document_type = DocumentType.objects.create(facility=facility, name="Preview type")
    template = FieldTemplate.objects.create(
        facility=facility,
        name="Selection",
        slug="selection",
        field_type=field_type,
        options_json=options,
    )
    DocumentTypeField.objects.create(document_type=document_type, field_template=template)
    event = Event.objects.create(
        facility=facility,
        document_type=document_type,
        created_by=staff_user,
        occurred_at=timezone.now(),
        data_json={},
    )
    # Reproduce stored legacy values that current write validation would reject.
    Event.objects.filter(pk=event.pk).update(data_json={template.slug: value})
    event.refresh_from_db()
    enrich_events_with_preview([{"type": "event", "object": event}], staff_user)
    return event


def test_encrypted_selection_without_options_preserves_the_decrypted_list(facility, staff_user, settings):
    """Enrichment preserves the typed decrypted fallback when no option labels exist.

    x__format_preview_value__mutmut_11.
    """
    settings.ENCRYPTION_KEYS = ""
    settings.ENCRYPTION_KEY = generate_key()
    value = ["a", "b"]
    event = preview(facility, staff_user, FieldTemplate.FieldType.MULTI_SELECT, [], encrypt_field(value))
    for entries in (event.preview_fields, event.expanded_fields):
        assert entries[0]["value"] == value
        assert isinstance(entries[0]["value"], list)


@pytest.mark.parametrize("options", [[{"slug": "a", "label": "Alpha"}], []], ids=["with-options", "without-options"])
def test_legacy_select_list_uses_the_string_fallback(facility, staff_user, options):
    """Legacy list-valued single selections enrich successfully with a string fallback.

    x__format_preview_value__mutmut_20, x__format_preview_value__mutmut_21.
    """
    value = ["a", "b"]
    event = preview(facility, staff_user, FieldTemplate.FieldType.SELECT, options, value)
    for entries in (event.preview_fields, event.expanded_fields):
        assert entries[0]["value"] == str(value)
