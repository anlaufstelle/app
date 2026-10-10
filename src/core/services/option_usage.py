"""Collect and protect referenced option slugs within a facility."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.models import Case, Client, Event, EventHistory, FieldCondition, FieldTemplate, QuickTemplate
from core.services.file_vault.encryption import safe_decrypt


def _collect_option_values(used: set[str], value: Any) -> None:
    """Collect individual slugs from a plain or encrypted selection value."""
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


def option_slugs_in_use(template: FieldTemplate, *, include_default: bool = True) -> set[str]:
    """Collect stored values, history, conditions, all quick templates and defaults.

    Queries stay within the template's facility, including soft-deleted rows.
    Only slugs are returned, never labels, contents or counts. Undecryptable
    values cannot be counted; this is not a database or concurrency guarantee.
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
    quick_values = (
        QuickTemplate.objects.for_facility(template.facility)
        .filter(prefilled_data__has_key=template.slug)
        .values_list("prefilled_data", flat=True)
    )
    for payload in quick_values.iterator():
        if isinstance(payload, dict):
            _collect_option_values(used, payload.get(template.slug))
    if include_default:
        used.update(_valid_default_slugs(template))
    return used


def option_slugs(options: Any) -> set[str]:
    """Read identifiers defensively from an options list."""
    if not isinstance(options, list):
        return set()
    return {option["slug"] for option in options if isinstance(option, dict) and isinstance(option.get("slug"), str)}


def _default_slugs(template: FieldTemplate, raw: str) -> set[str]:
    if template.field_type == FieldTemplate.FieldType.SELECT:
        return {raw.strip()} if raw.strip() else set()
    if template.field_type == FieldTemplate.FieldType.MULTI_SELECT:
        return {value.strip() for value in raw.split(",") if value.strip()}
    return set()


def _valid_default_slugs(template: FieldTemplate) -> set[str]:
    try:
        template._validate_default_value()
    except ValidationError:
        return set()
    return _default_slugs(template, template.default_value)


def validate_option_changes(template: FieldTemplate, desired_options: Any, *, default_value: str | None = None) -> None:
    """Reject disappearing referenced slugs, scanning only when a slug disappears.

    ``template`` holds the persisted options and default. A default explicitly
    cleared or replaced in the same write no longer retains its old reference.
    The checks cover supported ORM paths, not raw SQL or concurrent writes.
    """
    missing = option_slugs(template.options_json) - option_slugs(desired_options)
    if not missing:
        return
    used = option_slugs_in_use(template, include_default=False)
    pending_default = template.default_value if default_value is None else default_value
    used.update(_valid_default_slugs(template) & _default_slugs(template, pending_default))
    blocked = sorted(missing & used)
    if blocked:
        raise ValidationError(
            {
                "options_json": _(
                    "Verwendete Options-Slugs dürfen nicht entfernt oder umbenannt werden: %(slugs)s. "
                    "Bitte die Option deaktivieren statt entfernen oder umbenennen."
                )
                % {"slugs": ", ".join(blocked)}
            }
        )
