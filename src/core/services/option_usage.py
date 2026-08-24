"""Verwendete Options-Slugs einer Feldvorlage ermitteln (Refs #1875)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from core.models import Case, Client, Event, FieldCondition, FieldTemplate
from core.services.file_vault.encryption import safe_decrypt


def _collect_option_values(used: set[str], value: Any) -> None:
    """Einzel-/Mehrfachauswahl aus einem dynamischen JSON-Wert aufnehmen."""
    value = safe_decrypt(value, default=None)
    if isinstance(value, str):
        used.add(value)
    elif isinstance(value, list):
        used.update(item for item in value if isinstance(item, str))


def _dynamic_values(model, template: FieldTemplate) -> Iterable[Any]:
    rows = model.objects.filter(
        facility=template.facility,
        data_json__has_key=template.slug,
    ).values_list("data_json", flat=True)
    for data_json in rows.iterator():
        if isinstance(data_json, dict):
            yield data_json.get(template.slug)


def option_slugs_in_use(template: FieldTemplate) -> set[str]:
    """Slugs aus Bestandsdaten und Feldbedingungen, ohne Werte/Zaehler.

    ``User``-Eingaben liegen je nach Entitaet in ``Event``, ``Client`` oder
    ``Case``. Verschluesselte Auswahlfelder werden vor dem Vergleich ueber
    denselben fail-closed Helfer entschluesselt wie die Fachansichten.
    Bedingungen referenzieren den Options-Slug als typisierten JSON-Wert.
    Die Rueckgabe enthaelt bewusst nur die Slugs: Der Editor braucht fuer die
    Warnung weder Klartextdaten noch Bestandszaehler.
    """
    used: set[str] = set()
    for model in (Event, Client, Case):
        for value in _dynamic_values(model, template):
            _collect_option_values(used, value)

    condition_values = FieldCondition.objects.filter(
        depends_on_field__field_template=template,
    ).values_list("value", flat=True)
    for value in condition_values.iterator():
        _collect_option_values(used, value)
    return used
