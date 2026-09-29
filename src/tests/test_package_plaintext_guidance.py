"""Package guidance for unencrypted goal and note fields."""

from __future__ import annotations

import pytest

from core.forms.dynamic import DYNAMIC_FIELD_PLAINTEXT_HINT, build_dynamic_form_field
from core.models import FieldTemplate
from core.packages.catalog import parse_catalog_package
from core.packages.loader import load_package

PACKAGE_NAMES = ("frauenhaus", "default", "beratungsstelle")
FIELD_SLUGS = ("ziel", "notiz")
PLAINTEXT_GUIDANCE = " ".join(
    (
        "Keine Adressen, Klarnamen oder Verfahrensdaten eintragen —",
        "Pseudonyme wie in der übrigen Dokumentation verwenden.",
    )
)


def test_plaintext_fields_define_the_exact_guidance_within_the_model_limit():
    found = set()
    max_length = FieldTemplate._meta.get_field("help_text").max_length

    for package_name in PACKAGE_NAMES:
        package = parse_catalog_package(package_name)
        templates = {template.slug: template for template in package.field_templates}
        for field_slug in FIELD_SLUGS:
            found.add((package_name, field_slug))
            assert templates[field_slug].help_text == PLAINTEXT_GUIDANCE
            assert len(templates[field_slug].help_text) <= max_length

    assert found == {(package_name, field_slug) for package_name in PACKAGE_NAMES for field_slug in FIELD_SLUGS}


@pytest.mark.django_db
@pytest.mark.parametrize("package_name", PACKAGE_NAMES)
def test_reloading_package_restores_plaintext_guidance(package_name, facility):
    package = parse_catalog_package(package_name)
    first_report = load_package(facility, package)
    templates = FieldTemplate.objects.for_facility(facility).filter(slug__in=FIELD_SLUGS)

    assert first_report.errors == []
    assert templates.count() == len(FIELD_SLUGS)
    assert set(templates.values_list("help_text", flat=True)) == {PLAINTEXT_GUIDANCE}

    templates.update(help_text="")
    second_report = load_package(facility, package)

    assert second_report.errors == []
    assert set(templates.values_list("help_text", flat=True)) == {PLAINTEXT_GUIDANCE}


@pytest.mark.django_db
@pytest.mark.parametrize("package_name", PACKAGE_NAMES)
def test_dynamic_plaintext_fields_show_package_guidance_once(package_name, facility):
    package = parse_catalog_package(package_name)
    load_package(facility, package)
    templates = FieldTemplate.objects.for_facility(facility).filter(slug__in=FIELD_SLUGS)

    assert templates.count() == len(FIELD_SLUGS)
    for template in templates:
        field = build_dynamic_form_field(template, initial_data=None)
        assert field.help_text == PLAINTEXT_GUIDANCE
        assert str(field.help_text).count("Klarnamen") == 1
        assert str(DYNAMIC_FIELD_PLAINTEXT_HINT) not in str(field.help_text)
