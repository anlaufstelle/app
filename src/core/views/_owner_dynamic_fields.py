"""Shared dynamic-field helpers for person and case editing."""

from core.models import FieldTemplate
from core.services.compliance import user_can_see_document_type
from core.services.dynamic_data import build_field_template_lookup, count_filled_dynamic_values


def _file_field_slugs(document_type):
    """Return the current type's file slugs for preserving existing markers."""
    if document_type is None:
        return set()
    return {
        slug
        for slug, ft in build_field_template_lookup(document_type).items()
        if ft.field_type == FieldTemplate.FieldType.FILE
    }


def _lock_invisible_document_type(request, owner, form):
    """Remove an invisible type selector before validation to preserve hidden data."""
    doc_type = owner.document_type
    if doc_type is not None and not user_can_see_document_type(request.user, doc_type):
        del form.fields["document_type"]
        return True
    return False


def _active_attachment_chains(owner) -> int:
    """Count active chains, clearing default ordering before distinct entry IDs."""
    return owner.attachments.filter(deleted_at__isnull=True).order_by().values("entry_id").distinct().count()


def _values_at_risk(owner, data_json, *, type_locked: bool) -> dict[str, int] | None:
    """Return populated text-field and file-chain counts before a type switch."""
    if type_locked:
        return None
    field_values = count_filled_dynamic_values(data_json)
    files = _active_attachment_chains(owner)
    if not field_values and not files:
        return None
    return {"field_values": field_values, "files": files}
