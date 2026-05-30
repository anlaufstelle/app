"""Field-Type-Registry — zentrale Definition aller Feldtypen (Refs #907).

Refs #907: Vorher war die Feldtyp-Logik dreifach verteilt — Validierung
und Default-Casting im ``FieldTemplate``-Model, Form-Field-/Widget-Mapping
im ``DynamicEventDataForm``, plus Sonderpfade fuer SELECT/MULTI_SELECT
und FILE. Jede neue Feldart musste an drei Stellen nachgezogen werden.

Diese Registry buendelt pro Feldtyp:

- ``form_field_cls`` — die Django-Form-Field-Klasse.
- ``widget_factory`` — Callable, das eine frische Widget-Instanz liefert
  (verhindert geteilte State zwischen Form-Instanzen).
- ``parse_default`` — wandelt den String ``default_value`` in das
  initial-Form-Value (``None`` bei nicht-parsbarem oder leerem Default).
- ``validate_default`` — Pflichtenpruefung des Defaults vor dem Save;
  raised ``ValidationError`` bei Verstoessen. Bekommt optional einen
  ``context``-Dict (z.B. ``options_json`` fuer SELECT).
- ``allows_default`` — ob der Typ ueberhaupt einen Default unterstuetzt.

Bewusst klein gehalten: kein generisches Schema-Framework, keine Plugin-
Registry, kein Discovery. Wer einen neuen Feldtyp will, ergaenzt eine
Zeile im :data:`FIELD_TYPE_REGISTRY` und das ``FieldType.Choices``-Enum.

Datei-Security (Virus-Scan, MIME-Mismatch, Extension-Allowlist) bleibt
absichtlich im :mod:`core.services.file_vault` — die Form prueft hier
nur die UX-Vorbedingungen (Pflichtfeld, Anzahl Dateien), nicht die
Inhalts-Sicherheit.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, time
from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

# Bewusst keine Import-Schleife auf ``FieldTemplate`` — wir importieren das
# Enum lazy ueber den ``code``-String. ``register()`` und ``get()`` bekommen
# den String, der mit ``FieldTemplate.FieldType.<X>.value`` uebereinstimmt.


@dataclass(frozen=True)
class FieldTypeSpec:
    """Bundle aller pro Feldtyp benoetigten Helfer.

    Caller-Vertrag:

    - ``form_field_cls`` liefert das Django-Form-Field, das wir in
      ``DynamicEventDataForm`` instanziieren.
    - ``widget_factory()`` liefert eine frische Widget-Instanz pro
      Form-Aufruf (kritisch: ``DateInput(attrs={"type": "date"})`` darf
      nicht zwischen Form-Instanzen geteilt werden).
    - ``parse_default(raw)`` wandelt das String-Default in das
      Form-Initial-Value. Bei nicht-parsbarem Input ``None`` — fail-safe.
    - ``validate_default(raw, context)`` raised ``ValidationError`` bei
      Verstoessen. ``context`` kann z.B. ``options_json`` enthalten.
    - ``allows_default`` ist ``False`` fuer Feldtypen, bei denen kein
      Default zulaessig ist (``FILE``).
    """

    code: str
    form_field_cls: type[forms.Field]
    widget_factory: Callable[[], forms.Widget]
    parse_default: Callable[[str], Any]
    validate_default: Callable[[str, dict], None]
    allows_default: bool = True


# ----------------------------------------------------------------------------
# Default-Parser und -Validatoren pro Typ. Jede Funktion bekommt den
# bereits ``strip()``-gepruefte Roh-String und (fuer Validatoren) den
# Kontext. Sie sind absichtlich top-level definiert, damit Tests sie
# einzeln importieren koennen.
# ----------------------------------------------------------------------------


def _parse_text(raw: str) -> Any:
    return raw or None


def _parse_number(raw: str) -> Any:
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None


def _parse_date(raw: str) -> Any:
    try:
        return date.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


def _parse_time(raw: str) -> Any:
    try:
        return time.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


def _parse_boolean(raw: str) -> Any:
    return raw.lower() in {"true", "1"}


def _parse_multi_select(raw: str) -> Any:
    return [v.strip() for v in raw.split(",") if v.strip()] or None


def _parse_file(raw: str) -> Any:  # noqa: ARG001 — Signaturkonformitaet
    return None


def _validate_noop(raw: str, context: dict) -> None:  # noqa: ARG001 — Signaturkonformitaet
    return None


def _validate_number(raw: str, context: dict) -> None:  # noqa: ARG001
    try:
        int(raw)
    except (ValueError, TypeError):
        raise ValidationError({"default_value": _("Default-Wert muss eine ganze Zahl sein.")}) from None


def _validate_date(raw: str, context: dict) -> None:  # noqa: ARG001
    try:
        date.fromisoformat(raw)
    except (ValueError, TypeError):
        raise ValidationError({"default_value": _("Default-Wert muss ein ISO-Datum sein (YYYY-MM-DD).")}) from None


def _validate_time(raw: str, context: dict) -> None:  # noqa: ARG001
    try:
        time.fromisoformat(raw)
    except (ValueError, TypeError):
        raise ValidationError(
            {"default_value": _("Default-Wert muss eine ISO-Uhrzeit sein (HH:MM oder HH:MM:SS).")}
        ) from None


def _validate_boolean(raw: str, context: dict) -> None:  # noqa: ARG001
    if raw.lower() not in {"true", "false", "1", "0"}:
        raise ValidationError({"default_value": _("Default-Wert muss 'true' oder 'false' sein.")})


def _validate_select(raw: str, context: dict) -> None:
    options = context.get("options_json") or []
    active = {o["slug"] for o in options if isinstance(o, dict) and o.get("is_active", True) and "slug" in o}
    if raw not in active:
        raise ValidationError(
            {"default_value": _("Default-Wert '%(value)s' ist kein aktiver Options-Slug.") % {"value": raw}}
        )


def _validate_multi_select(raw: str, context: dict) -> None:
    options = context.get("options_json") or []
    active = {o["slug"] for o in options if isinstance(o, dict) and o.get("is_active", True) and "slug" in o}
    for v in [v.strip() for v in raw.split(",")]:
        if v not in active:
            raise ValidationError(
                {"default_value": _("Default-Wert '%(value)s' ist kein aktiver Options-Slug.") % {"value": v}}
            )


def _validate_file(raw: str, context: dict) -> None:  # noqa: ARG001
    # Wird nie erreicht — `allows_default=False` filtert vorab.
    raise ValidationError({"default_value": _("Für Datei-Felder ist kein Default-Wert zulässig.")})


# ----------------------------------------------------------------------------
# Widget-Factories. Pro Form-Aufruf einmal aufgerufen; nie geteilt.
# Die DynamicEventDataForm ergaenzt CSS-Klassen on-top.
# ----------------------------------------------------------------------------


def _widget_text() -> forms.Widget:
    return forms.TextInput()


def _widget_textarea() -> forms.Widget:
    return forms.Textarea()


def _widget_number() -> forms.Widget:
    return forms.NumberInput()


def _widget_date() -> forms.Widget:
    return forms.DateInput(attrs={"type": "date"})


def _widget_time() -> forms.Widget:
    return forms.TimeInput(attrs={"type": "time"})


def _widget_boolean() -> forms.Widget:
    return forms.CheckboxInput()


def _widget_select() -> forms.Widget:
    return forms.Select()


def _widget_multi_select() -> forms.Widget:
    return forms.CheckboxSelectMultiple()


def _widget_file() -> forms.Widget:
    # ``MultipleFileInput`` lebt im Forms-Layer (Django-spezifischer Hack).
    # Import lazy, damit das Registry-Modul keine Form-Abhaengigkeit hat.
    from core.forms.events import MultipleFileInput

    return MultipleFileInput()


# ----------------------------------------------------------------------------
# Form-Field-Klassen. ``MultipleFileField`` ebenfalls lazy ueber Lookup.
# ----------------------------------------------------------------------------


def _multiple_file_field() -> type[forms.Field]:
    from core.forms.events import MultipleFileField

    return MultipleFileField


# Codes spiegeln ``FieldTemplate.FieldType.choices`` (Refs models/document_type.py).
# Wir nutzen Strings, um den zirkulaeren Model-Import zu vermeiden — der
# Architekturtest sichert, dass beide Listen synchron bleiben (siehe
# tests/test_architecture.py::TestFieldTypeRegistryCompleteness).
TEXT = "text"
TEXTAREA = "textarea"
NUMBER = "number"
DATE = "date"
TIME = "time"
BOOLEAN = "boolean"
SELECT = "select"
MULTI_SELECT = "multi_select"
FILE = "file"


FIELD_TYPE_REGISTRY: dict[str, FieldTypeSpec] = {
    TEXT: FieldTypeSpec(
        code=TEXT,
        form_field_cls=forms.CharField,
        widget_factory=_widget_text,
        parse_default=_parse_text,
        validate_default=_validate_noop,
    ),
    TEXTAREA: FieldTypeSpec(
        code=TEXTAREA,
        form_field_cls=forms.CharField,
        widget_factory=_widget_textarea,
        parse_default=_parse_text,
        validate_default=_validate_noop,
    ),
    NUMBER: FieldTypeSpec(
        code=NUMBER,
        form_field_cls=forms.IntegerField,
        widget_factory=_widget_number,
        parse_default=_parse_number,
        validate_default=_validate_number,
    ),
    DATE: FieldTypeSpec(
        code=DATE,
        form_field_cls=forms.DateField,
        widget_factory=_widget_date,
        parse_default=_parse_date,
        validate_default=_validate_date,
    ),
    TIME: FieldTypeSpec(
        code=TIME,
        form_field_cls=forms.TimeField,
        widget_factory=_widget_time,
        parse_default=_parse_time,
        validate_default=_validate_time,
    ),
    BOOLEAN: FieldTypeSpec(
        code=BOOLEAN,
        form_field_cls=forms.BooleanField,
        widget_factory=_widget_boolean,
        parse_default=_parse_boolean,
        validate_default=_validate_boolean,
    ),
    SELECT: FieldTypeSpec(
        code=SELECT,
        form_field_cls=forms.ChoiceField,
        widget_factory=_widget_select,
        parse_default=_parse_text,
        validate_default=_validate_select,
    ),
    MULTI_SELECT: FieldTypeSpec(
        code=MULTI_SELECT,
        form_field_cls=forms.MultipleChoiceField,
        widget_factory=_widget_multi_select,
        parse_default=_parse_multi_select,
        validate_default=_validate_multi_select,
    ),
    FILE: FieldTypeSpec(
        # ``form_field_cls`` ist intentional ``forms.FileField`` als generischer
        # Marker — DynamicEventDataForm uebernimmt fuer FILE den
        # MultipleFileField direkt (Multi-Upload-Spezialisierung).
        code=FILE,
        form_field_cls=forms.FileField,
        widget_factory=_widget_file,
        parse_default=_parse_file,
        validate_default=_validate_file,
        allows_default=False,
    ),
}


def get_spec(field_type: str) -> FieldTypeSpec:
    """Liefere den :class:`FieldTypeSpec` fuer ein ``FieldTemplate.field_type``.

    Fallback auf ``TEXT``, damit unbekannte/legacy Feldtypen nicht crashen —
    konsistent mit der ``DynamicEventDataForm.FIELD_TYPE_MAP.get(...)``-
    Default-Logik vor #907.
    """
    return FIELD_TYPE_REGISTRY.get(field_type) or FIELD_TYPE_REGISTRY[TEXT]


def get_form_field_cls_for_file() -> type[forms.Field]:
    """MultipleFileField, lazy aufgeloest (Forms-Layer-Abhaengigkeit)."""
    return _multiple_file_field()


__all__ = [
    "BOOLEAN",
    "DATE",
    "FIELD_TYPE_REGISTRY",
    "FILE",
    "FieldTypeSpec",
    "MULTI_SELECT",
    "NUMBER",
    "SELECT",
    "TEXT",
    "TEXTAREA",
    "TIME",
    "get_form_field_cls_for_file",
    "get_spec",
]
