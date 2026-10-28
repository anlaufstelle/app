"""Pin retained defaults when removing select options.

Source: mutation delta of the release-0.99.9 checkpoint, 1 mutant.
Groups: omitted default argument retains the stored default (1).
"""

import pytest
from django.core.exceptions import ValidationError
from django.utils.translation import gettext

from core.models import FieldTemplate
from core.services.option_usage import validate_option_changes

pytestmark = pytest.mark.django_db


def test_omitted_default_keeps_its_option_protected(facility):
    """Removing the stored default yields a domain error, not an attribute error.

    x_validate_option_changes__mutmut_13.
    """
    field = FieldTemplate.objects.create(
        facility=facility,
        name="Choice",
        field_type=FieldTemplate.FieldType.SELECT,
        options_json=[{"slug": "retained", "label": "Retained"}, {"slug": "other", "label": "Other"}],
        default_value="retained",
    )
    with pytest.raises(ValidationError) as error:
        validate_option_changes(field, [{"slug": "other", "label": "Other"}])

    assert error.value.message_dict == {
        "options_json": [
            gettext(
                "Verwendete Options-Slugs dürfen nicht entfernt oder umbenannt werden: %(slugs)s. "
                "Bitte die Option deaktivieren statt entfernen oder umbenennen."
            )
            % {"slugs": "retained"}
        ]
    }
    assert error.value.error_dict["options_json"][0].code == "used_option_slugs"
