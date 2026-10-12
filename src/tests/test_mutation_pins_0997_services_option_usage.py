"""Pins for defensive option parsing and field validation errors.

Source: mutation delta of the release-0.99.7 checkpoint, 6 mutants.
Groups: malformed option entries (1), empty selection exemption (1),
unavailable selection message and error code (4). All calls use service APIs.
"""

import pytest
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from core.models import FieldTemplate
from core.services.option_usage import option_slugs, validate_option_references


@pytest.mark.parametrize("malformed", [{}, {"slug": 7}, None, "invalid"], ids=["missing", "numeric", "null", "string"])
def test_option_slugs_ignores_malformed_entries(malformed):
    """Only dictionary entries with string identifiers contribute slugs.

    x_option_slugs__mutmut_4.
    """
    assert option_slugs([malformed, {"slug": "valid", "label": "Valid"}]) == {"valid"}


@pytest.fixture
def selection_template(facility):
    """Persist a selection field with one available option."""
    return FieldTemplate.objects.create(
        facility=facility,
        name="Selection",
        slug="selection",
        field_type=FieldTemplate.FieldType.SELECT,
        options_json=[{"slug": "valid", "label": "Valid"}],
    )


@pytest.mark.django_db
def test_empty_selection_is_allowed_but_unknown_sentinel_is_rejected(selection_template):
    """Clearing a selection is allowed; an unknown nonempty slug is rejected.

    x_validate_option_references__mutmut_25.
    """
    template = selection_template
    validate_option_references(template.facility_id, {template.slug: ""})
    with pytest.raises(ValidationError):
        validate_option_references(template.facility_id, {template.slug: "XXXX"})


@pytest.mark.django_db
def test_unknown_selection_has_actionable_field_error(selection_template):
    """Unavailable selections retain their field, translated message and code.

    x_validate_option_references__mutmut_27, x_validate_option_references__mutmut_28,
    x_validate_option_references__mutmut_29, x_validate_option_references__mutmut_31.
    """
    template = selection_template
    with pytest.raises(ValidationError) as caught:
        validate_option_references(template.facility_id, {template.slug: "unknown"})

    assert caught.value.message_dict == {
        template.slug: [_("Die gewählte Option ist nicht mehr verfügbar. Bitte wählen Sie erneut.")],
    }
    assert [error.code for error in caught.value.error_dict[template.slug]] == ["unavailable_option"]
