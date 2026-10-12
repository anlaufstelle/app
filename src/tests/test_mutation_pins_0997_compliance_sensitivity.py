"""Pins from the mutation delta of the release-0.99.7 checkpoint, 26 mutants.

Groups: case-sensitive badge palette and fail-closed fallback (16),
strictly higher explicit levels (3), and explicit origin labels (7).
The public protection service and its rendering consumer are exercised
without database rows or rls_test_role.
"""

import pytest
from django.template.loader import render_to_string
from django.utils.translation import gettext as _

from core.models import DocumentType, FieldTemplate
from core.services.compliance.sensitivity import field_protection
from core.templatetags.core_tags import doctype_badge_classes


@pytest.mark.parametrize(
    "level,color", [("normal", "gray"), ("elevated", "amber"), ("high", "red"), ("unknown", "red")]
)
def test_protection_exposes_case_sensitive_badge_palette(level, color):
    """Every effective level keeps the palette key used by its rendered badge.

    x_field_protection__mutmut_41, x_field_protection__mutmut_42,
    x_field_protection__mutmut_43, x_field_protection__mutmut_44,
    x_field_protection__mutmut_45, x_field_protection__mutmut_46,
    x_field_protection__mutmut_47, x_field_protection__mutmut_48,
    x_field_protection__mutmut_49, x_field_protection__mutmut_50,
    x_field_protection__mutmut_51, x_field_protection__mutmut_52,
    x_field_protection__mutmut_54, x_field_protection__mutmut_55,
    x_field_protection__mutmut_56, x_field_protection__mutmut_57.
    """
    protection = field_protection(DocumentType(sensitivity="normal"), FieldTemplate(sensitivity=level))
    assert protection["color"] == color
    rendered = render_to_string("core/config/partials/field_protection.html", {"protection": protection})
    expected_badge = f'<span class="badge {doctype_badge_classes(color)}">{protection["level"]}</span>'
    assert expected_badge in rendered


@pytest.mark.parametrize(
    "base,field", [("normal", ""), ("elevated", "normal"), ("elevated", "elevated"), ("high", "high")]
)
def test_inherited_equal_and_lower_fields_are_not_higher(base, field):
    """The higher-field warning excludes inherited, equal and lower levels.

    x_field_protection__mutmut_71, x_field_protection__mutmut_73,
    x_field_protection__mutmut_74.
    """
    protection = field_protection(DocumentType(sensitivity=base), FieldTemplate(sensitivity=field))
    assert protection["higher"] is False
    rendered = render_to_string(
        "core/config/partials/field_protection.html", {"protection": protection, "show_origin": True}
    )
    assert _("Feldvorlage:") not in rendered


@pytest.mark.parametrize("level", ["normal", "elevated", "high", "unknown"])
def test_explicit_origin_label_matches_its_own_level(level):
    """Explicit fields retain their own translated label, including unknowns.

    x_field_protection__mutmut_10, x_field_protection__mutmut_11,
    x_field_protection__mutmut_12, x_field_protection__mutmut_13,
    x_field_protection__mutmut_14, x_field_protection__mutmut_67,
    x_field_protection__mutmut_68.
    """
    protection = field_protection(DocumentType(sensitivity="normal"), FieldTemplate(sensitivity=level))
    expected = dict(DocumentType.Sensitivity.choices).get(level, _("Unbekannte Stufe"))
    assert str(protection["field_level"]) == str(expected)
    if level != "normal":
        rendered = render_to_string(
            "core/config/partials/field_protection.html", {"protection": protection, "show_origin": True}
        )
        origin_label = _("Feldvorlage:")
        assert f"{origin_label} {expected}</span>" in rendered
