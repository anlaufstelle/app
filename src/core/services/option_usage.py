"""Verwendete Options-Slugs einer Feldvorlage ermitteln (Refs #1875)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.db.models import Q

from core.models import Case, Client, Event, EventHistory, FieldCondition, FieldTemplate
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
    """Collect option slugs from current values, event history and conditions.

    Decrypt selections with the same fail-closed helper as the detail views.
    Only slugs are returned; callers receive no labels, contents or counts.
    All entity and history queries remain scoped to the template's facility.
    """
    used: set[str] = set()
    for model in (Event, Client, Case):
        for value in _dynamic_values(model, template):
            _collect_option_values(used, value)

    # Historical selections still reference options after the current value changes.
    history_rows = (
        EventHistory.objects.filter(event__facility=template.facility)
        .filter(Q(data_before__has_key=template.slug) | Q(data_after__has_key=template.slug))
        .values_list("data_before", "data_after")
    )
    for before, after in history_rows.iterator():
        for payload in (before, after):
            if isinstance(payload, dict):
                _collect_option_values(used, payload.get(template.slug))

    condition_values = FieldCondition.objects.filter(
        depends_on_field__field_template=template,
    ).values_list("value", flat=True)
    for value in condition_values.iterator():
        _collect_option_values(used, value)
    return used
