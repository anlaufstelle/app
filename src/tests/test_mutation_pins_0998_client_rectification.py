"""Pins for typed persistence checks and missing correction configuration.

Source: mutation delta of the release-0.99.8 checkpoint, 7 mutants.
Groups: encrypted non-number float rejection (2), persistence message (2),
missing document type message (2), redundant early validation (1, not pinned).
The missing-type helper branch cannot be reached through approval: the public
field allowlist rejects dynamic paths first. Its defensive message is tested directly.
"""

import pytest
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from core.models import AuditLog, DocumentTypeField, FieldTemplate, RectificationRequest
from core.services.client import rectification
from core.services.file_vault import safe_decrypt
from tests.test_sensitivity_transitions import make_record

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def test_float_for_encrypted_text_does_not_pass_typed_persistence(facility, staff_user, lead_user):
    """A different encrypted number never validates a text float; failed approval rolls back.

    x__apply_rectification__mutmut_40, x__apply_rectification__mutmut_41,
    x__apply_rectification__mutmut_51, x__apply_rectification__mutmut_52.
    """
    field = FieldTemplate.objects.create(
        facility=facility,
        name="Corrected text",
        field_type=FieldTemplate.FieldType.TEXT,
        is_encrypted=True,
    )
    person, _field = make_record(facility, staff_user, "client", field=field, value="Original text")
    number = FieldTemplate.objects.create(
        facility=facility,
        name="Other number",
        field_type=FieldTemplate.FieldType.NUMBER,
        is_encrypted=True,
    )
    DocumentTypeField.objects.create(document_type=person.document_type, field_template=number)
    request = rectification.request_rectification(person, staff_user, f"data_json.{field.slug}", 1.5, "Correction")
    before = person.data_json.copy()
    audits = list(AuditLog.objects.values_list("pk", flat=True))

    with pytest.raises(ValidationError) as error:
        rectification.approve_rectification(request, lead_user)

    assert error.value.messages == [_("Die Berichtigung konnte nicht angewendet werden.")]
    person.refresh_from_db()
    request.refresh_from_db()
    assert person.data_json == before
    assert safe_decrypt(person.data_json[field.slug]) == "Original text"
    assert request.status == RectificationRequest.Status.PENDING
    assert request.reviewed_by is None and request.reviewed_at is None
    assert set(AuditLog.objects.values_list("pk", flat=True)) == set(audits)


def test_bound_correction_without_document_type_explains_failure(client_identified):
    """The defensive merge explains missing configuration: x__merge_rectification_data__mutmut_8,
    x__merge_rectification_data__mutmut_9.
    """
    token = rectification._DYNAMIC_CORRECTION.set((str(client_identified.pk), "removed-field", "Correction"))
    try:
        with pytest.raises(ValidationError) as error:
            rectification._merge_rectification_data(client_identified, {})
    finally:
        rectification._DYNAMIC_CORRECTION.reset(token)

    assert error.value.messages == [_("Die Berichtigung konnte nicht angewendet werden.")]
