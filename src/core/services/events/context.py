"""Read-only Context-Builder fuer Event-Detail-Views (Refs #777).

Aufgeteilt aus ``services/event.py``: alles, was rein lesend einen
Template-Context oder einen filtered-Diff aufbaut. Keine DB-Writes.

M24/AP2 (Refs #1738, ADR-039): Die Anhang-Bausteine (``build_attachment_context``,
``_resolve_file_entries``, ``_build_prior_versions``) sind nach
:mod:`core.services.attachments.context` umgezogen — owner-generisch fuer
Ereignis, Person und Fall. Hier bleiben delegierende Aliasse, damit
``services/events/__init__.py`` und Bestands-Tests ihre Importe behalten.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from core.models import DocumentType, FieldTemplate
from core.services.attachments import context as attachments_context
from core.services.attachments.context import build_prior_versions, resolve_file_entries
from core.services.compliance import user_can_see_field

# M2 (Refs #1058, P7): die Wert-Formatierung ist entity-neutral und lebt jetzt in
# ``services/dynamic_data``. Der private Alt-Name bleibt hier als Alias bestehen —
# ``services/events/__init__.py`` und die Mutation-Followup-Tests importieren ihn
# unveraendert von hier (Muster P3: Umzug + Alias-Re-Export).
from core.services.dynamic_data import format_dynamic_display_value as _format_field_display_value
from core.services.dynamic_data import option_label_map, orphan_gating_sensitivity, resolve_orphan_field_templates
from core.services.events.fields import (
    _is_file_marker,
    build_field_template_lookup,
)
from core.services.file_vault import safe_decrypt


def filtered_server_data_json(user, event):
    """Return ``event.data_json`` with fields *user* cannot see removed.

    Keeps the conflict-diff honest: the user only ever sees values they could
    also read via the normal detail view, so the merge UI cannot become a
    side-channel for restricted content. Visible encrypted values are
    decrypted after the gate, preserving selection lists for replay.
    """
    if not event.data_json:
        return {}
    doc_sensitivity = event.document_type.sensitivity
    field_templates = build_field_template_lookup(event.document_type)
    # SICHERHEITSBEFUND (Refs #1725): verwaiste Slugs (Wert in ``data_json``, aber
    # nicht mehr am Typ) facility-weit auflösen und bei fehlendem Template
    # konservativ HIGH gaten — sonst fiele ihre Sensitivity auf ``""`` (=NORMAL)
    # und ein einst als elevated/high eingestufter Wert leckte entschlüsselt in
    # den Konflikt-Diff (server_state) an unterberechtigte Rollen. Gleiche
    # Auflösung wie ``build_event_detail_context`` und das Offline-Bundle.
    orphan_slugs = [slug for slug in event.data_json if slug not in field_templates]
    orphan_templates = resolve_orphan_field_templates(event.document_type, orphan_slugs)
    result = {}
    for slug, value in event.data_json.items():
        ft = field_templates.get(slug)
        field_sensitivity = (
            (ft.sensitivity or "") if ft is not None else orphan_gating_sensitivity(orphan_templates.get(slug))
        )
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


def build_event_conflict_state(user, event):
    """Keep replay values separate from role-filtered selection labels."""
    data = filtered_server_data_json(user, event)
    templates = build_field_template_lookup(event.document_type)
    templates.update(resolve_orphan_field_templates(event.document_type, set(data).difference(templates)))
    field_options = {}
    for slug, template in templates.items():
        if template.field_type != FieldTemplate.FieldType.MULTI_SELECT:
            continue
        if not user_can_see_field(user, event.document_type.sensitivity, template.sensitivity or ""):
            continue
        # Include inactive labels for existing selections, without making
        # them available as new choices in the offline edit form (Refs #2520).
        field_options[slug] = [
            {"value": value, "label": label} for value, label in option_label_map(template.options_json).items()
        ]
    return {"data_json": data, "field_options": field_options}


#: M24/AP2 (Refs #1738): Umzug nach :mod:`core.services.attachments.context`
#: (owner-generisch). Die Alt-Namen bleiben hier als Aliasse — ``services/
#: events/__init__.py`` und Bestands-Tests importieren sie unveraendert von
#: hier (Muster P3: Umzug + Alias-Re-Export).
_build_prior_versions = build_prior_versions
_resolve_file_entries = resolve_file_entries


def build_attachment_context(event):
    """Alt-Einstiegspunkt fuer den Event-Pfad — delegiert owner-generisch.

    M24/AP2 (Refs #1738): Implementierung in
    :func:`core.services.attachments.context.build_attachment_context`.
    """
    return attachments_context.build_attachment_context(event)


def resolve_default_document_type(facility):
    """Refs #804 (C-37): Default-DocumentType einer Facility.

    Liefert ``(default_doc_type, initial)`` — ``default_doc_type`` ist die
    aktive Standard-Dokumentart der Facility-Settings (oder ``None``, wenn
    keine gesetzt ist, sie zwischenzeitlich deaktiviert/verschoben wurde,
    oder — M2, Refs #1058, Entscheid E21 — ihr ``entity_type`` nicht
    ``event`` ist). ``initial`` ist ein Dict, das in das ``EventMetaForm``
    geht (``{"document_type": <pk>}`` oder leer).
    """
    initial: dict = {}
    try:
        settings = facility.settings
    except facility._meta.get_field("settings").related_model.DoesNotExist:
        return None, initial
    if not settings.default_document_type_id:
        return None, initial
    default_doc_type = settings.default_document_type
    if not default_doc_type.is_active or default_doc_type.facility != facility:
        return None, initial
    # M2 (Refs #1058, Entscheid E21): ein auf einen client-/case-Typ zeigender
    # Default macht die Facility-Konfiguration wirkungslos statt ihn in den
    # Event-Pfad durchzureichen — Fallback-Verhalten wie ohne Default.
    if default_doc_type.entity_type != DocumentType.EntityType.EVENT:
        return None, initial
    initial["document_type"] = default_doc_type.pk
    return default_doc_type, initial


def _build_field_display_entry(
    key, value, ft, user, doc_sensitivity, attachments_by_pk, predecessor_index, *, orphaned=False
):
    """Refs #1160: Ein einzelnes ``fields_display``-Dict bauen.

    Kapselt die drei Faelle aus :func:`build_event_detail_context`:
    restringiert (``[Eingeschränkt]``), FILE-Marker mit aktiven Anhaengen,
    und der entschluesselte Default. Wirkungs-identisch — gibt genau das
    Dict zurueck, das vorher inline an ``fields_display`` angehaengt wurde,
    ergaenzt um das (fuer regulaere Felder ``False``) ``orphaned``-Flag.

    ``orphaned=True`` markiert ein verwaistes Feld (Slug in ``data_json``, aber
    nicht mehr am aktuellen Typ, Refs #1725). Der Aufrufer loest sein
    ``FieldTemplate`` facility-weit auf und reicht es als ``ft`` durch — so
    behaelt der Wert seine echte Sensitivity/Verschluesselung. Findet der
    Aufrufer ausnahmsweise kein Template (``ft=None`` trotz ``orphaned=True`` —
    der Loeschschutz sollte das verhindern, solange Daten unter dem Slug
    liegen), wird das Feld **konservativ als HIGH** behandelt: nur Leitung/Admin
    sehen den Wert, statt ihn versehentlich offenzulegen.
    """
    if ft is not None:
        field_sensitivity = ft.sensitivity or ""
    elif orphaned:
        field_sensitivity = DocumentType.Sensitivity.HIGH
    else:
        field_sensitivity = ""
    is_encrypted = ft.is_encrypted if ft else False

    if not user_can_see_field(user, doc_sensitivity, field_sensitivity):
        return {
            "label": ft.name if ft else key.replace("-", " ").title(),
            "value": _("[Eingeschränkt]"),
            "is_sensitive": bool(field_sensitivity),
            "restricted": True,
            "orphaned": orphaned,
        }

    if _is_file_marker(value):
        file_entries = resolve_file_entries(value, attachments_by_pk, predecessor_index)
        if file_entries:
            first = file_entries[0]
            return {
                "label": ft.name if ft else key,
                "is_file": True,
                "attachment_id": first["attachment_id"],
                "original_filename": first["original_filename"],
                "file_size_display": first["file_size_display"],
                "prior_versions": first["prior_versions"],
                "entries": file_entries,
                "is_sensitive": bool(field_sensitivity),
                "orphaned": orphaned,
            }

    decrypted = safe_decrypt(value, default=_("[verschlüsselt]"))
    return {
        "label": ft.name if ft else key.replace("-", " ").title(),
        "value": _format_field_display_value(decrypted, ft),
        "is_encrypted": is_encrypted,
        "is_sensitive": bool(field_sensitivity),
        "orphaned": orphaned,
    }


def _build_history_with_slug_info(event):
    """Refs #1160/#824 (C-57): History DESC laden + geteilten Slug-Info-Lookup anhaengen.

    Alle History-Entries gehoeren zum selben Event und damit zum selben
    DocumentType. Wir bauen den Slug-Info-Lookup einmal vor und haengen ihn an
    jeden Entry — das Template-Tag ``compute_diff`` nutzt ``entry._slug_info``
    per Vorrang vor seinem eigenen ``_build_slug_info``-Fallback und spart pro
    Entry einen Field-Query.
    """
    shared_slug_info = {}
    for dtf in event.document_type.fields.select_related("field_template"):
        ft = dtf.field_template
        shared_slug_info[ft.slug] = {
            "name": ft.name,
            "is_encrypted": ft.is_encrypted,
            "sensitivity": ft.sensitivity,
        }
    history = list(event.history.select_related("changed_by").order_by("-changed_at"))
    for entry in history:
        entry.event = event
        entry._slug_info = shared_slug_info
    return history


def _build_secondary_action_flags(event, user):
    """Sichtbarkeit und Position der Sekundäraktionen des Ereignis-Details.

    Refs #1864: Das ⋮ „Weitere Aktionen" soll nur erscheinen, wenn es
    wirklich eine Auswahl bündelt (>= 2 Einträge). Für ein Ereignis heißt das
    zählen — und die Bedingung im Template
    (``event.client or event.case and user.is_staff_or_above or ...``) war
    schon vor der Zählung nur über Operatorvorrang lesbar. Sie hier einmal
    auseinanderzunehmen ist billiger, als sie im Template ein zweites Mal zu
    formulieren: Template und Test lesen jetzt aus derselben Quelle.

    ``Löschen`` ist folgenschwer und bleibt IMMER im Menü (§6.2) — auch als
    Einzeleintrag. Sobald es sichtbar ist, gibt es also ein ⋮, egal wie viele
    Einträge daneben stehen.
    """
    show_to_client = event.client_id is not None
    # ``case_detail`` ist STAFF_PLUS — die Assistenz sähe sonst einen 403-Link.
    # Refs #1818: und ein Fall im Papierkorb antwortet für JEDE Rolle 404
    # (``cases_with_live_client()``). Der Fremdschlüssel bleibt bis zur Tilgung
    # bestehen (P5, ADR-044) — die Sichtgrenze muss deshalb hier gezogen werden,
    # sonst zeigt die Personenakte weiter den Freitext-Titel eines gelöschten
    # Falls und verlinkt ins Leere. Der Wert trägt beide Flächen: die ⋮-Aktion
    # „Zum Fall" UND das Kontext-Badge im Objektkopf (detail.html).
    show_to_case = event.case_id is not None and user.is_staff_or_above and not event.case.is_deleted
    can_delete_event = user.is_lead_or_admin or (user.is_staff_or_above and event.created_by_id == user.pk)
    bleibt_im_menue = can_delete_event or (show_to_client and show_to_case)
    return {
        "show_to_client": show_to_client,
        "show_to_case": show_to_case,
        "can_delete_event": can_delete_event,
        # "overflow" -> ⋮-Rahmen rendern; "primary" -> der eine Rest steht
        # direkt in der Leiste. Bleibt gar nichts sichtbar, rendert keine der
        # Aktionen und der Wert ist folgenlos.
        "secondary_action_position": "overflow" if bleibt_im_menue else "primary",
    }


def build_event_detail_context(event, user):
    """Build the template context dict for :class:`EventDetailView`.

    Returns a dict with ``event``, ``fields_display`` (list of per-field dicts
    with decrypted values / file markers / sensitivity flags), ``history``
    (reverse-chronological :class:`EventHistory` queryset) and the secondary
    action flags from :func:`_build_secondary_action_flags`.
    """
    field_templates = build_field_template_lookup(event.document_type, ordered=True)
    doc_sensitivity = event.document_type.sensitivity
    data = event.data_json or {}

    all_attachments = list(event.attachments.all())
    attachments_by_pk = {att.pk: att for att in all_attachments}
    predecessor_index = {att.superseded_by_id: att for att in all_attachments if att.superseded_by_id is not None}

    # Refs #1729: erst über die konfigurierte Blockreihenfolge iterieren (die
    # ``jsonb``-Schlüsselreihenfolge ist für Benutzer willkürlich — PostgreSQL
    # normalisiert sie). Nur vorhandene Slugs rendern; Muster
    # ``build_dynamic_fields_display``.
    fields_display = [
        _build_field_display_entry(
            key,
            data[key],
            ft,
            user,
            doc_sensitivity,
            attachments_by_pk,
            predecessor_index,
        )
        for key, ft in field_templates.items()
        if key in data
    ]
    # Danach die verwaisten Keys (in ``data_json``, aber nicht (mehr) am Typ) in
    # stabiler, sortierter Reihenfolge anhängen — sie dürfen NICHT verschwinden
    # (Refs #1729/#1725).
    orphan_slugs = [k for k in data if k not in field_templates]
    if orphan_slugs:
        # SICHERHEITSBEFUND (Refs #1725): Ohne facility-weite Auflösung fiele die
        # Sensitivity eines verwaisten Felds auf ``""`` (=NORMAL) — einst als
        # elevated/high eingestufte Werte würden allen sichtbar. ``FieldTemplate``
        # ist unique je (facility, slug) und bleibt durch den Löschschutz am
        # Leben, solange Daten unter dem Slug existieren (gemeinsamer Helfer, den
        # auch Konflikt-Diff und Offline-Bundle nutzen).
        orphan_templates = resolve_orphan_field_templates(event.document_type, orphan_slugs)
        for key in sorted(orphan_slugs):
            fields_display.append(
                _build_field_display_entry(
                    key,
                    data[key],
                    orphan_templates.get(key),
                    user,
                    doc_sensitivity,
                    attachments_by_pk,
                    predecessor_index,
                    orphaned=True,
                )
            )

    return {
        "event": event,
        "fields_display": fields_display,
        "history": _build_history_with_slug_info(event),
        **_build_secondary_action_flags(event, user),
    }
