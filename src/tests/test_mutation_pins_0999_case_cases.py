"""Case type removal must not invoke creation defaults.

Source: mutation delta of the release-0.99.9 checkpoint, 2 mutants.
Groups: explicit removal with overlapping source and target fields (2).
Follows test_mutation_pins_0997_case_cases.py.
"""

import pytest
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from core.models import AuditLog, DocumentType, DocumentTypeField, FieldTemplate
from core.services.case.cases import update_case

pytestmark = pytest.mark.django_db


def test_explicit_removal_cannot_default_and_relabel_source(case_open, facility, staff_user):
    """Clearing an inactive source type cannot silently copy values into the sole active type.

    x_update_case__mutmut_58
    x_update_case__mutmut_59
    """
    source = DocumentType.objects.create(facility=facility, name="Inactive source", entity_type="case", is_active=False)
    target = DocumentType.objects.create(facility=facility, name="Active target", entity_type="case")
    field = FieldTemplate.objects.create(facility=facility, name="Shared field", slug="shared-field")
    for document_type in (source, target):
        DocumentTypeField.objects.create(document_type=document_type, field_template=field)
    case_open.document_type = source
    case_open.data_json = {field.slug: "Source value"}
    case_open.save(update_fields=["document_type", "data_json"])
    audit_ids = set(AuditLog.objects.values_list("pk", flat=True))
    with pytest.raises(ValidationError) as error:
        update_case(case_open, staff_user, expected_updated_at=case_open.updated_at, document_type=None)
    assert error.value.message_dict == {"document_type": [_("Bitte wählen Sie einen Dokumentationstyp aus.")]}
    case_open.refresh_from_db()
    assert case_open.document_type_id == source.pk
    assert case_open.data_json == {field.slug: "Source value"}
    assert set(AuditLog.objects.values_list("pk", flat=True)) == audit_ids
