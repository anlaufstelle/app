"""Forms for event recording."""

from typing import Any

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.constants import DEFAULT_ALLOWED_FILE_TYPES, DEFAULT_MAX_FILE_SIZE_MB
from core.forms.widgets import INPUT_CSS
from core.models import Case, DocumentType, DocumentTypeField
from core.models.settings import Settings


class MultipleFileInput(forms.ClearableFileInput):
    """ClearableFileInput-Widget mit ``multiple``-Attribut — Django erlaubt
    Multi-File-Upload seit 5.0, aber der Standard-Widget blockiert das aktiv.
    Refs #622.
    """

    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """FileField, das eine Liste von UploadedFile zurückgibt (auch für N=0/1).

    Wird für FILE-Feldtypen in :class:`DynamicEventDataForm` verwendet, damit
    mehrere Dateien pro Feld hochgeladen werden können (Refs #622).
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_clean(d, initial) for d in data if d]
        if data:
            return [single_clean(data, initial)]
        return []


def _case_label(case: Case) -> str:
    """Dropdown label: title plus client pseudonym so users can distinguish cases."""
    pseudonym = case.client.pseudonym if case.client_id else None
    return f"{case.title} – {pseudonym}" if pseudonym else case.title


class EventMetaForm(forms.Form):
    """Event metadata (document type, client, timestamp)."""

    document_type = forms.ModelChoiceField(
        queryset=DocumentType.objects.none(),
        label=_("Dokumentationstyp"),  # pragma: no mutate
        widget=forms.Select(
            attrs={
                "class": INPUT_CSS,
                "hx-get": "",  # wird im View gesetzt
                "hx-target": "#dynamic-fields",
                "hx-trigger": "change",
            }
        ),
    )
    client = forms.UUIDField(
        required=False,
        label=_("Person"),  # pragma: no mutate
        widget=forms.HiddenInput(),
    )
    occurred_at = forms.DateTimeField(
        label=_("Zeitpunkt"),  # pragma: no mutate
        widget=forms.DateTimeInput(
            attrs={
                "type": "datetime-local",
                "class": INPUT_CSS,
                "tabindex": "4",
            },
        ),
    )
    case = forms.ModelChoiceField(
        queryset=Case.objects.none(),
        required=False,
        label=_("Fall"),  # pragma: no mutate
        empty_label=_("– Keinem Fall zuordnen –"),
        widget=forms.Select(attrs={"class": INPUT_CSS}),
    )

    def __init__(self, *args, facility=None, user=None, **kwargs):
        initial = kwargs.get("initial", {})
        if "occurred_at" not in initial:
            initial["occurred_at"] = timezone.localtime(timezone.now()).strftime("%Y-%m-%dT%H:%M")
            kwargs["initial"] = initial
        super().__init__(*args, **kwargs)
        self._facility = facility
        self._user = user
        if facility:
            qs = DocumentType.objects.for_facility(facility).filter(is_active=True)
            if user is not None:
                from core.services.compliance import allowed_sensitivities_for_user

                qs = qs.filter(sensitivity__in=allowed_sensitivities_for_user(user))
            self.fields["document_type"].queryset = qs  # type: ignore[attr-defined]
            case_qs = Case.objects.for_facility(facility).filter(status=Case.Status.OPEN).select_related("client")
            self.fields["case"].queryset = case_qs  # type: ignore[attr-defined]
            self.fields["case"].label_from_instance = _case_label  # type: ignore[attr-defined]

    def clean(self):
        cleaned = super().clean()
        return cleaned


class DynamicEventDataForm(forms.Form):
    """Dynamic form based on DocumentType fields.

    Refs #907: Form-Field-, Widget-, Default-Auswahl pro Feldtyp
    laeuft ueber :data:`core.services.system.field_types.FIELD_TYPE_REGISTRY` —
    die alte ``FIELD_TYPE_MAP`` ist ersetzt. SELECT/MULTI_SELECT-Choices
    und die FILE-Sonderlogik bleiben in dieser Form, da sie auf Form-State
    (``initial_data``, deaktivierte Options) zugreifen.
    """

    def __init__(self, *args, document_type=None, initial_data=None, facility=None, **kwargs):
        self.facility = facility
        super().__init__(*args, **kwargs)
        if not document_type:
            return

        from core.services.system import (
            FILE,
            MULTI_SELECT,
            SELECT,
            get_form_field_cls_for_file,
            get_spec,
        )

        dtf_qs = (
            DocumentTypeField.objects.filter(
                document_type=document_type,
            )
            .select_related("field_template")
            .order_by("sort_order")
        )

        for dtf in dtf_qs:
            ft = dtf.field_template
            spec = get_spec(ft.field_type)

            # FILE bekommt den MultipleFileField — der unterstuetzt
            # Multi-Upload (Refs #622). Der Registry-Eintrag fuer FILE
            # nennt nur ``forms.FileField`` als generischen Marker.
            if ft.field_type == FILE:
                field_cls: type[forms.Field] = get_form_field_cls_for_file()
            else:
                field_cls = spec.form_field_cls

            widget = spec.widget_factory()

            css = INPUT_CSS
            if not isinstance(widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
                widget.attrs.setdefault("class", css)
            else:
                widget.attrs.setdefault("class", "rounded border-subtle text-accent")

            kwargs_copy: dict[str, Any] = {
                "widget": widget,
                "required": ft.is_required,
                "label": ft.name,
            }
            if ft.help_text:
                kwargs_copy["help_text"] = ft.help_text

            if ft.field_type in (SELECT, MULTI_SELECT):
                choices = [("", "---------")] + list(ft.choices) if ft.field_type == SELECT else list(ft.choices)

                # Bei Bearbeitung: inaktive Werte des Events beibehalten
                if initial_data and ft.slug in initial_data:
                    current_values = initial_data[ft.slug]
                    if not isinstance(current_values, list):
                        current_values = [current_values] if current_values else []
                    for o in ft.options_json or []:
                        if not o.get("is_active", True) and o["slug"] in current_values:
                            choices.append((o["slug"], f"{o['label']} (deaktiviert)"))

                kwargs_copy["choices"] = choices

            field = field_cls(**kwargs_copy)  # type: ignore[arg-type]

            if initial_data and ft.slug in initial_data:
                field.initial = initial_data[ft.slug]
            elif spec.allows_default:
                default_initial = ft.get_default_initial()
                if default_initial is not None:
                    field.initial = default_initial

            self.fields[ft.slug] = field

    def clean(self):
        cleaned = super().clean() or {}
        if not self.facility:
            return cleaned
        # Refs #771 — fail-closed: fehlende Settings oder leere Whitelist greifen
        # auf DEFAULT_ALLOWED_FILE_TYPES / DEFAULT_MAX_FILE_SIZE_MB zurueck,
        # statt jeden Upload stillschweigend durchzulassen.
        try:
            facility_settings = Settings.objects.get(facility=self.facility)
        except Settings.DoesNotExist:
            facility_settings = None
        raw = (facility_settings.allowed_file_types or "") if facility_settings else ""
        allowed = {ext.strip().lower().lstrip(".") for ext in raw.split(",") if ext.strip()}
        if not allowed:
            allowed = set(DEFAULT_ALLOWED_FILE_TYPES)
        max_mb = facility_settings.max_file_size_mb if facility_settings else DEFAULT_MAX_FILE_SIZE_MB
        max_bytes = max_mb * 1024 * 1024
        for field_name, field_obj in self.fields.items():
            if not isinstance(field_obj, forms.FileField):
                continue
            value = cleaned.get(field_name)
            if not value:
                continue
            # MultipleFileField \u2192 Liste; klassisches FileField \u2192 Einzelobjekt.
            uploads = value if isinstance(value, list) else [value]
            for uploaded in uploads:
                if not uploaded:
                    continue
                ext = uploaded.name.rsplit(".", 1)[-1].lower() if "." in uploaded.name else ""
                if allowed and ext not in allowed:
                    self.add_error(
                        field_name,
                        _("Dateityp .%(ext)s nicht erlaubt. Erlaubt: %(allowed)s")  # pragma: no mutate
                        % {"ext": ext, "allowed": ", ".join(sorted(allowed))},
                    )
                if uploaded.size > max_bytes:
                    self.add_error(
                        field_name,
                        _("Datei zu gro\u00df (%(size)d MB). Maximum: %(max)d MB")  # pragma: no mutate
                        % {
                            "size": uploaded.size // (1024 * 1024),
                            "max": max_mb,
                        },
                    )
        return cleaned
