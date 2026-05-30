"""Helper-Factories für ``test_mutation_followup_client_export_*`` (Refs Welle 7 / #930).

Wird von den Sub-Files ``test_mutation_followup_client_export_gathers.py``,
``test_mutation_followup_client_export_serializers.py`` und
``test_mutation_followup_client_export_aggregate.py`` geteilt.
"""

from __future__ import annotations

from django.utils import timezone

from core.models import (
    DocumentType,
    Event,
)


def _make_doc_type(
    facility,
    *,
    name: str = "Doc",
    sensitivity: str = DocumentType.Sensitivity.NORMAL,
) -> DocumentType:
    return DocumentType.objects.create(
        facility=facility,
        category=DocumentType.Category.CONTACT,
        sensitivity=sensitivity,
        name=name,
    )


def _make_event(
    facility,
    client,
    doc_type,
    user,
    *,
    data_json=None,
    occurred_at=None,
    is_deleted=False,
) -> Event:
    return Event.objects.create(
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=occurred_at or timezone.now(),
        data_json=data_json or {},
        created_by=user,
        is_deleted=is_deleted,
    )
