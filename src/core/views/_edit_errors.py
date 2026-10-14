"""Shared presentation of failed HTML edit submissions."""

from contextlib import suppress

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.services.dynamic_data import remove_restricted_fields
from core.views._dynamic_errors import add_dynamic_service_errors


def is_version_conflict(error):
    """Classify the explicit optimistic locking error codes."""
    return getattr(error, "code", None) in {"version_conflict", "invalid_token", "missing_token"}


def refresh_edit_visibility(user, data_form):
    """Recheck field policy after a service rejection, before reflecting POST."""
    if data_form is None or data_form.document_type is None:
        return []
    data_form.document_type.refresh_from_db(fields=["sensitivity"])
    restricted = remove_restricted_fields(user, data_form.document_type, data_form)
    # Validation may have cached conditions before the policy changed (Refs #2539).
    data_form.__dict__.pop("_conditions_payload_cache", None)
    return restricted


def _display_value(bound_field):
    """Render permitted values as text, without editable widgets or hidden data."""
    value = bound_field.value()
    if isinstance(bound_field.field, forms.FileField):
        attachments = getattr(bound_field.form, "_edit_attachments", {}).get(bound_field.name, ())
        if attachments:
            return "; ".join(f"{entry['filename']} ({entry['size']})" for entry in attachments)
        existing = getattr(bound_field.form, "_existing_file_slugs", ())
        return str(_("Datei vorhanden")) if value or bound_field.name in existing else "—"
    values = value if isinstance(value, (list, tuple)) else [value]
    choices = dict(bound_field.field.choices) if hasattr(bound_field.field, "choices") else {}
    labels = {str(key): str(label) for key, label in choices.items()}
    return ", ".join(labels.get(str(item), str(item)) for item in values if item not in (None, "")) or "—"


def _comparison_value(bound_field):
    """Compare field values rather than potentially ambiguous display labels."""
    if bound_field is None:
        return None
    if isinstance(bound_field.field, forms.FileField):
        form = bound_field.form
        uploads = [
            (key, [(upload.name, upload.size) for upload in form.files.getlist(key)])
            for key in form.files
            if key == bound_field.name or key.startswith(f"{bound_field.name}__replace__")
        ]
        return (
            getattr(form, "_edit_file_state", {}).get(bound_field.name),
            uploads,
            form.data.get(f"{bound_field.name}__remove", ""),
        )
    value = bound_field.value()
    with suppress(ValidationError):
        value = bound_field.field.prepare_value(bound_field.field.to_python(value))
    values = value if isinstance(value, (list, tuple)) else [value]
    return [str(item) if item is not None else "" for item in values]


def file_reselection_context(data_form):
    """Name the file fields whose uploads a failed submit could not keep.

    Browsers never refill file inputs, so a re-rendered form silently drops any
    chosen file. Used by the service-error path and by the plain form-invalid
    re-render of the edit views alike (Refs #2539).
    """
    files = getattr(data_form, "files", None)
    if files is None:
        return {"file_reselection_fields": []}
    return {
        "file_reselection_fields": [
            field.label
            for name, field in getattr(data_form, "fields", {}).items()
            if isinstance(field, forms.FileField)
            and any(files.getlist(key) for key in files if key == name or key.startswith(f"{name}__replace__"))
        ]
    }


def edit_error_context(
    error,
    data_form,
    restricted_keys=(),
    *,
    current_forms=(),
    current_version=None,
    current_form_state=None,
):
    """Keep bound forms; refresh guards only alongside an authorized comparison.

    Callers reload and authorize the owner and filter both forms before passing
    them here. This helper never queries owner data or copies raw POST fields.
    """
    display_error = error
    if is_version_conflict(error):
        display_error = ValidationError(
            _(
                "Der Datensatz wurde zwischenzeitlich bearbeitet oder der Versionsnachweis ist ungültig. "
                "Es wurde nichts gespeichert."
            ),
            code=error.code,
        )
    context = {
        "edit_notices": add_dynamic_service_errors(data_form, display_error, restricted_keys),
        **file_reselection_context(data_form),
        "has_edit_conflict": False,
    }
    if not is_version_conflict(error) or current_version is None or not current_forms:
        return context

    rows = []
    for own, current in current_forms:
        # Both field sets have already passed the role's visibility filter.
        for name in dict.fromkeys([*current.fields, *getattr(own, "fields", {})]):
            server_field = current[name] if name in current.fields else None
            own_field = own[name] if own is not None and name in own.fields else None
            field = server_field or own_field
            if field.is_hidden:
                continue
            server_value = _display_value(server_field) if server_field is not None else "—"
            rows.append(
                {
                    "label": field.label,
                    "value": server_value,
                    "differs": _comparison_value(server_field) != _comparison_value(own_field),
                }
            )
    context.update(
        has_edit_conflict=True,
        edit_conflict_rows=rows,
        expected_updated_at=current_version,
        form_state_token=current_form_state,
    )
    return context
