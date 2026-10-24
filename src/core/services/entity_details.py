"""Loss accounting for episode and task document type changes."""

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.services.dynamic_data import count_filled_dynamic_values
from core.services.file_vault import safe_decrypt


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
        {slug: safe_decrypt(value, default=value) for slug, value in form.details_old_data.items()}
    )
    return {
        "data_form": form.data_form,
        "dynamic_values_at_risk": {"field_values": count, "files": 0} if count else None,
        "original_document_type_id": form.details_old_type_id,
    }
