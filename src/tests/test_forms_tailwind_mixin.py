"""Widget-Klassen der Tailwind-Formulare (Refs #1834).

``INPUT_CSS`` ist seit #733 zentral definiert, wurde aber 51-mal von Hand als
``attrs={"class": INPUT_CSS}`` in acht Modulen wiederholt — der passende
``setdefault``-Helfer existierte nur im dynamischen Zweig
(``core/forms/dynamic.py``). :class:`~core.forms.mixins.TailwindFormMixin`
trägt die Klasse jetzt einmal über ``self.fields``.

Diese Datei ist das Sicherheitsnetz der Zusammenführung: sie hält für **jedes**
betroffene Formular fest, welches Feld die Eingabe-Klasse trägt, welches nicht,
und welche Zusatz-Attribute (HTMX, ``data-testid``, ``rows``, ``accept``,
``min``/``max``, ``input_type``) daran hängen. Ein Feld, das nach
dem Umbau seine Klasse verliert — oder eines, das plötzlich eine bekommt
(Checkbox, Hidden, Radio) — fällt hier auf, nicht erst in der Oberfläche.

Die Erwartungswerte sind der **Ist-Stand vor dem Umbau**, Feld für Feld aus den
Formularen ausgelesen.
"""

import pytest

from core.forms.cases import CaseForm
from core.forms.clients import ClientForm
from core.forms.config import (
    DocumentTypeFieldAddForm,
    DocumentTypeForm,
    FieldConditionForm,
    FieldGroupForm,
    FieldTemplateForm,
)
from core.forms.episodes import EpisodeForm
from core.forms.events import EventMetaForm
from core.forms.import_data import ImportUploadForm
from core.forms.mixins import TailwindFormMixin
from core.forms.rectification import RectificationRequestForm
from core.forms.user_access import UserAccessCreateForm, UserAccessUpdateForm
from core.forms.widgets import INPUT_CSS
from core.forms.workitems import WorkItemForm

pytestmark = pytest.mark.django_db

#: Attributwert ist laufzeitabhängig (Datum, reverse()-URL) — nur der Key zählt.
ANY = ...

FORM_FACTORIES = {
    "CaseForm": lambda: CaseForm(facility=None, user=None),
    "ClientForm": lambda: ClientForm(facility=None, user=None),
    "DocumentTypeForm": lambda: DocumentTypeForm(facility=None),
    "FieldTemplateForm": lambda: FieldTemplateForm(facility=None),
    "FieldGroupForm": lambda: FieldGroupForm(doc_type=None),
    "DocumentTypeFieldAddForm": lambda: DocumentTypeFieldAddForm(doc_type=None),
    "FieldConditionForm": lambda: FieldConditionForm(document_type=None),
    "EpisodeForm": EpisodeForm,
    "EventMetaForm": lambda: EventMetaForm(facility=None, user=None),
    "ImportUploadForm": lambda: ImportUploadForm(facility=None),
    "RectificationRequestForm": lambda: RectificationRequestForm(client=None, user=None),
    "UserAccessCreateForm": lambda: UserAccessCreateForm(facility=None, actor=None),
    "UserAccessUpdateForm": lambda: UserAccessUpdateForm(facility=None, actor=None),
    "WorkItemForm": lambda: WorkItemForm(facility=None),
}

#: form -> field -> (Widget-Klasse, traegt INPUT_CSS, uebrige attrs)
EXPECTED = {
    "CaseForm": {
        "title": ("TextInput", True, {"maxlength": "200"}),
        "description": ("Textarea", True, {"rows": 4, "cols": "40"}),
        "lead_user": ("Select", True, {}),
        "document_type": (
            "Select",
            True,
            {
                "hx-get": ANY,
                "hx-target": "#dynamic-fields",
                "hx-trigger": "change",
                "hx-include": "[name='document_type']",
            },
        ),
        "client": ("HiddenInput", False, {}),
    },
    "ClientForm": {
        "pseudonym": ("TextInput", True, {"maxlength": "100"}),
        "contact_stage": ("Select", True, {}),
        "age_cluster": ("Select", True, {}),
        "notes": ("Textarea", True, {"rows": 4, "cols": "40"}),
        "document_type": (
            "Select",
            True,
            {
                "hx-get": ANY,
                "hx-target": "#dynamic-fields",
                "hx-trigger": "change",
                "hx-include": "[name='document_type']",
            },
        ),
    },
    "DocumentTypeForm": {
        "name": ("TextInput", True, {"maxlength": "200"}),
        "entity_type": ("Select", True, {}),
        "sensitivity": ("Select", True, {}),
        "system_type": ("Select", True, {}),
        "report_key": ("Select", True, {}),
        "min_contact_stage": ("Select", True, {}),
        "retention_days": ("NumberInput", True, {"min": 1}),
        "description": ("Textarea", True, {"rows": 3, "cols": "40"}),
        "icon": ("TextInput", True, {"maxlength": "50"}),
        "color": ("TextInput", True, {"maxlength": "20"}),
        "sort_order": ("NumberInput", True, {}),
        "is_active": ("CheckboxInput", False, {}),
        "tally_enabled": ("CheckboxInput", False, {}),
    },
    "FieldTemplateForm": {
        "name": ("TextInput", True, {"maxlength": "200"}),
        "field_type": ("Select", True, {}),
        "min_value": ("NumberInput", True, {}),
        "max_value": ("NumberInput", True, {}),
        "is_required": ("CheckboxInput", False, {}),
        "is_encrypted": ("CheckboxInput", False, {}),
        "sensitivity": ("Select", True, {}),
        "options_json": ("Textarea", True, {"rows": 3, "cols": "40"}),
        "default_value": ("TextInput", True, {}),
        "report_key": ("Select", True, {}),
        "help_text": ("TextInput", True, {"maxlength": "200"}),
        "is_active": ("CheckboxInput", False, {}),
    },
    "FieldGroupForm": {
        "name": ("TextInput", True, {"maxlength": "200"}),
        "is_collapsible": ("CheckboxInput", False, {}),
    },
    "DocumentTypeFieldAddForm": {
        "field_template": ("Select", True, {}),
        "field_group": ("Select", True, {}),
    },
    "FieldConditionForm": {
        "target_field": ("Select", True, {"data-testid": "condition-target"}),
        "depends_on_field": ("Select", True, {"data-testid": "condition-source"}),
        # Opt-out 1: gerendert wird der Operator ueber das HTMX-Partial
        # ``condition_operator_input.html`` (#1715), nicht ueber dieses Widget.
        "operator": ("Select", False, {}),
        "value": ("TextInput", True, {"data-testid": "condition-value"}),
        "group_index": ("HiddenInput", False, {"data-testid": "condition-group-index"}),
    },
    "EpisodeForm": {
        "title": ("TextInput", True, {"maxlength": "200"}),
        "description": ("Textarea", True, {"rows": 4, "cols": "40"}),
        "started_at": ("DateInput", True, {}),
        "ended_at": ("DateInput", True, {}),
    },
    "EventMetaForm": {
        "document_type": ("Select", True, {"hx-get": "", "hx-target": "#dynamic-fields", "hx-trigger": "change"}),
        "client": ("HiddenInput", False, {}),
        "occurred_at": ("DateTimeInput", True, {}),
        "case": ("Select", True, {}),
    },
    "ImportUploadForm": {
        # Opt-out 2: Radio-Gruppe — die Eingabe-Klasse ist fuer Text-/Select-
        # Felder gedacht und wuerde die Radios ueber die volle Breite ziehen.
        "target_type": ("RadioSelect", False, {}),
        "document_type": ("Select", True, {}),
        "source_file": ("ClearableFileInput", True, {"accept": ".csv,text/csv"}),
        "delimiter": ("Select", True, {}),
        "encoding": ("Select", True, {}),
    },
    "RectificationRequestForm": {
        "field_path": ("Select", True, {}),
        "new_value": ("Textarea", True, {"rows": 2, "cols": "40"}),
        "reason": ("Textarea", True, {"rows": 4, "cols": "40"}),
        # Refs #2045: Art.-18-Opt-in im Antragsformular (unvorbelegt).
        "restrict_processing": ("CheckboxInput", False, {}),
    },
    "UserAccessCreateForm": {
        "username": ("TextInput", True, {"maxlength": "150"}),
        "email": ("EmailInput", True, {"maxlength": "254"}),
        "display_name": ("TextInput", True, {"maxlength": "100"}),
        "role": ("Select", True, {}),
        "mfa_required": ("CheckboxInput", False, {}),
        "can_confirm_deletion": ("CheckboxInput", False, {}),
    },
    "UserAccessUpdateForm": {
        "username": ("TextInput", True, {"maxlength": "150"}),
        "email": ("EmailInput", True, {"maxlength": "254"}),
        "display_name": ("TextInput", True, {"maxlength": "100"}),
        "role": ("Select", True, {}),
        "is_active": ("CheckboxInput", False, {}),
        "mfa_required": ("CheckboxInput", False, {}),
        "can_confirm_deletion": ("CheckboxInput", False, {}),
    },
    "WorkItemForm": {
        "item_type": ("Select", True, {}),
        "title": ("TextInput", True, {"maxlength": "200"}),
        "description": ("Textarea", True, {"rows": 4, "cols": "40"}),
        "priority": ("Select", True, {}),
        "due_date": (
            "DateInput",
            True,
            {"min": ANY, "max": ANY, "data-msg-too-early": ANY, "data-msg-too-late": ANY},
        ),
        "remind_at": (
            "DateInput",
            True,
            {"min": ANY, "max": ANY, "data-msg-too-early": ANY, "data-msg-too-late": ANY},
        ),
        "recurrence": ("Select", True, {}),
        "assigned_to": ("Select", True, {}),
        "client": ("HiddenInput", False, {}),
    },
}

#: Felder, deren ``type=``-Attribut Django in ``widget.input_type`` verschiebt.
EXPECTED_INPUT_TYPES = {
    ("EpisodeForm", "started_at"): "date",
    ("EpisodeForm", "ended_at"): "date",
    ("EventMetaForm", "occurred_at"): "datetime-local",
    ("WorkItemForm", "due_date"): "date",
    ("WorkItemForm", "remind_at"): "date",
}

FORM_NAMES = sorted(FORM_FACTORIES)


@pytest.mark.parametrize("form_name", FORM_NAMES)
class TestWidgetAttributesSurviveTheMixin:
    def test_form_uses_the_mixin(self, form_name):
        assert isinstance(FORM_FACTORIES[form_name](), TailwindFormMixin)

    def test_every_field_is_accounted_for(self, form_name):
        """Kein Feld darf am Erwartungs-Katalog vorbei entstehen oder verschwinden."""
        form = FORM_FACTORIES[form_name]()
        assert set(form.fields) == set(EXPECTED[form_name])

    def test_input_class_is_applied_exactly_where_it_was(self, form_name):
        form = FORM_FACTORIES[form_name]()
        for field_name, (_widget, has_css, _extra) in EXPECTED[form_name].items():
            css = form.fields[field_name].widget.attrs.get("class")
            if has_css:
                assert css == INPUT_CSS, f"{form_name}.{field_name} verlor die Eingabe-Klasse"
            else:
                assert css is None, f"{form_name}.{field_name} bekam unerwartet die Eingabe-Klasse {css!r}"

    def test_widget_type_and_extra_attributes_are_unchanged(self, form_name):
        form = FORM_FACTORIES[form_name]()
        for field_name, (widget_name, _has_css, extra) in EXPECTED[form_name].items():
            widget = form.fields[field_name].widget
            assert type(widget).__name__ == widget_name, f"{form_name}.{field_name}: Widget-Typ gewechselt"
            attrs = dict(widget.attrs)
            attrs.pop("class", None)
            assert set(attrs) == set(extra), f"{form_name}.{field_name}: Attribut-Menge gewandert"
            for key, value in extra.items():
                if value is not ANY:
                    assert attrs[key] == value, f"{form_name}.{field_name}: {key} gewandert"
            expected_input_type = EXPECTED_INPUT_TYPES.get((form_name, field_name))
            if expected_input_type:
                assert widget.input_type == expected_input_type


class TestDocumentedOptOuts:
    """Zwei Felder bekommen die Eingabe-Klasse bewusst NICHT."""

    def test_opt_outs_are_declared_on_the_form(self):
        assert ImportUploadForm.tailwind_opt_out == ("target_type",)
        assert FieldConditionForm.tailwind_opt_out == ("operator",)

    def test_radio_group_stays_unstyled(self):
        widget = ImportUploadForm(facility=None).fields["target_type"].widget
        assert "class" not in widget.attrs

    def test_htmx_rendered_operator_stays_unstyled(self):
        widget = FieldConditionForm(document_type=None).fields["operator"].widget
        assert "class" not in widget.attrs


class TestMixinMechanics:
    def test_existing_class_is_not_overwritten(self):
        """``setdefault``-Semantik: ein eigens gesetztes ``class`` gewinnt."""
        from django import forms

        class CustomForm(TailwindFormMixin, forms.Form):
            special = forms.CharField(widget=forms.TextInput(attrs={"class": "eigene-klasse"}))
            normal = forms.CharField()

        form = CustomForm()
        assert form.fields["special"].widget.attrs["class"] == "eigene-klasse"
        assert form.fields["normal"].widget.attrs["class"] == INPUT_CSS

    def test_hidden_and_checkbox_widgets_stay_unstyled(self):
        from django import forms

        class CustomForm(TailwindFormMixin, forms.Form):
            hidden = forms.CharField(widget=forms.HiddenInput())
            flag = forms.BooleanField(required=False)

        form = CustomForm()
        assert "class" not in form.fields["hidden"].widget.attrs
        assert "class" not in form.fields["flag"].widget.attrs
