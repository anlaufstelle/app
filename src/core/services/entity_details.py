"""Loss accounting for episode and task document type changes."""

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.services.compliance import user_can_see_document_type, user_can_see_field
from core.services.dynamic_data import (
    build_field_template_lookup,
    count_filled_dynamic_values,
    orphan_gating_sensitivity,
    resolve_orphan_field_templates,
)
from core.services.file_vault import safe_decrypt


def visible_stored_details(record, user):
    """Filter ciphertext by the source type and field protection before decryption."""
    from core.models import DocumentType, FieldTemplate

    values = dict(record.data_json or {})
    if not values or user is None:
        return values
    source = record.document_type
    if source is not None and not user_can_see_document_type(user, source):
        return {}
    if source is not None:
        fields = build_field_template_lookup(source)
        fields.update(resolve_orphan_field_templates(source, set(values) - set(fields)))
    else:
        # A previously untyped record still carries the field's own protection.
        fields = {
            field.slug: field
            for field in FieldTemplate.objects.for_facility(record.facility_id).filter(slug__in=values)
        }
    source_level = source.sensitivity if source is not None else DocumentType.Sensitivity.NORMAL
    return {
        slug: value
        for slug, value in values.items()
        if user_can_see_field(user, source_level, orphan_gating_sensitivity(fields.get(slug)))
    }


def require_confirmed_type_loss(record, document_type, *, confirmed):
    """Require explicit consent for filled fields outside the target schema."""
    if record.document_type_id == (document_type.pk if document_type else None):
        return 0
    allowed = set(document_type.fields.values_list("field_template__slug", flat=True)) if document_type else set()
    removed = {
        slug: safe_decrypt(value, default=value) for slug, value in record.data_json.items() if slug not in allowed
    }
    count = count_filled_dynamic_values(removed)
    if count and not confirmed:
        raise ValidationError(
            {"document_type": _("Bitte bestätigen Sie den Verlust der ausgefüllten Zusatzfelder beim Typwechsel.")}
        )
    return count


def entity_details_context(form):
    """Keep loss warnings tied to the original stored values after validation."""
    count = count_filled_dynamic_values(
        {
            slug: safe_decrypt(value, default=value) if slug in form.details_visible_old_data else value
            for slug, value in form.details_old_data.items()
        }
    )
    return {
        "data_form": form.data_form,
        "dynamic_values_at_risk": {"field_values": count, "files": 0} if count else None,
        "original_document_type_id": form.details_old_type_id,
    }
