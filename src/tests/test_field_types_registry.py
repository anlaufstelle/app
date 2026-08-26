"""Unit tests for the field-type registry (Refs #907)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, time
from types import SimpleNamespace

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

    @pytest.mark.parametrize("slot", ["coerce_import", "parse_literal", "format_display"])
    def test_every_field_type_defines_all_dispatch_slots(self, slot):
        missing = [spec.code for spec in FIELD_TYPE_REGISTRY.values() if not callable(getattr(spec, slot, None))]
        assert not missing, f"Registry-Slot {slot!r} fehlt für Feldtypen: {missing}"


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

    def test_time_widget_is_timeinput(self):
        """Refs #1998: analog test_widgets_are_fresh_instances (DATE) —
        ``_widget_time`` war ungetestet. ``TimeInput.input_type`` kommt vom
        WIDGET-KLASSEN-Attribut, nicht von ``attrs``; der Test bewacht also vor
        allem, dass ``_widget_time`` überhaupt ``forms.TimeInput`` liefert
        (nicht z. B. versehentlich ``forms.DateInput`` durch einen Copy-Paste-
        Fehler analog zu ``_widget_date``)."""
        spec = get_spec(field_types.TIME)
        widget = spec.widget_factory()
        assert isinstance(widget, forms.TimeInput)
        assert widget.input_type == "time"


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

    def test_number_raises_when_default_exceeds_max_value(self):
        """Refs #1998: analog ``test_model_rejects_default_outside_configured_range``
        (``test_field_template_ranges.py``) — dort nur UNTERHALB ``min_value``
        getestet. ``_validate_number`` prüft über ``validate_number_value``
        auch die OBERE Grenze; ein Mutant, der ``value > max_value`` zu
        ``value < max_value`` kippt, überlebte bislang unbemerkt."""
        with pytest.raises(ValidationError):
            get_spec(field_types.NUMBER).validate_default("11", {"max_value": 10})

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


@pytest.mark.django_db
class TestValidDefaultDoesNotRaiseFullClean:
    """Refs #1998: „gültiger Default-Wert löst ``full_clean()`` NICHT aus" für
    BOOLEAN/DATE/TIME/MULTI_SELECT — analog ``test_valid_number_default``
    (``test_fieldtemplate_default_value.py``) bzw. dem TEXT-„no raise"-Kommentar
    oben in ``TestValidateDefault``. Bislang war für diese vier Typen nur die
    INVALID-Seite getestet (``test_invalid_*_raises`` dort bzw.
    ``test_multi_select_raises_when_any_value_unknown`` hier) — ein Mutant, der
    einen Validator dazu bringt, IMMER zu raisen (statt nur bei ungültigem
    Wert), überlebt eine reine Invalid-Suite unbemerkt, weil dort ohnehin ein
    Raise erwartet wird. Deckt u. a. eine Logik-Inversion in
    ``_validate_multi_select`` ab (``not in`` → ``in`` würde gültige,
    aktive Slugs fälschlich ablehnen).
    """

    def test_valid_boolean_default(self, facility):
        from core.models import FieldTemplate

        ft = FieldTemplate.objects.create(
            facility=facility, name="Aktiv-Valid", field_type=field_types.BOOLEAN, default_value="true"
        )
        ft.full_clean()  # no raise

    def test_valid_date_default(self, facility):
        from core.models import FieldTemplate

        ft = FieldTemplate.objects.create(
            facility=facility, name="Datum-Valid", field_type=field_types.DATE, default_value="2026-05-16"
        )
        ft.full_clean()  # no raise

    def test_valid_time_default(self, facility):
        from core.models import FieldTemplate

        ft = FieldTemplate.objects.create(
            facility=facility, name="Uhrzeit-Valid", field_type=field_types.TIME, default_value="13:45"
        )
        ft.full_clean()  # no raise

    def test_valid_multi_select_default(self, facility):
        from core.models import FieldTemplate

        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Themen-Valid",
            field_type=field_types.MULTI_SELECT,
            options_json=[
                {"slug": "a", "label": "A", "is_active": True},
                {"slug": "b", "label": "B", "is_active": True},
            ],
            default_value="a,b",
        )
        ft.full_clean()  # no raise


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


class TestRuntimeDispatch:
    def test_import_coercion_uses_registry_slot(self, monkeypatch):
        from core.services.import_engine import _coerce_field_value

        sentinel = object()
        spec = get_spec(field_types.TEXT)
        monkeypatch.setitem(
            FIELD_TYPE_REGISTRY,
            field_types.TEXT,
            replace(spec, coerce_import=lambda template, raw, column: sentinel),
        )

        template = SimpleNamespace(field_type=field_types.TEXT)
        assert _coerce_field_value(template, "raw", "Spalte") is sentinel

    def test_condition_parser_uses_registry_slot(self, monkeypatch):
        from core.services.conditions import parse_condition_value

        sentinel = object()
        spec = get_spec(field_types.TEXT)
        monkeypatch.setitem(
            FIELD_TYPE_REGISTRY,
            field_types.TEXT,
            replace(spec, parse_literal=lambda template, raw: sentinel),
        )

        template = SimpleNamespace(options_json=[])
        assert parse_condition_value(field_types.TEXT, template, "raw") is sentinel

    def test_display_formatter_uses_registry_slot(self, monkeypatch):
        from core.services.dynamic_data import format_dynamic_display_value

        sentinel = object()
        spec = get_spec(field_types.TEXT)
        monkeypatch.setitem(
            FIELD_TYPE_REGISTRY,
            field_types.TEXT,
            replace(spec, format_display=lambda value, template: sentinel),
        )

        template = SimpleNamespace(
            field_type=field_types.TEXT,
            FieldType=SimpleNamespace(BOOLEAN="boolean", SELECT="select", MULTI_SELECT="multi_select"),
            options_json=[],
        )
        assert format_dynamic_display_value("raw", template) is sentinel
