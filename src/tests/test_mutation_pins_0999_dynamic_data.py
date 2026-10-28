"""Dynamic data loss and explicit type-selection contracts.

Source: mutation delta of the release-0.99.9 checkpoint, 12 mutants.
Groups: protected discarded values (10), empty discarded values (1),
explicit type selection (1).
Follows test_mutation_pins_0998_dynamic_data.py.
"""

import pytest
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from core.models import DocumentType
from core.services.dynamic_data import clean_dynamic_data, resolve_entity_document_type
from core.services.file_vault import encrypt_field

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


@pytest.fixture
def target(facility):
    return DocumentType.objects.create(facility=facility, name="Empty target", entity_type="episode")


@pytest.mark.parametrize("value", ["filled", "encrypted", "corrupt"])
def test_protected_type_switch_reports_exact_field_error(target, facility, value):
    """Discarded filled values, including unreadable ciphertext, block protected switches.

    x_clean_dynamic_data__mutmut_26
    x_clean_dynamic_data__mutmut_27
    x_clean_dynamic_data__mutmut_28
    x_clean_dynamic_data__mutmut_31
    x_clean_dynamic_data__mutmut_32
    x_clean_dynamic_data__mutmut_33
    x_clean_dynamic_data__mutmut_34
    x_clean_dynamic_data__mutmut_35
    x_clean_dynamic_data__mutmut_36
    x_clean_dynamic_data__mutmut_37
    """
    stored = {
        "filled": "Protected source",
        "encrypted": encrypt_field("Protected source"),
        "corrupt": {"__encrypted__": True, "value": "invalid"},
    }[value]
    with pytest.raises(ValidationError) as error:
        clean_dynamic_data(
            facility,
            target,
            {},
            entity_type="episode",
            entity_type_error="Wrong entity",
            old_data={"removed-source": stored},
            prevent_data_loss=True,
        )
    assert error.value.message_dict == {
        "document_type": [
            _("Der Dokumentationstyp kann nicht gewechselt werden, solange dadurch ausgefüllte Felder verloren gehen.")
        ]
    }


def test_protected_type_switch_allows_empty_removed_value(target, facility):
    """Removing only empty old text remains valid with loss prevention enabled.

    x_clean_dynamic_data__mutmut_29
    """
    assert (
        clean_dynamic_data(
            facility,
            target,
            {},
            entity_type="episode",
            entity_type_error="Wrong entity",
            old_data={"removed-source": "  "},
            prevent_data_loss=True,
        )
        == {}
    )


def test_resolver_requires_explicit_selection_by_default(target, facility):
    """A sole active type is not selected without explicit defaulting consent.

    x_resolve_entity_document_type__mutmut_1
    """
    with pytest.raises(ValidationError) as error:
        resolve_entity_document_type(facility, None, entity_type="episode")
    assert error.value.message_dict == {"document_type": [_("Bitte wählen Sie einen Dokumentationstyp aus.")]}
