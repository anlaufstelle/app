"""Helper-Factories für ``test_mutation_followup_offline_*`` (Refs Welle 7 / #930).

Wird von den Sub-Files ``test_mutation_followup_offline_filters.py``,
``test_mutation_followup_offline_serializers.py`` und
``test_mutation_followup_offline_visible_data.py`` geteilt. Kapselt die
Factories, die in ``conftest.py`` nicht fertig vorliegen
(FieldTemplate mit spezifischer Sensitivity, DocumentType mit
überschriebenem Sensitivity-Level, Event mit beliebigem ``data_json``).
"""

from __future__ import annotations

from django.utils import timezone

from core.models import (
    DocumentType,
    DocumentTypeField,
    Event,
    FieldTemplate,
)


def _make_field_template(
    facility,
    *,
    name: str,
    field_type: str = FieldTemplate.FieldType.TEXT,
    sensitivity: str = "",
    is_encrypted: bool = False,
    options_json=None,
) -> FieldTemplate:
    return FieldTemplate.objects.create(
        facility=facility,
        name=name,
        field_type=field_type,
        sensitivity=sensitivity,
        is_encrypted=is_encrypted,
        options_json=options_json or [],
    )


def _make_doc_type(
    facility,
    *,
    name: str = "DT",
    sensitivity: str = DocumentType.Sensitivity.NORMAL,
    icon: str = "",
    color: str = "",
) -> DocumentType:
    return DocumentType.objects.create(
        facility=facility,
        category=DocumentType.Category.CONTACT,
        sensitivity=sensitivity,
        name=name,
        icon=icon,
        color=color,
    )


def _attach(doc_type: DocumentType, field_template: FieldTemplate, sort_order: int = 0) -> None:
    DocumentTypeField.objects.create(document_type=doc_type, field_template=field_template, sort_order=sort_order)


def _make_event(
    facility,
    client,
    doc_type,
    staff_user,
    *,
    data_json=None,
    occurred_at=None,
    case=None,
    episode=None,
    is_anonymous: bool = False,
    is_deleted: bool = False,
    created_by=None,
) -> Event:
    event = Event.objects.create(
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=occurred_at or timezone.now(),
        data_json=data_json or {},
        case=case,
        episode=episode,
        is_anonymous=is_anonymous,
        created_by=created_by if created_by is not None else staff_user,
    )
    if is_deleted:
        Event.objects.filter(pk=event.pk).update(is_deleted=True)
        event.refresh_from_db()
    return event
