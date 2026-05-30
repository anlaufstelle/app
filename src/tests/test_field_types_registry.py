"""Unit tests for the field-type registry (Refs #907 / FND-006)."""

from __future__ import annotations

from datetime import date, time

import pytest
from django import forms
from django.core.exceptions import ValidationError

from core.services.system import FIELD_TYPE_REGISTRY, FieldTypeSpec, field_types, get_spec


class TestRegistryCompleteness:
    """Stellt sicher, dass die Registry und das Model-Enum synchron bleiben."""

    @pytest.mark.django_db
    def test_registry_covers_all_field_type_choices(self):
        from core.models import FieldTemplate

        enum_codes = {value for value, _label in FieldTemplate.FieldType.choices}
        registry_codes = set(FIELD_TYPE_REGISTRY.keys())
        missing = enum_codes - registry_codes
        extra = registry_codes - enum_codes
        assert not missing, f"FieldType-Enum kennt {missing}, aber Registry nicht. Neuer Feldtyp ohne Registry-Eintrag?"
        assert not extra, f"Registry kennt {extra}, aber FieldType-Enum nicht. Veralteter Registry-Eintrag?"


class TestGetSpec:
    def test_returns_spec_for_known_type(self):
        assert get_spec(field_types.NUMBER).form_field_cls is forms.IntegerField

    def test_falls_back_to_text_for_unknown_type(self):
        spec = get_spec("totally-unknown-field-type")
        assert spec.code == field_types.TEXT


class TestWidgetFactory:
    def test_widgets_are_fresh_instances(self):
        spec = get_spec(field_types.DATE)
        w1 = spec.widget_factory()
        w2 = spec.widget_factory()
        assert w1 is not w2
        # Beide sind DateInput-Widgets — Django setzt input_type="date"
        # ueber das Class-Attribut, nicht ueber attrs.
        assert isinstance(w1, forms.DateInput)
        assert w1.input_type == "date"

    def test_text_widget_is_textinput(self):
        spec = get_spec(field_types.TEXT)
        assert isinstance(spec.widget_factory(), forms.TextInput)

    def test_textarea_widget_is_textarea(self):
        spec = get_spec(field_types.TEXTAREA)
        assert isinstance(spec.widget_factory(), forms.Textarea)

    def test_multi_select_widget_is_checkbox_select_multiple(self):
        spec = get_spec(field_types.MULTI_SELECT)
        assert isinstance(spec.widget_factory(), forms.CheckboxSelectMultiple)


class TestParseDefault:
    def test_number_parses_int(self):
        assert get_spec(field_types.NUMBER).parse_default("42") == 42

    def test_number_returns_none_on_invalid(self):
        assert get_spec(field_types.NUMBER).parse_default("abc") is None

    def test_date_parses_iso(self):
        assert get_spec(field_types.DATE).parse_default("2026-05-16") == date(2026, 5, 16)

    def test_date_returns_none_on_invalid(self):
        assert get_spec(field_types.DATE).parse_default("nope") is None

    def test_time_parses_iso(self):
        assert get_spec(field_types.TIME).parse_default("13:45") == time(13, 45)

    @pytest.mark.parametrize("raw,expected", [("true", True), ("1", True), ("false", False), ("0", False)])
    def test_boolean_parses_truthy(self, raw, expected):
        assert get_spec(field_types.BOOLEAN).parse_default(raw) is expected

    def test_multi_select_splits_on_comma(self):
        assert get_spec(field_types.MULTI_SELECT).parse_default("a,b,c") == ["a", "b", "c"]

    def test_multi_select_strips_whitespace(self):
        assert get_spec(field_types.MULTI_SELECT).parse_default("a ,  b") == ["a", "b"]

    def test_file_default_always_none(self):
        assert get_spec(field_types.FILE).parse_default("ignored.pdf") is None

    def test_text_default_is_raw(self):
        assert get_spec(field_types.TEXT).parse_default("hello") == "hello"


class TestValidateDefault:
    def test_text_accepts_anything(self):
        get_spec(field_types.TEXT).validate_default("egal", {})  # no raise

    def test_number_raises_on_non_int(self):
        with pytest.raises(ValidationError):
            get_spec(field_types.NUMBER).validate_default("abc", {})

    def test_date_raises_on_invalid_format(self):
        with pytest.raises(ValidationError):
            get_spec(field_types.DATE).validate_default("nope", {})

    def test_boolean_raises_on_invalid_word(self):
        with pytest.raises(ValidationError):
            get_spec(field_types.BOOLEAN).validate_default("maybe", {})

    def test_select_raises_on_unknown_slug(self):
        spec = get_spec(field_types.SELECT)
        with pytest.raises(ValidationError):
            spec.validate_default("missing", {"options_json": [{"slug": "ok", "is_active": True}]})

    def test_select_accepts_active_slug(self):
        spec = get_spec(field_types.SELECT)
        spec.validate_default("ok", {"options_json": [{"slug": "ok", "is_active": True}]})

    def test_select_rejects_inactive_slug(self):
        spec = get_spec(field_types.SELECT)
        with pytest.raises(ValidationError):
            spec.validate_default("ok", {"options_json": [{"slug": "ok", "is_active": False}]})

    def test_multi_select_raises_when_any_value_unknown(self):
        spec = get_spec(field_types.MULTI_SELECT)
        with pytest.raises(ValidationError):
            spec.validate_default(
                "a,b",
                {"options_json": [{"slug": "a", "is_active": True}]},
            )


class TestAllowsDefault:
    def test_file_does_not_allow_default(self):
        assert get_spec(field_types.FILE).allows_default is False

    @pytest.mark.parametrize(
        "code",
        [
            field_types.TEXT,
            field_types.TEXTAREA,
            field_types.NUMBER,
            field_types.DATE,
            field_types.TIME,
            field_types.BOOLEAN,
            field_types.SELECT,
            field_types.MULTI_SELECT,
        ],
    )
    def test_all_other_types_allow_default(self, code):
        assert get_spec(code).allows_default is True


class TestFieldTypeSpecImmutability:
    def test_spec_is_frozen(self):
        spec = get_spec(field_types.TEXT)
        with pytest.raises((AttributeError, Exception)):  # noqa: PT011 — dataclass-Specific
            spec.code = "mutated"  # type: ignore[misc]

    def test_spec_is_instance_of_fieldtypespec(self):
        for spec in FIELD_TYPE_REGISTRY.values():
            assert isinstance(spec, FieldTypeSpec)
