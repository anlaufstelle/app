"""Sichere Abbildung service-seitiger Feldfehler auf dynamische Formulare (Refs #1936, #1938).

Die Services validieren ``data_json`` ein zweites Mal, nachdem das dynamische
Formular bereits gültig war: :func:`~core.services.dynamic_data.validate_data_json`
prüft die Wertebereiche (#1914) auch an Slugs, die das Formular gar nicht
trägt — etwa an einem für die Rolle unsichtbaren Feld, dessen Altwert der
E11-Merge wieder einsetzt, oder wenn eine Grenze zwischen Formular- und
Service-Validierung verschärft wurde.

Die daraus entstehende ``ValidationError`` ist dict-förmig
(``{slug: [...]}``) und darf nicht ungefiltert an den Aufrufer zurück:

* **Sichtbare Felder** behalten Slug, Meldung und Code — nur so kann die
  Fachkraft ihre eigene Eingabe korrigieren, und nur so markiert der
  Offline-Editor das richtige Eingabefeld.
* **Restricted und unbekannte Slugs** werden vollständig durch
  :data:`INVALID_DYNAMIC_INPUT_MESSAGE` ersetzt. Weder der Feldname noch die
  fachliche Meldung noch der Fehlercode dürfen ein Feld, einen Wert oder eine
  interne Regel offenlegen, deren Existenz die Rolle nicht kennen darf: „Wert
  muss mindestens 5 sein" verrät an einem unsichtbaren Feld dessen Grenze und
  per Umkehrschluss, dass der verborgene Bestandswert darunter liegt.

Der Helfer ist bewusst view-übergreifend (Ereignis, Person, Fall): dieselbe
Fehlerquelle, dieselbe Datenschutzentscheidung. Die zurückgegebene
Meldungsliste ist auch für den klassischen HTML-/HTMX-Flash-Pfad sicher.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

#: Ersatzmeldung für jeden Fehler, der nicht an einem sichtbaren Feld hängt.
INVALID_DYNAMIC_INPUT_MESSAGE = _("Ungültige Eingabe.")


def add_dynamic_service_errors(form, error, restricted_keys=()) -> list[str]:
    """Fehler auf ``form`` abbilden und die sicheren Meldungen zurückgeben.

    :param form: das dynamische Formular, dessen ``fields`` die Sichtbarkeit
        entscheiden. ``None`` ist zulässig (z. B. bei gesperrtem Typ, wo gar
        kein dynamisches Formular gebaut wurde) — dann gilt jeder dict-Key als
        unsichtbar und wird ersetzt.
    :param error: die ``ValidationError`` des Services.
    :param restricted_keys: Slugs, die ``remove_restricted_fields`` für diese
        Rolle aus dem Formular entfernt hat. Sie stehen zwar nicht mehr in
        ``form.fields``, werden aber ausdrücklich mitgeführt, damit ein
        wiederhergestelltes Formular die Prüfung nicht aushebelt.
    """
    if not hasattr(error, "error_dict"):
        # Nicht-feldbezogene Fehler (``ValidationError("...")``) tragen keine
        # Slug-Information und bleiben unverändert.
        if form is not None:
            form.add_error(None, error)
        return [str(message) for message in error.messages]

    restricted = set(restricted_keys or ())
    form_fields = getattr(form, "fields", {}) if form is not None else {}
    safe_messages: list[str] = []
    for field_name, field_errors in error.error_dict.items():
        if field_name in form_fields and field_name not in restricted:
            form.add_error(field_name, field_errors)
            safe_messages.extend(str(message) for field_error in field_errors for message in field_error.messages)
            continue

        if form is not None:
            form.add_error(None, ValidationError(INVALID_DYNAMIC_INPUT_MESSAGE, code="invalid"))
        safe_messages.append(str(INVALID_DYNAMIC_INPUT_MESSAGE))
    return safe_messages
