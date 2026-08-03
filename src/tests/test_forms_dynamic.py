"""Tests für die entity-agnostische Basis ``DynamicDataForm`` + ``services.dynamic_data`` (M2/P3, Refs #1058).

M2 macht das dynamische Feldsystem entity-übergreifend: Client/Case bekommen in
späteren Phasen ebenfalls ``document_type`` + ``data_json``. P3 zieht dafür die
bisher event-gebundene Formular- und Service-Logik in entity-neutrale Module um:

* :class:`core.forms.dynamic.DynamicDataForm` — Basis, aus der
  ``DynamicEventDataForm`` nur noch ohne eigenen Body erbt (Charakterisierung:
  der Event-Pfad verhält sich unverändert, siehe Bestandssuiten).
* :mod:`core.services.dynamic_data` — ``remove_restricted_fields``,
  ``build_field_template_lookup``, ``validate_data_json`` (vormals
  ``_validate_data_json``) plus die neue E11-Merge-Policy
  ``merge_dynamic_data``.

Getestet wird hier die **Basis** (über einen Event-``DocumentType`` als Träger,
weil ``DocumentType`` in M2/P3 noch die einzige Feldquelle ist) — nicht der
Event-Pfad selbst; dessen Charakterisierung leisten die unveränderten
Bestandssuiten (``test_event_form*.py``, ``test_option_deactivation.py``, …).

``conftest.py`` bleibt unangetastet (Parallel-Konvention); Testdaten lokal gebaut.
"""

from __future__ import annotations

import datetime as dt
import logging

import pytest
from django import forms

from core.forms.dynamic import DynamicDataForm
from core.forms.events import DynamicEventDataForm, MultipleFileField
from core.models import DocumentType, DocumentTypeField, FieldCondition, FieldTemplate
from core.services.conditions import build_conditions_payload
from core.services.dynamic_data import merge_dynamic_data, validate_data_json

FieldType = FieldTemplate.FieldType
Operator = FieldCondition.Operator


# ---------------------------------------------------------------------------
# Lokale Testdaten-Helfer
# ---------------------------------------------------------------------------


def _ft(facility, name, field_type, **kwargs):
    return FieldTemplate.objects.create(facility=facility, name=name, field_type=field_type, **kwargs)


def _assign(doc_type, field_template, sort_order=0):
    return DocumentTypeField.objects.create(
        document_type=doc_type,
        field_template=field_template,
        sort_order=sort_order,
    )


def _options(*slugs):
    return [{"slug": s, "label": s.upper(), "is_active": True} for s in slugs]


class _NoFileDynamicDataForm(DynamicDataForm):
    """Subklasse für den E8-Pfad: FILE-Felder werden nicht aufgebaut."""

    exclude_file_fields = True


@pytest.fixture
def all_types_doctype(facility):
    """Ein ``DocumentType`` mit je einem Feld pro Feldtyp (alle neun)."""
    dt_obj = DocumentType.objects.create(facility=facility, name="Alle Feldtypen")
    for index, (name, field_type, extra) in enumerate(
        [
            ("Text", FieldType.TEXT, {}),
            ("Bereich", FieldType.TEXTAREA, {}),
            ("Zahl", FieldType.NUMBER, {}),
            ("Datum", FieldType.DATE, {}),
            ("Uhrzeit", FieldType.TIME, {}),
            ("Ja Nein", FieldType.BOOLEAN, {}),
            ("Auswahl", FieldType.SELECT, {"options_json": _options("a", "b")}),
            ("Mehrfach", FieldType.MULTI_SELECT, {"options_json": _options("x", "y")}),
            ("Anhang", FieldType.FILE, {}),
        ]
    ):
        _assign(dt_obj, _ft(facility, name, field_type, **extra), sort_order=index)
    return dt_obj


@pytest.fixture
def cond_doctype(facility):
    """Number-Quelle ``dauer`` + Textarea-Ziel ``notiz`` (eine Bedingung)."""
    dt_obj = DocumentType.objects.create(facility=facility, name="Bedingt")
    _assign(dt_obj, _ft(facility, "Dauer", FieldType.NUMBER), sort_order=0)
    _assign(dt_obj, _ft(facility, "Notiz", FieldType.TEXTAREA, is_required=True), sort_order=1)
    FieldCondition.objects.create(
        target_field=dt_obj.fields.get(field_template__slug="notiz"),
        depends_on_field=dt_obj.fields.get(field_template__slug="dauer"),
        operator=Operator.GREATER_THAN,
        value=10,
        group_index=0,
    )
    return dt_obj


# ---------------------------------------------------------------------------
# DynamicDataForm — Feldaufbau
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDynamicDataFormFieldBuild:
    """Registry-Feldaufbau je Feldtyp — identisch zum bisherigen Event-Formular."""

    def test_all_field_types_are_built(self, facility, all_types_doctype):
        form = DynamicDataForm(document_type=all_types_doctype, facility=facility)
        assert list(form.fields) == [
            "text",
            "bereich",
            "zahl",
            "datum",
            "uhrzeit",
            "ja-nein",
            "auswahl",
            "mehrfach",
            "anhang",
        ]

    @pytest.mark.parametrize(
        ("slug", "expected_cls"),
        [
            ("text", forms.CharField),
            ("bereich", forms.CharField),
            ("zahl", forms.IntegerField),
            ("datum", forms.DateField),
            ("uhrzeit", forms.TimeField),
            ("ja-nein", forms.BooleanField),
            ("auswahl", forms.ChoiceField),
            ("mehrfach", forms.MultipleChoiceField),
            ("anhang", MultipleFileField),
        ],
    )
    def test_field_class_per_type(self, facility, all_types_doctype, slug, expected_cls):
        form = DynamicDataForm(document_type=all_types_doctype, facility=facility)
        assert isinstance(form.fields[slug], expected_cls)

    def test_select_gets_empty_option_multi_select_does_not(self, facility, all_types_doctype):
        form = DynamicDataForm(document_type=all_types_doctype, facility=facility)
        assert list(form.fields["auswahl"].choices)[0] == ("", "---------")
        assert [value for value, _label in form.fields["mehrfach"].choices] == ["x", "y"]

    def test_without_document_type_no_fields(self, facility):
        form = DynamicDataForm(document_type=None, facility=facility)
        assert form.fields == {}

    def test_inactive_option_kept_when_present_in_initial_data(self, facility):
        dt_obj = DocumentType.objects.create(facility=facility, name="Inaktiv")
        ft = _ft(
            facility,
            "Auswahl",
            FieldType.SELECT,
            options_json=[
                {"slug": "a", "label": "A", "is_active": True},
                {"slug": "alt", "label": "Alt", "is_active": False},
            ],
        )
        _assign(dt_obj, ft)

        form = DynamicDataForm(document_type=dt_obj, facility=facility, initial_data={"auswahl": "alt"})
        labels = dict(form.fields["auswahl"].choices)
        assert labels["alt"] == "Alt (deaktiviert)"

    def test_default_value_and_initial_data(self, facility):
        dt_obj = DocumentType.objects.create(facility=facility, name="Defaults")
        _assign(dt_obj, _ft(facility, "Mit Default", FieldType.TEXT, default_value="vorgabe"), sort_order=0)
        _assign(dt_obj, _ft(facility, "Ohne Default", FieldType.TEXT), sort_order=1)

        form = DynamicDataForm(document_type=dt_obj, facility=facility)
        assert form.fields["mit-default"].initial == "vorgabe"
        assert form.fields["ohne-default"].initial is None

        # ``initial_data`` schlägt den Default (Bearbeitungsfall).
        edit = DynamicDataForm(document_type=dt_obj, facility=facility, initial_data={"mit-default": "bestand"})
        assert edit.fields["mit-default"].initial == "bestand"

    def test_required_flag_is_taken_from_field_template(self, facility):
        dt_obj = DocumentType.objects.create(facility=facility, name="Pflicht")
        _assign(dt_obj, _ft(facility, "Pflichtfeld", FieldType.TEXT, is_required=True), sort_order=0)
        _assign(dt_obj, _ft(facility, "Freifeld", FieldType.TEXT), sort_order=1)

        form = DynamicDataForm(document_type=dt_obj, facility=facility)
        assert form.fields["pflichtfeld"].required is True
        assert form.fields["freifeld"].required is False

        bound = DynamicDataForm({"pflichtfeld": "", "freifeld": ""}, document_type=dt_obj, facility=facility)
        assert bound.is_valid() is False
        assert "pflichtfeld" in bound.errors


@pytest.mark.django_db
class TestExcludeFileFields:
    """E8: FILE ist event-gebunden — die Basis darf FILE-Felder überspringen."""

    def test_default_keeps_file_fields(self, facility, all_types_doctype):
        form = DynamicDataForm(document_type=all_types_doctype, facility=facility)
        assert DynamicDataForm.exclude_file_fields is False
        assert "anhang" in form.fields

    def test_exclude_file_fields_drops_file_and_warns(self, facility, all_types_doctype, caplog):
        """FILE fehlt im Formular und der Skip wird geloggt (kein 500er, letzte Verteidigungslinie)."""
        module_logger = logging.getLogger("core.forms.dynamic")
        caplog.set_level(logging.WARNING)
        module_logger.addHandler(caplog.handler)
        try:
            form = _NoFileDynamicDataForm(document_type=all_types_doctype, facility=facility)
        finally:
            module_logger.removeHandler(caplog.handler)

        assert "anhang" not in form.fields
        # Alle übrigen acht Feldtypen bleiben unangetastet.
        assert len(form.fields) == 8
        messages = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
        assert any("anhang" in message for message in messages), messages

    def test_exclude_file_fields_without_file_field_logs_nothing(self, facility, cond_doctype, caplog):
        module_logger = logging.getLogger("core.forms.dynamic")
        caplog.set_level(logging.WARNING)
        module_logger.addHandler(caplog.handler)
        try:
            form = _NoFileDynamicDataForm(document_type=cond_doctype, facility=facility)
        finally:
            module_logger.removeHandler(caplog.handler)

        assert set(form.fields) == {"dauer", "notiz"}
        assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


@pytest.mark.django_db
class TestConditionsPayloadPassThrough:
    """``conditions_payload()`` liefert exakt das, was ``build_conditions_payload`` baut."""

    def test_payload_matches_build_conditions_payload(self, facility, cond_doctype):
        form = DynamicDataForm(document_type=cond_doctype, facility=facility)
        expected = build_conditions_payload(cond_doctype, list(form.fields))
        assert form.conditions_payload() == expected
        assert expected is not None

    def test_subclass_payload_identical_to_base(self, facility, cond_doctype):
        """Die Event-Subklasse erbt das Verhalten unverändert (Charakterisierung)."""
        base = DynamicDataForm(document_type=cond_doctype, facility=facility)
        event = DynamicEventDataForm(document_type=cond_doctype, facility=facility)
        assert event.conditions_payload() == base.conditions_payload()
        assert event.condition_target_slugs == base.condition_target_slugs == {"notiz"}

    def test_payload_is_none_without_conditions(self, facility, all_types_doctype):
        form = DynamicDataForm(document_type=all_types_doctype, facility=facility)
        assert form.conditions_payload() is None
        assert form.condition_target_slugs == set()

    def test_dynamic_event_data_form_is_subclass(self):
        assert issubclass(DynamicEventDataForm, DynamicDataForm)


# ---------------------------------------------------------------------------
# services.dynamic_data — validate_data_json (öffentlicher Name)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestValidateDataJson:
    """Charakterisierung des Alt-Verhaltens unter dem neuen öffentlichen Namen."""

    def test_whitelist_drops_foreign_keys(self, facility, cond_doctype):
        result = validate_data_json(cond_doctype, {"dauer": 5, "fremd": "weg"})
        assert result == {"dauer": 5}

    def test_empty_payload(self, facility, cond_doctype):
        assert validate_data_json(cond_doctype, None) == {}
        assert validate_data_json(cond_doctype, {}) == {}

    def test_date_and_time_are_serialized(self, facility, all_types_doctype):
        result = validate_data_json(
            all_types_doctype,
            {"datum": dt.date(2026, 8, 3), "uhrzeit": dt.time(9, 30)},
        )
        assert result == {"datum": "2026-08-03", "uhrzeit": "09:30:00"}

    def test_file_slug_only_survives_as_marker(self, facility, all_types_doctype):
        marker = {"__files__": True, "entries": [{"id": "abc", "sort": 0}]}
        assert validate_data_json(all_types_doctype, {"anhang": marker}) == {"anhang": marker}
        assert validate_data_json(all_types_doctype, {"anhang": "kein-marker"}) == {}

    def test_old_private_alias_still_points_to_the_same_function(self):
        """Bestands-Importe (``crud.py``, Tests) nutzen weiter ``_validate_data_json``."""
        from core.services.events.fields import _validate_data_json

        assert _validate_data_json is validate_data_json


# ---------------------------------------------------------------------------
# services.dynamic_data — merge_dynamic_data (E11-Policy)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMergeDynamicData:
    """E11: ``data_json`` entsteht komplett aus dem Formular des AKTUELLEN Typs.

    Restricted-Werte überleben nur innerhalb desselben Typs; bei Typwechsel
    fallen Alt-Keys weg, weil die Whitelist des neuen Typs sie verwirft.
    """

    def test_same_type_restricted_value_survives(self, facility, cond_doctype):
        merged = merge_dynamic_data(
            cond_doctype,
            {"dauer": 5},
            old_data={"dauer": 1, "notiz": "geheim"},
            restricted_slugs={"notiz"},
        )
        assert merged == {"dauer": 5, "notiz": "geheim"}

    def test_type_switch_drops_old_keys(self, facility, cond_doctype, all_types_doctype):
        """Alt-Daten des vorherigen Typs überleben den Typwechsel nicht."""
        merged = merge_dynamic_data(
            all_types_doctype,
            {"text": "neu"},
            old_data={"dauer": 1, "notiz": "geheim"},
            restricted_slugs={"notiz", "dauer"},
        )
        assert merged == {"text": "neu"}

    def test_restricted_merge_only_for_slugs_of_current_type(self, facility, cond_doctype):
        """Ein restricted Slug, den der aktuelle Typ nicht kennt, wird verworfen."""
        merged = merge_dynamic_data(
            cond_doctype,
            {"dauer": 5},
            old_data={"notiz": "geheim", "fremd": "wert"},
            restricted_slugs={"notiz", "fremd"},
        )
        assert merged == {"dauer": 5, "notiz": "geheim"}

    def test_empty_old_data(self, facility, cond_doctype):
        assert merge_dynamic_data(cond_doctype, {"dauer": 5}, old_data=None, restricted_slugs={"notiz"}) == {"dauer": 5}
        assert merge_dynamic_data(cond_doctype, {"dauer": 5}, old_data={}, restricted_slugs=set()) == {"dauer": 5}

    def test_cleaned_value_wins_over_old_value_for_visible_field(self, facility, cond_doctype):
        """Nur nicht-sichtbare (restricted) Slugs werden nachgezogen — sichtbare nie."""
        merged = merge_dynamic_data(
            cond_doctype,
            {"dauer": 5, "notiz": "neu"},
            old_data={"dauer": 1, "notiz": "alt"},
            restricted_slugs=set(),
        )
        assert merged == {"dauer": 5, "notiz": "neu"}

    def test_result_is_serialized_like_validate_data_json(self, facility, all_types_doctype):
        merged = merge_dynamic_data(
            all_types_doctype,
            {"datum": dt.date(2026, 8, 3), "fremd": "weg"},
            old_data={},
            restricted_slugs=set(),
        )
        assert merged == {"datum": "2026-08-03"}

    def test_input_dicts_are_not_mutated(self, facility, cond_doctype):
        cleaned = {"dauer": 5}
        old = {"notiz": "geheim"}
        merge_dynamic_data(cond_doctype, cleaned, old_data=old, restricted_slugs={"notiz"})
        assert cleaned == {"dauer": 5}
        assert old == {"notiz": "geheim"}
