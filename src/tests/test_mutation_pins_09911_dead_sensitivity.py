"""Pins from the targeted mutation delta DEAD, batch sensitivity.

Source: delta DEAD survivors of ``core.services.compliance.sensitivity``
(``field_protection``). Rendering inputs are unsaved model instances, so no
database rows are needed.
"""

import pytest
from django.utils import translation

from core.models import DocumentType, FieldTemplate
from core.services.compliance.sensitivity import field_protection


def test_unknown_field_level_shows_unknown_level_label():
    """An unrecognised stored field level is labelled as unknown and rendered red.

    core.services.compliance.sensitivity.x_field_protection__mutmut_31
    """
    with translation.override("de"):
        protection = field_protection(DocumentType(sensitivity="normal"), FieldTemplate(sensitivity="bogus"))
        assert str(protection["level"]) == "Unbekannte Stufe"
    assert protection["color"] == "red"


def test_inheriting_field_without_document_type_shows_inherit_label():
    """Without a document type an inheriting field is labelled as 'like type'.

    core.services.compliance.sensitivity.x_field_protection__mutmut_38
    """
    with translation.override("de"):
        protection = field_protection(None, FieldTemplate(sensitivity=""))
        assert str(protection["level"]) == "wie Typ"
        # An explicit field level keeps its own label even without a document type.
        explicit = field_protection(None, FieldTemplate(sensitivity="elevated"))
        assert str(explicit["level"]) != "wie Typ"


@pytest.mark.parametrize(
    "type_level,expected",
    [
        ("normal", "Assistenz, Fachkraft, Leitung, Anwendungsbetreuung"),
        ("elevated", "Fachkraft, Leitung, Anwendungsbetreuung"),
        ("high", "Leitung, Anwendungsbetreuung"),
    ],
)
def test_permitted_roles_are_joined_with_comma_space(type_level, expected):
    """The role list names every permitted role, separated by comma and space.

    core.services.compliance.sensitivity.x_field_protection__mutmut_63
    """
    with translation.override("de"):
        protection = field_protection(DocumentType(sensitivity=type_level), FieldTemplate(sensitivity=""))
        assert protection["roles"] == expected
