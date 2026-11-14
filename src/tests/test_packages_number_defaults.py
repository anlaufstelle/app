"""Number defaults in the shipped packages and the loader's tri-state ``default_value``.

Covers:

* every shipped package loads with its number defaults and each default honours
  the bounds of its field,
* the tally form for the needle exchange and the regular event form pre-fill
  the defaults,
* re-applying a package never silently clears or replaces a default kept in the
  configuration UI when the package does not carry the key (omitted = untouched,
  ``null`` = deliberately cleared, a value = set),
* a facility-maintained bound that excludes the package default is a clear
  error, never a silently invalid state.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.template.loader import render_to_string

from core.forms.events import DynamicEventDataForm
from core.forms.tally import TallyNumberDataForm
from core.models import DocumentType, FieldTemplate
from core.packages.catalog import list_packages, load_package_data, parse_catalog_package
from core.packages.loader import PackageLoadError, PackageReport, load_package
from core.packages.schema import parse_package

# Number fields of the shipped packages and the default each one must carry.
EXPECTED_NUMBER_DEFAULTS: dict[str, dict[str, str]] = {
    "default": {"dauer": "15", "ausgabe": "1", "rueckgabe": "1"},
    "beratungsstelle": {"dauer": "15"},
    "awareness": {"dauer": "15"},
    "frauenhaus": {"dauer": "15"},
    "jugendarbeit": {"dauer": "15", "teilnehmende": "1"},
}


def _raw(**template_overrides: Any) -> dict[str, Any]:
    """Minimal package with one number field; ``template_overrides`` extend that field."""
    template = {"slug": "menge", "name": "Menge", "field_type": "number", **template_overrides}
    return {"schema_version": 1, "field_templates": [template]}


def _load(facility: Any, raw: dict[str, Any], *, dry_run: bool = False) -> PackageReport:
    return load_package(facility, parse_package(raw), dry_run=dry_run)


def _set_in_ui(facility: Any, slug: str, **values: Any) -> None:
    """Change a template the way the configuration UI does (full_clean + save)."""
    template = FieldTemplate.objects.get(facility=facility, slug=slug)
    for attribute, value in values.items():
        setattr(template, attribute, value)
    template.full_clean()
    template.save()


def test_expected_defaults_cover_every_number_field_of_the_catalog():
    """Guard: a new number field in a package must get an explicit decision here."""
    assert set(EXPECTED_NUMBER_DEFAULTS) == set(list_packages())
    for package_name in list_packages():
        package = parse_catalog_package(package_name)
        number_slugs = {
            template.slug
            for template in package.field_templates
            if template.field_type == FieldTemplate.FieldType.NUMBER
        }
        assert number_slugs == set(EXPECTED_NUMBER_DEFAULTS[package_name]), package_name


@pytest.mark.django_db
@pytest.mark.parametrize("package_name", list_packages())
def test_package_loads_with_valid_number_defaults(facility, package_name):
    report = load_package(facility, parse_catalog_package(package_name))

    assert report.errors == []
    for slug, expected in EXPECTED_NUMBER_DEFAULTS[package_name].items():
        template = FieldTemplate.objects.get(facility=facility, slug=slug)
        assert template.default_value == expected, slug
        value = int(template.default_value)
        if template.min_value is not None:
            assert value >= template.min_value, slug
        if template.max_value is not None:
            assert value <= template.max_value, slug


@pytest.mark.django_db
class TestFormsPrefillPackageDefaults:
    def test_tally_form_for_needle_exchange_prefills_both_numbers(self, facility):
        load_package(facility, parse_catalog_package("default"))
        document_type = DocumentType.objects.get(facility=facility, slug="spritzentausch")

        form = TallyNumberDataForm(document_type=document_type)

        assert form.fields["ausgabe"].initial == 1
        assert form.fields["rueckgabe"].initial == 1

    def test_tally_number_sheet_renders_the_prefilled_values(self, facility):
        load_package(facility, parse_catalog_package("default"))
        document_type = DocumentType.objects.get(facility=facility, slug="spritzentausch")

        html = render_to_string(
            "core/zeitstrom/partials/tally_inline_form.html",
            {
                "tally_form": TallyNumberDataForm(document_type=document_type),
                "document_type": document_type,
                "tally_increment_url": f"/partials/tally/increment/?document_type={document_type.pk}",
            },
        )

        assert html.count('value="1"') >= 2

    def test_regular_event_form_prefills_the_numbers(self, facility):
        load_package(facility, parse_catalog_package("default"))
        document_type = DocumentType.objects.get(facility=facility, slug="spritzentausch")

        form = DynamicEventDataForm(document_type=document_type, facility=facility)

        assert form.fields["ausgabe"].initial == 1
        assert form.fields["rueckgabe"].initial == 1

    def test_regular_event_form_prefills_the_duration(self, facility):
        load_package(facility, parse_catalog_package("default"))
        document_type = DocumentType.objects.get(facility=facility, slug="kontakt")

        form = DynamicEventDataForm(document_type=document_type, facility=facility)

        assert form.fields["dauer"].initial == 15


@pytest.mark.django_db
class TestShippedPackageResetsDefaultsOnReapply:
    """A package value always wins on re-apply: a deliberate sync-back, like the other package values."""

    @pytest.mark.parametrize(("slug", "package_value"), [("dauer", "15"), ("ausgabe", "1"), ("rueckgabe", "1")])
    def test_ui_default_is_reset_to_the_package_value(self, facility, slug, package_value):
        package = parse_catalog_package("default")
        load_package(facility, package)
        _set_in_ui(facility, slug, default_value="30")

        report = load_package(facility, package)

        assert report.errors == []
        assert slug in report.field_templates.updated
        assert FieldTemplate.objects.get(facility=facility, slug=slug).default_value == package_value

    def test_dry_run_plans_the_reset_without_writing_it(self, facility):
        package = parse_catalog_package("default")
        load_package(facility, package)
        _set_in_ui(facility, "dauer", default_value="30")

        report = load_package(facility, package, dry_run=True)

        assert "dauer" in report.field_templates.updated
        assert FieldTemplate.objects.get(facility=facility, slug="dauer").default_value == "30"

    def test_package_without_the_key_leaves_the_ui_default_untouched(self, facility):
        raw = load_package_data("default")
        load_package(facility, parse_package(raw))
        _set_in_ui(facility, "dauer", default_value="30")
        dauer = next(template for template in raw["field_templates"] if template["slug"] == "dauer")
        del dauer["default_value"]

        report = load_package(facility, parse_package(raw))

        assert report.errors == []
        assert "dauer" in report.field_templates.unchanged
        assert FieldTemplate.objects.get(facility=facility, slug="dauer").default_value == "30"


@pytest.mark.django_db
class TestDefaultValueTriState:
    def test_missing_key_leaves_a_ui_default_untouched(self, facility):
        _load(facility, _raw())
        _set_in_ui(facility, "menge", default_value="7")

        report = _load(facility, _raw())

        assert report.errors == []
        assert FieldTemplate.objects.get(facility=facility, slug="menge").default_value == "7"
        assert report.field_templates.unchanged == ["menge"]

    def test_missing_key_is_reported_as_preserved(self, facility):
        _load(facility, _raw())
        _set_in_ui(facility, "menge", default_value="7")

        report = _load(facility, _raw())

        assert [identifier for identifier, _message in report.preserved] == ["menge"]
        assert "default_value='7'" in report.preserved[0][1]

    def test_missing_key_without_existing_default_reports_nothing_preserved(self, facility):
        _load(facility, _raw())

        report = _load(facility, _raw())

        assert report.preserved == []

    def test_missing_key_on_create_uses_the_model_default(self, facility):
        _load(facility, _raw())

        assert FieldTemplate.objects.get(facility=facility, slug="menge").default_value == ""

    def test_explicit_value_is_set_on_create(self, facility):
        _load(facility, _raw(default_value="4"))

        assert FieldTemplate.objects.get(facility=facility, slug="menge").default_value == "4"

    def test_explicit_value_replaces_the_existing_default(self, facility):
        _load(facility, _raw(default_value="4"))
        _set_in_ui(facility, "menge", default_value="7")

        report = _load(facility, _raw(default_value="4"))

        assert report.field_templates.updated == ["menge"]
        assert FieldTemplate.objects.get(facility=facility, slug="menge").default_value == "4"

    def test_explicit_null_clears_the_existing_default(self, facility):
        _load(facility, _raw(default_value="4"))

        report = _load(facility, _raw(default_value=None))

        assert report.errors == []
        assert report.field_templates.updated == ["menge"]
        assert FieldTemplate.objects.get(facility=facility, slug="menge").default_value == ""

    def test_explicit_empty_string_clears_the_existing_default(self, facility):
        _load(facility, _raw(default_value="4"))

        _load(facility, _raw(default_value=""))

        assert FieldTemplate.objects.get(facility=facility, slug="menge").default_value == ""

    def test_dry_run_with_missing_key_writes_nothing_and_keeps_the_default(self, facility):
        _load(facility, _raw())
        _set_in_ui(facility, "menge", default_value="7")

        report = _load(facility, _raw(), dry_run=True)

        assert report.field_templates.unchanged == ["menge"]
        assert FieldTemplate.objects.get(facility=facility, slug="menge").default_value == "7"

    def test_non_string_default_is_a_schema_error(self, facility):
        with pytest.raises(Exception) as excinfo:
            parse_package(_raw(default_value=5))

        assert "default_value" in str(excinfo.value)

    def test_second_run_of_a_shipped_package_is_unchanged(self, facility):
        package = parse_catalog_package("default")
        load_package(facility, package)

        second = load_package(facility, package)

        assert second.errors == []
        assert second.field_templates.created == []
        assert second.field_templates.updated == []


@pytest.mark.django_db
class TestPackageDefaultAgainstBounds:
    def test_default_below_the_ui_maintained_minimum_is_a_clear_error(self, facility):
        _load(facility, _raw())
        _set_in_ui(facility, "menge", min_value=5)

        with pytest.raises(PackageLoadError) as excinfo:
            _load(facility, _raw(default_value="1"))

        message = str(excinfo.value)
        assert "menge" in message
        assert "min_value" in message or "mindestens 5" in message
        template = FieldTemplate.objects.get(facility=facility, slug="menge")
        assert (template.min_value, template.default_value) == (5, "")

    def test_dry_run_reports_the_same_bound_error(self, facility):
        _load(facility, _raw())
        _set_in_ui(facility, "menge", min_value=5)

        with pytest.raises(PackageLoadError) as excinfo:
            _load(facility, _raw(default_value="1"), dry_run=True)

        assert [identifier for identifier, _message in excinfo.value.report.errors] == ["menge"]

    def test_default_above_the_ui_maintained_maximum_is_a_clear_error(self, facility):
        _load(facility, _raw())
        _set_in_ui(facility, "menge", max_value=3)

        with pytest.raises(PackageLoadError) as excinfo:
            _load(facility, _raw(default_value="9"))

        assert "menge" in str(excinfo.value)
        assert FieldTemplate.objects.get(facility=facility, slug="menge").default_value == ""

    def test_default_outside_the_package_bounds_is_an_error_on_create(self, facility):
        with pytest.raises(Exception) as excinfo:
            _load(facility, _raw(min_value=5, default_value="1"))

        assert "menge" in str(excinfo.value)
        assert not FieldTemplate.objects.filter(facility=facility, slug="menge").exists()

    def test_package_bound_that_excludes_the_existing_ui_default_is_an_error(self, facility):
        _load(facility, _raw())
        _set_in_ui(facility, "menge", default_value="1")

        with pytest.raises(PackageLoadError):
            _load(facility, _raw(min_value=5))

        template = FieldTemplate.objects.get(facility=facility, slug="menge")
        assert (template.min_value, template.default_value) == (None, "1")

    def test_default_inside_the_bounds_is_applied(self, facility):
        _load(facility, _raw())
        _set_in_ui(facility, "menge", min_value=1, max_value=10)

        report = _load(facility, _raw(default_value="5"))

        assert report.errors == []
        assert FieldTemplate.objects.get(facility=facility, slug="menge").default_value == "5"
