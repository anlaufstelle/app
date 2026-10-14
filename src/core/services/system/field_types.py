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
- ``coerce_import`` — toleranter CSV-String-Parser des Importers.
- ``parse_literal`` — strikter Parser fuer Vergleichswerte im Bedingungseditor.
- ``format_display`` — lesbare Darstellung gespeicherter dynamischer Werte.

``conditions.normalize_value`` bleibt bewusst ausserhalb der Registry: Der
Evaluator-Kern muss ohne Django-App-Registry importierbar bleiben und bildet
bereits gespeicherte Python-/JSON-Werte ab, nicht Eingaben einer Django-Schicht.

Bewusst klein gehalten: kein generisches Schema-Framework, keine Plugin-
Registry, kein Discovery. Wer einen neuen Feldtyp will, ergaenzt eine
Zeile im :data:`FIELD_TYPE_REGISTRY` und das ``FieldType.Choices``-Enum.

Datei-Security (Virus-Scan, MIME-Mismatch, Extension-Allowlist) bleibt
absichtlich im :mod:`core.services.file_vault` — die Form prueft hier
nur die UX-Vorbedingungen (Pflichtfeld, Anzahl Dateien), nicht die
Inhalts-Sicherheit.
"""

from __future__ import annotations

import datetime
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, time
from typing import TYPE_CHECKING, Any

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.utils.date_input import AppDateInput, AppTimeInput

if TYPE_CHECKING:
    from django.utils.functional import _StrPromise

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
    - ``coerce_import(template, raw, column)`` bildet tolerante CSV-Werte auf
      den jeweiligen ``data_json``-Typ ab.
    - ``parse_literal(template, raw)`` parst Editor-Vergleichswerte strikt.
    - ``format_display(value, template)`` formatiert gespeicherte Werte fuer
      die Anzeige.
    """

    code: str
    form_field_cls: type[forms.Field]
    widget_factory: Callable[[], forms.Widget]
    parse_default: Callable[[str], Any]
    validate_default: Callable[[str, dict], None]
    coerce_import: Callable[[Any, str, str | None], Any]
    parse_literal: Callable[[Any, str], Any]
    format_display: Callable[[Any, Any], Any]
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
        value = int(raw)
    except (ValueError, TypeError):
        raise ValidationError({"default_value": _("Default-Wert muss eine ganze Zahl sein.")}) from None
    validate_number_value(value, context.get("min_value"), context.get("max_value"), error_key="default_value")


def validate_number_value(
    value: int | float,
    min_value: int | None,
    max_value: int | None,
    *,
    error_key: str | None = None,
) -> None:
    """Validiere einen ganzzahligen Wert gegen optionale Feldgrenzen.

    Der Helfer ist die gemeinsame Server-Quelle für Default-Werte und den
    service-seitigen ``data_json``-Schreibpfad. Formulare setzen dieselben
    Grenzen zusätzlich als HTML-Eingabehilfe.
    """
    message: str | _StrPromise | None = None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or (isinstance(value, float) and not math.isfinite(value))
    ):
        message = _("Wert muss eine ganze Zahl sein.")
    elif min_value is not None and value < min_value:
        message = _("Wert muss mindestens %(minimum)d sein.") % {"minimum": min_value}
    elif max_value is not None and value > max_value:
        message = _("Wert darf höchstens %(maximum)d sein.") % {"maximum": max_value}
    if message is not None:
        if error_key is not None:
            raise ValidationError({error_key: message})
        raise ValidationError(message)


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


def active_option_slugs(options_json) -> set[str]:
    """Slugs aller aktiven Optionen aus einer ``options_json``-Liste.

    Refs #1827: identisch dupliziert in ``_validate_select``/
    ``_validate_multi_select`` (hier) sowie
    ``services/dashboard/quick_templates._active_option_slugs`` (dort
    schwaecher gefiltert — ohne ``isinstance(o, dict)``-Guard). Nimmt bewusst
    die rohe ``options_json``-Liste entgegen statt eines ``FieldTemplate``:
    hier kommt sie aus einem ``context``-Dict (Pre-Save-Validierung ohne
    Modell-Instanz), in ``quick_templates.py`` aus ``field_template.options_json``
    — ein gemeinsamer Helfer muss auf dem kleinsten gemeinsamen Nenner
    (der Liste selbst) arbeiten.
    """
    return {o["slug"] for o in (options_json or []) if isinstance(o, dict) and o.get("is_active", True) and "slug" in o}


def _validate_select(raw: str, context: dict) -> None:
    active = active_option_slugs(context.get("options_json"))
    if raw not in active:
        raise ValidationError(
            {"default_value": _("Default-Wert '%(value)s' ist kein aktiver Options-Slug.") % {"value": raw}}
        )


def _validate_multi_select(raw: str, context: dict) -> None:
    active = active_option_slugs(context.get("options_json"))
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
    return AppDateInput(attrs={"type": "date"})


def _widget_time() -> forms.Widget:
    return AppTimeInput(attrs={"type": "time"})


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


# Codes spiegeln ``FieldTemplate.FieldType.choices`` (Refs models/field_template.py).
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


# ----------------------------------------------------------------------------
# Dispatch-Helfer fuer Import, Bedingungs-Literale und Anzeige (Refs #1832).
# Die Import-Parser bleiben im Import-Service: Dessen _CellError und tolerante
# CSV-Konventionen sind kein allgemeiner Registry-Vertrag. Die kleinen Adapter
# werden erst zur Laufzeit aufgerufen und vermeiden so einen Import-Zyklus.
# ----------------------------------------------------------------------------


def _coerce_import_raw(_template: Any, raw: str, _column: str | None) -> str:
    return raw


def _coerce_import_number(_template: Any, raw: str, column: str | None) -> Any:
    from core.services.import_engine import _coerce_number

    return _coerce_number(raw, column)


def _coerce_import_date(_template: Any, raw: str, column: str | None) -> str:
    from core.services.import_engine import _coerce_date

    return _coerce_date(raw, column)


def _coerce_import_time(_template: Any, raw: str, column: str | None) -> str:
    from core.services.import_engine import _coerce_time

    return _coerce_time(raw, column)


def _coerce_import_boolean(_template: Any, raw: str, column: str | None) -> bool:
    from core.services.import_engine import _coerce_boolean

    return _coerce_boolean(raw, column)


def _coerce_import_select(template: Any, raw: str, column: str | None) -> str:
    from core.services.import_engine import _coerce_option

    return _coerce_option(template, raw, column)


def _coerce_import_multi_select(template: Any, raw: str, column: str | None) -> list[str]:
    from core.services.import_engine import _coerce_option

    return [_coerce_option(template, part.strip(), column) for part in raw.split(",") if part.strip()]


def _parse_literal_raw(_template: Any, raw: str) -> str:
    return raw


def _parse_literal_number(_template: Any, raw: str) -> int | float:
    try:
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            raise ValidationError(_("Bitte eine gültige Zahl eingeben.")) from None


def _parse_literal_boolean(_template: Any, raw: str) -> bool:
    low = raw.lower()
    if low in ("true", "1", "ja"):
        return True
    if low in ("false", "0", "nein"):
        return False
    raise ValidationError(_("Bitte „wahr“ oder „falsch“ auswählen."))


def _parse_literal_date(_template: Any, raw: str) -> str:
    try:
        datetime.date.fromisoformat(raw)
    except ValueError:
        raise ValidationError(_("Bitte ein gültiges Datum (JJJJ-MM-TT) eingeben.")) from None
    return raw


def _parse_literal_time(_template: Any, raw: str) -> str:
    try:
        parsed = datetime.time.fromisoformat(raw)
    except ValueError:
        raise ValidationError(_("Bitte eine gültige Uhrzeit eingeben.")) from None
    # E11-Normalisierung: Editor speichert time als „HH:MM:SS" (JS hängt
    # an „HH:MM" ein „:00" an) — sonst schlagen equals-Vergleiche fehl.
    return parsed.isoformat(timespec="seconds")


def _parse_literal_option(template: Any, raw: str) -> str:
    known = {option.get("slug") for option in (template.options_json or [])}
    if raw not in known:
        raise ValidationError(_("Bitte eine gültige Option auswählen."))
    return raw


def _format_identity(value: Any, _template: Any) -> Any:
    return value


def _format_boolean(value: Any, _template: Any) -> Any:
    return _("Ja") if value else _("Nein")


def _format_select(value: Any, template: Any) -> Any:
    if template.options_json:
        from core.services.dynamic_data import option_label_map

        return option_label_map(template.options_json).get(value, value)
    return value


def _format_multi_select(value: Any, template: Any) -> Any:
    if template.options_json and isinstance(value, list):
        from core.services.dynamic_data import option_label_map

        label_map = option_label_map(template.options_json)
        return "\n".join(label_map.get(v, str(v)) for v in value)
    return value


FIELD_TYPE_REGISTRY: dict[str, FieldTypeSpec] = {
    TEXT: FieldTypeSpec(
        code=TEXT,
        form_field_cls=forms.CharField,
        widget_factory=_widget_text,
        parse_default=_parse_text,
        validate_default=_validate_noop,
        coerce_import=_coerce_import_raw,
        parse_literal=_parse_literal_raw,
        format_display=_format_identity,
    ),
    TEXTAREA: FieldTypeSpec(
        code=TEXTAREA,
        form_field_cls=forms.CharField,
        widget_factory=_widget_textarea,
        parse_default=_parse_text,
        validate_default=_validate_noop,
        coerce_import=_coerce_import_raw,
        parse_literal=_parse_literal_raw,
        format_display=_format_identity,
    ),
    NUMBER: FieldTypeSpec(
        code=NUMBER,
        form_field_cls=forms.IntegerField,
        widget_factory=_widget_number,
        parse_default=_parse_number,
        validate_default=_validate_number,
        coerce_import=_coerce_import_number,
        parse_literal=_parse_literal_number,
        format_display=_format_identity,
    ),
    DATE: FieldTypeSpec(
        code=DATE,
        form_field_cls=forms.DateField,
        widget_factory=_widget_date,
        parse_default=_parse_date,
        validate_default=_validate_date,
        coerce_import=_coerce_import_date,
        parse_literal=_parse_literal_date,
        format_display=_format_identity,
    ),
    TIME: FieldTypeSpec(
        code=TIME,
        form_field_cls=forms.TimeField,
        widget_factory=_widget_time,
        parse_default=_parse_time,
        validate_default=_validate_time,
        coerce_import=_coerce_import_time,
        parse_literal=_parse_literal_time,
        format_display=_format_identity,
    ),
    BOOLEAN: FieldTypeSpec(
        code=BOOLEAN,
        form_field_cls=forms.BooleanField,
        widget_factory=_widget_boolean,
        parse_default=_parse_boolean,
        validate_default=_validate_boolean,
        coerce_import=_coerce_import_boolean,
        parse_literal=_parse_literal_boolean,
        format_display=_format_boolean,
    ),
    SELECT: FieldTypeSpec(
        code=SELECT,
        form_field_cls=forms.ChoiceField,
        widget_factory=_widget_select,
        parse_default=_parse_text,
        validate_default=_validate_select,
        coerce_import=_coerce_import_select,
        parse_literal=_parse_literal_option,
        format_display=_format_select,
    ),
    MULTI_SELECT: FieldTypeSpec(
        code=MULTI_SELECT,
        form_field_cls=forms.MultipleChoiceField,
        widget_factory=_widget_multi_select,
        parse_default=_parse_multi_select,
        validate_default=_validate_multi_select,
        coerce_import=_coerce_import_multi_select,
        parse_literal=_parse_literal_option,
        format_display=_format_multi_select,
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
        coerce_import=_coerce_import_raw,
        parse_literal=_parse_literal_raw,
        format_display=_format_identity,
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
    "validate_number_value",
]
