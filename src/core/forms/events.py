"""Forms for event recording."""

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import Case, DocumentType, DocumentTypeField, FieldTemplate


class EventMetaForm(forms.Form):
    """Event metadata (document type, client, timestamp)."""

    document_type = forms.ModelChoiceField(
        queryset=DocumentType.objects.none(),
        label=_("Dokumentationstyp"),
        widget=forms.Select(
            attrs={
                "class": "w-full border border-gray-300 rounded-md px-3 py-2",
                "hx-get": "",  # wird im View gesetzt
                "hx-target": "#dynamic-fields",
                "hx-trigger": "change",
            }
        ),
    )
    client = forms.UUIDField(
        required=False,
        label=_("Klientel"),
        widget=forms.HiddenInput(),
    )
    occurred_at = forms.DateTimeField(
        label=_("Zeitpunkt"),
        widget=forms.DateTimeInput(
            attrs={
                "type": "datetime-local",
                "class": "w-full border border-gray-300 rounded-md px-3 py-2",
                "tabindex": "4",
            },
        ),
    )
    case = forms.ModelChoiceField(
        queryset=Case.objects.none(),
        required=False,
        label=_("Fall"),
        widget=forms.Select(attrs={"class": "w-full border border-gray-300 rounded-md px-3 py-2"}),
    )

    def __init__(self, *args, facility=None, **kwargs):
        initial = kwargs.get("initial", {})
        if "occurred_at" not in initial:
            initial["occurred_at"] = timezone.localtime(timezone.now()).strftime("%Y-%m-%dT%H:%M")
            kwargs["initial"] = initial
        super().__init__(*args, **kwargs)
        self._facility = facility
        if facility:
            self.fields["document_type"].queryset = DocumentType.objects.for_facility(facility).filter(is_active=True)
            self.fields["case"].queryset = Case.objects.filter(facility=facility, status=Case.Status.OPEN)

    def clean(self):
        cleaned = super().clean()
        return cleaned


class DynamicEventDataForm(forms.Form):
    """Dynamic form based on DocumentType fields."""

    FIELD_TYPE_MAP = {
        FieldTemplate.FieldType.TEXT: (forms.CharField, {"widget": forms.TextInput}),
        FieldTemplate.FieldType.TEXTAREA: (forms.CharField, {"widget": forms.Textarea}),
        FieldTemplate.FieldType.NUMBER: (forms.IntegerField, {"widget": forms.NumberInput}),
        FieldTemplate.FieldType.DATE: (forms.DateField, {"widget": forms.DateInput(attrs={"type": "date"})}),
        FieldTemplate.FieldType.TIME: (forms.TimeField, {"widget": forms.TimeInput(attrs={"type": "time"})}),
        FieldTemplate.FieldType.BOOLEAN: (forms.BooleanField, {"widget": forms.CheckboxInput}),
        FieldTemplate.FieldType.SELECT: (forms.ChoiceField, {"widget": forms.Select}),
        FieldTemplate.FieldType.MULTI_SELECT: (forms.MultipleChoiceField, {"widget": forms.CheckboxSelectMultiple}),
    }

    def __init__(self, *args, document_type=None, initial_data=None, **kwargs):
        super().__init__(*args, **kwargs)
        if not document_type:
            return

        dtf_qs = (
            DocumentTypeField.objects.filter(
                document_type=document_type,
            )
            .select_related("field_template")
            .order_by("sort_order")
        )

        for dtf in dtf_qs:
            ft = dtf.field_template
            field_cls, field_kwargs = self.FIELD_TYPE_MAP.get(
                ft.field_type, (forms.CharField, {"widget": forms.TextInput})
            )

            kwargs_copy = {}
            widget = field_kwargs.get("widget")
            if isinstance(widget, type):
                widget = widget()
            elif widget is None:
                widget = forms.TextInput()
            else:
                widget = widget.__class__(attrs=widget.attrs.copy() if hasattr(widget, "attrs") else {})

            css = "w-full border border-gray-300 rounded-md px-3 py-2"
            if not isinstance(widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
                widget.attrs.setdefault("class", css)
            else:
                widget.attrs.setdefault("class", "rounded border-gray-300")

            kwargs_copy["widget"] = widget
            kwargs_copy["required"] = ft.is_required
            kwargs_copy["label"] = ft.name
            if ft.help_text:
                kwargs_copy["help_text"] = ft.help_text

            if ft.field_type in (FieldTemplate.FieldType.SELECT, FieldTemplate.FieldType.MULTI_SELECT):
                if ft.field_type == FieldTemplate.FieldType.SELECT:
                    choices = [("", "---------")] + list(ft.choices)
                else:
                    choices = list(ft.choices)

                # Bei Bearbeitung: inaktive Werte des Events beibehalten
                if initial_data and ft.slug in initial_data:
                    current_values = initial_data[ft.slug]
                    if not isinstance(current_values, list):
                        current_values = [current_values] if current_values else []
                    for o in ft.options_json or []:
                        if not o.get("is_active", True) and o["slug"] in current_values:
                            choices.append((o["slug"], f"{o['label']} (deaktiviert)"))

                kwargs_copy["choices"] = choices

            field = field_cls(**kwargs_copy)

            if initial_data and ft.slug in initial_data:
                field.initial = initial_data[ft.slug]

            self.fields[ft.slug] = field
