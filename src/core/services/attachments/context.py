"""Read-only Context-Builder der Anhang-Domaene (M24/AP2, Refs #1738).

Umgezogen aus :mod:`core.services.events.context`. Alles hier ist rein lesend
und owner-generisch: ein Anhang haengt seit ADR-039 an einem Ereignis, einer
Person oder einem Fall, und die Anzeige-Dicts sind fuer alle drei identisch.

``events/context.py`` behaelt delegierende Aliasse (``build_attachment_context``,
``_resolve_file_entries``, ``_build_prior_versions``), damit Bestands-Importe
ueber ``core.services.events`` unveraendert gueltig bleiben. Die
Import-Richtung ist bewusst einseitig — dieses Modul importiert NICHTS aus
``services/events`` (sonst entstuende ein Zyklus).
"""

from __future__ import annotations

import uuid

from django.urls import reverse

from core.services.dynamic_data import normalize_file_marker
from core.services.file_vault import get_original_filename
from core.utils.formatting import format_file_size

#: Download-Route je Owner-Typ (ADR-039 §12). Die Kwarg-Signatur ist fuer alle
#: drei identisch zur Bestands-Event-Route: ``pk`` = Owner-PK, ``attachment_pk``
#: = Anhang-PK.
#:
#: **Stand AP2:** nur ``core:attachment_download`` existiert. Die Client-/Fall-
#: Routen legt AP4 an; bis dahin hebt :func:`attachment_download_url` fuer
#: personen-/falleigene Anhaenge ``NoReverseMatch`` — bewusst fail-loud statt
#: eines stillen ``None``, das im Template als toter Link landen wuerde.
OWNER_DOWNLOAD_ROUTES = {
    "event": "core:attachment_download",
    "client": "core:client_attachment_download",
    "case": "core:case_attachment_download",
}


def attachment_download_url(attachment):
    """Download-URL eines Anhangs — aufgeloest ueber seinen Owner-Typ.

    Loest das bisher fuenffach im Template hartkodierte
    ``{% url 'core:attachment_download' event.pk … %}`` ab: der Kontext traegt
    die URL ab AP2 selbst, damit AP4 die Templates auf einen Ein-Zeilen-Diff
    (``entry.download_url``) umstellen kann.

    :raises django.urls.NoReverseMatch: fuer personen-/falleigene Anhaenge,
        solange AP4 die beiden Routen noch nicht angelegt hat (erwartet).
    :raises ValueError: wenn der Anhang keinen Owner hat (DB-seitig durch
        ``attachment_exactly_one_owner`` ausgeschlossen; nur bei einer noch
        nicht gespeicherten Instanz moeglich).
    """
    owner_type = attachment.owner_type
    route = OWNER_DOWNLOAD_ROUTES.get(owner_type)
    if route is None:
        raise ValueError(f"Anhang ohne aufloesbaren Owner-Typ: {attachment.pk!r} (owner_type={owner_type!r})")
    return reverse(route, kwargs={"pk": attachment.owner_id, "attachment_pk": attachment.pk})


def build_prior_versions(attachment, predecessor_index):
    """Walk the ``superseded_by`` chain backwards for an attachment.

    ``predecessor_index`` ist ``{successor_pk: predecessor}`` fuer alle
    Attachments des Owners, vorab in einem einzigen Query erstellt
    (#662). Jede Vorversion traegt ihre eigene ``download_url`` (Refs #1738) —
    die Detail-Anzeige verlinkt auch aeltere Fassungen.
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
                "download_url": attachment_download_url(predecessor),
            }
        )
        current_attachment = predecessor
        predecessor = predecessor_index.get(current_attachment.pk)
    return prior_versions


def resolve_file_entries(value, attachments_by_pk, predecessor_index):
    """Refs #1160: Marker-Entries eines FILE-Felds zu Display-Dicts aufloesen.

    Akzeptiert beide Marker-Formate (via
    :func:`~core.services.dynamic_data.normalize_file_marker`), ueberspringt
    nicht aufloesbare/geloeschte (``deleted_at``) Anhaenge und haengt pro aktivem
    Anhang die ``prior_versions``-Kette sowie die ``download_url`` an.

    M24/AP2 (Refs #1738): aus dem privaten ``_resolve_file_entries`` in
    ``events/context.py`` gehoben; der Alt-Name bleibt dort als Alias.
    """
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
        prior = build_prior_versions(att, predecessor_index)
        file_entries.append(
            {
                "attachment_id": str(att.pk),
                "original_filename": get_original_filename(att),
                "file_size_display": format_file_size(att.file_size),
                "prior_versions": prior,
                "download_url": attachment_download_url(att),
            }
        )
    return file_entries


def build_attachment_context(owner):
    """Refs #804 (C-37): pro File-Slug eine Liste aktiver Anhaenge bauen.

    Liefert ein Dict ``{slug: [{entry_id, attachment_id, filename, size,
    sort_order, download_url}, ...]}``, wobei legacy ``__file__``-Marker via
    :func:`~core.services.dynamic_data.normalize_file_marker` zu Single-Entry-
    Listen aufgeloest werden. Geloeschte Anhaenge (``deleted_at``) werden
    gefiltert.

    Refs #894: Anhaenge einmalig laden und per PK-Dict aufloesen — bei N
    File-Markern entstand vorher pro Marker eine separate Query.

    M24/AP2 (Refs #1738): ``owner`` ist ein ``Event``, ``Client`` oder ``Case``
    (Umzug aus ``events/context.py``, Alias bleibt dort); jeder Eintrag traegt
    zusaetzlich seine ``download_url``.
    """
    if not owner.data_json:
        return {}

    attachments_by_pk = {att.pk: att for att in owner.attachments.all()}
    existing_attachments_by_slug: dict[str, list[dict]] = {}
    for slug, value in owner.data_json.items():
        entries_meta = normalize_file_marker(value)
        if not entries_meta:
            continue
        entries = []
        for entry in entries_meta:
            try:
                entry_pk = uuid.UUID(entry["id"]) if not isinstance(entry["id"], uuid.UUID) else entry["id"]
            except (ValueError, TypeError):
                continue
            att = attachments_by_pk.get(entry_pk)
            if not att or att.deleted_at is not None:
                continue
            entries.append(
                {
                    "entry_id": str(att.entry_id),
                    "attachment_id": str(att.pk),
                    "filename": get_original_filename(att),
                    "size": format_file_size(att.file_size),
                    "sort_order": att.sort_order,
                    "download_url": attachment_download_url(att),
                }
            )
        if entries:
            existing_attachments_by_slug[slug] = entries
    return existing_attachments_by_slug
