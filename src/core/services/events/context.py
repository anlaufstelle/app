"""Read-only Context-Builder fuer Event-Detail-Views (Refs #777).

Aufgeteilt aus ``services/event.py``: alles, was rein lesend einen
Template-Context oder einen filtered-Diff aufbaut. Keine DB-Writes.
"""

from __future__ import annotations

import uuid

from django.utils.translation import gettext_lazy as _

from core.services.encryption import safe_decrypt
from core.services.events.fields import (
    _is_file_marker,
    build_field_template_lookup,
    normalize_file_marker,
)
from core.services.file_vault import get_original_filename
from core.services.sensitivity import user_can_see_field
from core.utils.formatting import format_file_size


def filtered_server_data_json(user, event):
    """Return ``event.data_json`` with fields *user* cannot see removed.

    Keeps the conflict-diff honest: the user only ever sees values they could
    also read via the normal detail view, so the merge UI cannot become a
    side-channel for restricted content. Encrypted values stay as their
    marker dicts.
    """
    if not event.data_json:
        return {}
    doc_sensitivity = event.document_type.sensitivity
    field_templates = build_field_template_lookup(event.document_type)
    result = {}
    for slug, value in event.data_json.items():
        ft = field_templates.get(slug)
        field_sensitivity = ft.sensitivity if ft else ""
        if not user_can_see_field(user, doc_sensitivity, field_sensitivity):
            continue
        if isinstance(value, dict) and (value.get("__file__") or value.get("__files__")):
            if value.get("__files__"):
                result[slug] = {
                    "__files__": True,
                    "entries": [{"id": e.get("id"), "sort": e.get("sort", 0)} for e in (value.get("entries") or [])],
                }
            else:
                result[slug] = {"__file__": True, "name": value.get("name", "")}
            continue
        result[slug] = safe_decrypt(value, default="") if isinstance(value, dict) else value
    return result


def _format_field_display_value(value, ft):
    """Format a ``data_json`` value for human-readable detail display.

    Wandelt SELECT/MULTI_SELECT-Slugs in Labels (``['beratung']`` ->
    ``Beratung``) und BOOLEAN-Werte in ``Ja``/``Nein`` um. Refs #749.
    """
    if ft is None:
        return value

    if ft.field_type == ft.FieldType.BOOLEAN:
        return _("Ja") if value else _("Nein")

    if ft.field_type == ft.FieldType.SELECT and ft.options_json:
        label_map = {o["slug"]: o["label"] for o in ft.options_json if isinstance(o, dict) and "slug" in o}
        return label_map.get(value, value)

    if ft.field_type == ft.FieldType.MULTI_SELECT and ft.options_json and isinstance(value, list):
        label_map = {o["slug"]: o["label"] for o in ft.options_json if isinstance(o, dict) and "slug" in o}
        return ", ".join(label_map.get(v, str(v)) for v in value)

    return value


def _build_prior_versions(attachment, predecessor_index):
    """Walk the ``superseded_by`` chain backwards for an attachment.

    ``predecessor_index`` ist ``{successor_pk: predecessor}`` fuer alle
    Attachments des Events, vorab in einem einzigen Query erstellt
    (#662 FND-05).
    """
    prior_versions = []
    current_attachment = attachment
    seen = {current_attachment.pk}
    predecessor = predecessor_index.get(current_attachment.pk)
    while predecessor is not None and predecessor.pk not in seen:
        seen.add(predecessor.pk)
        prior_versions.append(
            {
                "attachment_id": str(predecessor.pk),
                "original_filename": get_original_filename(predecessor),
                "file_size_display": format_file_size(predecessor.file_size),
                "superseded_at": predecessor.superseded_at,
            }
        )
        current_attachment = predecessor
        predecessor = predecessor_index.get(current_attachment.pk)
    return prior_versions


def build_event_detail_context(event, user):
    """Build the template context dict for :class:`EventDetailView`.

    Returns a dict with ``event``, ``fields_display`` (list of per-field dicts
    with decrypted values / file markers / sensitivity flags) and ``history``
    (reverse-chronological :class:`EventHistory` queryset).
    """
    field_templates = build_field_template_lookup(event.document_type, ordered=True)
    doc_sensitivity = event.document_type.sensitivity

    all_attachments = list(event.attachments.all())
    attachments_by_pk = {att.pk: att for att in all_attachments}
    predecessor_index = {att.superseded_by_id: att for att in all_attachments if att.superseded_by_id is not None}

    fields_display = []
    for key, value in (event.data_json or {}).items():
        ft = field_templates.get(key)
        field_sensitivity = ft.sensitivity if ft else ""
        is_encrypted = ft.is_encrypted if ft else False

        if not user_can_see_field(user, doc_sensitivity, field_sensitivity):
            fields_display.append(
                {
                    "label": ft.name if ft else key.replace("-", " ").title(),
                    "value": _("[Eingeschränkt]"),
                    "is_sensitive": bool(field_sensitivity),
                    "restricted": True,
                }
            )
            continue

        if _is_file_marker(value):
            entries_meta = normalize_file_marker(value)
            file_entries = []
            for entry in entries_meta:
                try:
                    entry_pk = uuid.UUID(entry["id"]) if not isinstance(entry["id"], uuid.UUID) else entry["id"]
                except (ValueError, TypeError):
                    continue
                att = attachments_by_pk.get(entry_pk)
                if not att:
                    continue
                if att.deleted_at is not None:
                    continue
                prior = _build_prior_versions(att, predecessor_index)
                file_entries.append(
                    {
                        "attachment_id": str(att.pk),
                        "original_filename": get_original_filename(att),
                        "file_size_display": format_file_size(att.file_size),
                        "prior_versions": prior,
                    }
                )
            if file_entries:
                first = file_entries[0]
                fields_display.append(
                    {
                        "label": ft.name if ft else key,
                        "is_file": True,
                        "attachment_id": first["attachment_id"],
                        "original_filename": first["original_filename"],
                        "file_size_display": first["file_size_display"],
                        "prior_versions": first["prior_versions"],
                        "entries": file_entries,
                        "is_sensitive": bool(field_sensitivity),
                    }
                )
                continue

        decrypted = safe_decrypt(value, default=_("[verschlüsselt]"))
        fields_display.append(
            {
                "label": ft.name if ft else key.replace("-", " ").title(),
                "value": _format_field_display_value(decrypted, ft),
                "is_encrypted": is_encrypted,
                "is_sensitive": bool(field_sensitivity),
            }
        )

    history = event.history.select_related("changed_by").order_by("-changed_at")

    return {
        "event": event,
        "fields_display": fields_display,
        "history": history,
    }
