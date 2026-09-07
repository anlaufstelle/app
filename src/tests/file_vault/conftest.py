"""Gemeinsame Fixtures der file_vault-Testfamilie."""

import pytest
from django.utils import timezone

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate


@pytest.fixture
def doc_type_with_file(facility):
    dt = DocumentType.objects.create(
        facility=facility,
        name="DocWithFile",
        sensitivity=DocumentType.Sensitivity.NORMAL,
    )
    ft_file = FieldTemplate.objects.create(
        facility=facility,
        name="Scan",
        field_type=FieldTemplate.FieldType.FILE,
    )
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_file, sort_order=0)
    return dt, ft_file


@pytest.fixture
def event(facility, staff_user, doc_type_with_file):
    dt, _ = doc_type_with_file
    return Event.objects.create(
        facility=facility,
        document_type=dt,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
    )
