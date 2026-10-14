"""Application-language entry metadata with native ISO fallback (Refs #2603)."""

from django import forms
from django.utils.translation import get_language
from django.utils.translation import gettext as _


def date_input_attributes(kind="date", *, seconds=False):
    """Describe presentation only; the original input remains the ISO transport."""
    language = (get_language() or "de").split("-")[0]
    date_format = "TT.MM.JJJJ" if language == "de" else "YYYY-MM-DD"
    time_format = "HH:MM:SS" if seconds else "HH:MM"
    placeholder = {
        "date": date_format,
        "time": time_format,
        "datetime-local": f"{date_format} {time_format}",
    }[kind]
    return {
        "x-app-date": "",
        "data-date-language": language,
        "data-date-placeholder": placeholder,
        "data-date-time-placeholder": time_format,
        "data-date-invalid": _("Bitte ein gültiges Datum bzw. eine gültige Uhrzeit im angegebenen Format eingeben."),
        "data-date-required": _("Bitte dieses Feld ausfüllen."),
        "data-date-range": _("Der Wert liegt außerhalb des erlaubten Bereichs."),
        "data-date-step": _("Bitte die angegebene Zeitgenauigkeit einhalten."),
    }


class AppDateInput(forms.DateInput):
    """Date input with ISO value and language-specific presentation metadata."""

    input_type = "date"

    def __init__(self, attrs=None, format="%Y-%m-%d"):
        super().__init__(attrs=attrs, format=format)

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["widget"]["attrs"].update(date_input_attributes())
        return context


class AppTimeInput(forms.TimeInput):
    """Time input retaining second precision."""

    input_type = "time"

    def __init__(self, attrs=None, format="%H:%M:%S"):
        super().__init__(attrs={"step": "1", **(attrs or {})}, format=format)

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["widget"]["attrs"].update(date_input_attributes("time", seconds=True))
        return context


class AppDateTimeInput(forms.DateTimeInput):
    """Local wall-clock entry; timezone and fold validation stay on the field."""

    input_type = "datetime-local"

    def __init__(self, attrs=None, format="%Y-%m-%dT%H:%M:%S"):
        super().__init__(attrs={"step": "1", **(attrs or {})}, format=format)

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["widget"]["attrs"].update(date_input_attributes("datetime-local", seconds=True))
        return context
