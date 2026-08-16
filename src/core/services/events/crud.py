"""Event-CRUD-Pfade (Refs #777).

``create_event``, ``update_event``, ``soft_delete_event`` und die
Event-spezifische Update-Payload-Ergaenzung ``merge_update_payload``.

Aufgeteilt aus dem alten ``services/event.py`` (#777).

M24/AP2 (Refs #1738, ADR-039): Die Attachment-Schreiber
``finalize_staged_files`` (Refs #1345) und ``apply_attachment_changes`` sind
nach :mod:`core.services.attachments.crud` umgezogen — sie waren nie
event-spezifisch, seit ADR-039 haengen Anhaenge auch an Person und Fall. Hier
bleiben nur duenne, delegierende Aliasse mit dem alten ``event``-Parameter,
damit ``core.services.events``-Importe und die Event-Views unveraendert
gueltig bleiben.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from core.models import Activity, AuditLog, Client, DocumentType, Event, EventHistory, FieldTemplate
from core.services.attachments import crud as attachments_crud
from core.services.audit import audit_event
from core.services.compliance import user_can_see_document_type
from core.services.dashboard import log_activity
from core.services.events.fields import (
    _snapshot_field_metadata,
    _validate_data_json,
    build_field_template_lookup,
    build_redacted_delete_history,
    compute_event_search_text,
    stage_index,
)
from core.services.file_vault import delete_attachments_for


def finalize_staged_files(event, user, staged_by_slug, document_type):
    """Alt-Einstiegspunkt fuer den Event-Pfad — delegiert owner-generisch.

    M24/AP2 (Refs #1738): Die Implementierung ist nach
    :func:`core.services.attachments.crud.finalize_staged_files` umgezogen
    (owner-generisch: Ereignis, Person, Fall). Dieser duenne Alias haelt den
    Bestands-Importpfad (``core.services.events``) und die Event-Views
    unveraendert gueltig.
    """
    return attachments_crud.finalize_staged_files(event, user, staged_by_slug, document_type)


def apply_attachment_changes(event, user, request_post, request_files, file_fields, document_type):
    """Alt-Einstiegspunkt fuer den Event-Pfad — delegiert owner-generisch.

    M24/AP2 (Refs #1738): Implementierung in
    :func:`core.services.attachments.crud.apply_attachment_changes`.
    """
    return attachments_crud.apply_attachment_changes(
        event, user, request_post, request_files, file_fields, document_type
    )


def _validate_case_assignment(facility, case, client, is_anonymous):
    """Refs #1160: Fall-Zuordnung pruefen (Facility-Scope, Person-Match,
    Anonymitaets-Ausschluss). Wirkungs-identisch zum frueheren Inline-Block."""
    if case is None:
        return
    if case.facility_id != facility.pk:
        raise ValidationError(_("Fall gehört nicht zur selben Einrichtung wie das Ereignis."))
    if case.client_id is not None and client is not None and case.client_id != client.pk:
        raise ValidationError(_("Person des Ereignisses passt nicht zur Person des Falls."))
    if case.client_id is not None and (client is None or is_anonymous):
        raise ValidationError(_("Anonyme Ereignisse dürfen nicht an Fälle mit zugeordneter Person gehängt werden."))


def _validate_contact_stage(document_type, client, is_anonymous):
    """Refs #1160: Mindest-Kontaktstufen-Regeln des DocumentType pruefen.

    Wirkungs-identisch zum frueheren Inline-Block — die Reihenfolge der
    Checks (anonym -> fehlende Person -> Stufe zu niedrig) bleibt erhalten.
    """
    if not document_type.min_contact_stage:
        return
    if is_anonymous:
        raise ValidationError(
            _(
                "Anonyme Kontakte sind bei diesem Dokumentationstyp nicht erlaubt, "
                "da eine Mindest-Kontaktstufe vorausgesetzt wird."
            )
        )
    if client is None:
        raise ValidationError(
            _(
                "Für diesen Dokumentationstyp muss eine Person ausgewählt werden, "
                "da eine Mindest-Kontaktstufe vorausgesetzt wird."
            )
        )
    required = stage_index(document_type.min_contact_stage)
    actual = stage_index(client.contact_stage)
    if actual < required:
        raise ValidationError(
            _(
                "Person muss mindestens die Kontaktstufe "
                "'%(required_stage)s' "
                "haben, aktuelle Stufe ist "
                "'%(actual_stage)s'."
            )
            % {
                # Refs #1805: beide Seiten als Anzeige-Label. Vorher stand
                # hier der Rohwert ("... Kontaktstufe 'qualified' ...") neben dem
                # deutschen Label der Ist-Stufe — englischer Enum-Wert und
                # deutsches Label in einem Satz.
                "required_stage": Client.ContactStage(document_type.min_contact_stage).label,
                "actual_stage": client.get_contact_stage_display(),
            }
        )


@transaction.atomic
def create_event(
    facility,
    user,
    document_type,
    occurred_at,
    data_json,
    client=None,
    is_anonymous=False,
    case=None,
    idempotency_key=None,
    idempotency_payload_hash=None,
):
    """Create an event + EventHistory(CREATE).

    ``idempotency_key`` und ``idempotency_payload_hash`` (Review R5/R6, #1909):
    Der normalisierte Schlüssel und der SHA-256-Fingerprint des Requests werden
    als persistenter DB-Backstop mitgeschrieben. Constraint und Hash fangen
    Duplikate sowie Payload-Kollisionen auch bei Cache-Ausfall ab.
    """
    if document_type.facility_id != facility.pk:
        raise ValueError("DocumentType gehört nicht zur Facility")
    # Refs #1058 (Review-Haertung, Defense-in-Depth): analog zum Guard in
    # ``create_client``/``create_case`` (core.services.client.main /
    # core.services.case.cases). Alle heutigen Aufrufer (Views/Offline-Replay)
    # filtern bereits auf entity_type=event; dieser Guard schuetzt kuenftige
    # Direkt-Aufrufer, die den View-Filter umgehen wuerden.
    if document_type.entity_type != DocumentType.EntityType.EVENT:
        raise ValidationError(_("Der Dokumentationstyp ist nicht für Ereignisse vorgesehen."))
    if client and client.facility_id != facility.pk:
        raise ValueError("Client gehört nicht zur Facility")
    _validate_case_assignment(facility, case, client, is_anonymous)

    if user is not None and not user_can_see_document_type(user, document_type):
        raise PermissionDenied(_("Diese Dokumentation darf von Ihrer Rolle nicht erstellt werden."))

    if client is None and not is_anonymous and not document_type.min_contact_stage:
        is_anonymous = True

    _validate_contact_stage(document_type, client, is_anonymous)

    data_json = _validate_data_json(document_type, data_json)

    event = Event(
        facility=facility,
        client=client,
        document_type=document_type,
        occurred_at=occurred_at,
        data_json=data_json,
        search_text=compute_event_search_text(data_json, document_type),
        is_anonymous=is_anonymous,
        created_by=user,
        case=case,
        idempotency_key=idempotency_key,
        idempotency_payload_hash=idempotency_payload_hash,
    )
    event.save()
    EventHistory.objects.create(
        event=event,
        changed_by=user,
        action=EventHistory.Action.CREATE,
        data_after=data_json,
        field_metadata=_snapshot_field_metadata(document_type),
    )
    summary = f"{document_type.name}"
    if client:
        summary += f" für {client.pseudonym}"
    log_activity(
        facility=facility,
        actor=user,
        verb=Activity.Verb.CREATED,
        target=event,
        summary=summary,
    )
    audit_event(
        AuditLog.Action.EVENT_CREATE,
        user=user,
        facility=facility,
        target_obj=event,
        detail={"document_type": document_type.name, "is_anonymous": is_anonymous},
    )
    return event


@transaction.atomic
def update_event(event, user, data_json, expected_updated_at=None, require_version_token=False, **kwargs):
    """Update an event + EventHistory(UPDATE).

    Refs #734: nutzt zentrale ``check_version_conflict`` statt eigenem
    ``str(updated_at)``-Vergleich.

    ``require_version_token`` (Refs #1338): wird 1:1 an
    ``check_version_conflict`` durchgereicht. ``EventUpdateView`` setzt es
    auf das Ergebnis von ``_wants_json_response`` — JSON-/Offline-Replay-
    Clients müssen dadurch einen Token mitschicken, während der klassische
    HTML-Formular-Pfad beim Default ``False`` bleibt (kein Verhaltensbruch).

    Allowlist (Refs #1789): ``**kwargs`` bleibt in der Signatur, die Menge der
    erlaubten Felder ist aber **leer**. ``update_event`` war die letzte der
    vier Update-Services ohne Mass-Assignment-Prüfung (Restpunkt aus #1786):
    jedes durchgereichte Feld landete blind im ``setattr``. Kein heutiger
    Aufrufer nutzt Kwargs — ``EventUpdateView.post`` übergibt ausschließlich
    ``data_json``. Ein künftiges Update-Feld muss deshalb bewusst in
    ``allowed`` eingetragen werden, statt still durchzurutschen.
    """
    from core.services.security import check_version_conflict

    check_version_conflict(event, expected_updated_at, require_token=require_version_token)
    # Läuft ZUERST (Muster ``update_case``/``update_workitem``): die
    # Mass-Assignment-Prüfung darf weder vom Diff noch von der
    # ``data_json``-Validierung abhängen — ein abgewiesener Kwarg darf
    # keinen Teil-Write hinterlassen.
    allowed: set[str] = set()
    for key in kwargs:
        if key not in allowed:
            raise ValueError(f"Feld '{key}' darf nicht aktualisiert werden.")

    data_json = _validate_data_json(event.document_type, data_json)
    data_before = event.data_json.copy() if event.data_json else {}
    event.data_json = data_json
    event.search_text = compute_event_search_text(data_json, event.document_type)
    # Solange ``allowed`` leer ist, laeuft diese Schleife nie — sie bleibt als
    # Write-Pfad stehen, damit das Eintragen eines Feldes in ``allowed`` eine
    # Ein-Zeilen-Aenderung ist (Symmetrie zu den drei Geschwister-Services).
    for k, v in kwargs.items():  # pragma: no cover
        setattr(event, k, v)
    event.save()
    EventHistory.objects.create(
        event=event,
        changed_by=user,
        action=EventHistory.Action.UPDATE,
        data_before=data_before,
        data_after=data_json,
        field_metadata=_snapshot_field_metadata(event.document_type),
    )
    return event


def decrypt_event_text_data(event):
    """Refs #1160: Klartext-``data_json`` eines Events fuer Form-Vorbelegung.

    Liefert ein ``{slug: wert}``-Dict, in dem File-Marker (legacy ``__file__``
    und Stufe-B ``__files__``) uebersprungen und alle uebrigen Werte via
    :func:`safe_decrypt` (Default ``""``) entschluesselt sind. Aus
    ``EventUpdateView.get``/``post`` herausgezogen — beide Stellen bauten
    bisher dieselbe Schleife (Duplikat, Refs #1160 R1b).
    """
    from core.services.file_vault import safe_decrypt

    result = {}
    for key, value in (event.data_json or {}).items():
        if isinstance(value, dict) and (value.get("__file__") or value.get("__files__")):
            continue
        result[key] = safe_decrypt(value, default="")
    return result


def merge_update_payload(event, merged, restricted_keys, document_type):
    """Refs #1160: Update-Payload um Felder ergaenzen, die das Formular nicht traegt.

    Aus ``EventUpdateView.post`` herausgezogen (R1b). Mutiert ``merged`` in-place
    und liefert es zurueck:

    - **Restricted-Felder** (vom User nicht sichtbar, vorher per
      ``remove_restricted_fields`` aus dem Form entfernt) werden mit ihrem
      Original-Wert aus ``event.data_json`` wieder eingesetzt — sonst wuerde der
      Update sie loeschen.
    - **FILE-Felder** behalten ihren bestehenden Marker (beide Formate), damit
      ``apply_attachment_changes`` ihn anschliessend anpassen kann.
    """
    event_data = event.data_json or {}

    # Re-insert restricted fields with original values
    for key in restricted_keys:
        if key in event_data:
            merged[key] = event_data[key]

    # Für FILE-Felder: bestehenden Marker (beide Formate) erstmal beibehalten —
    # apply_attachment_changes passt ihn anschliessend an.
    field_templates = build_field_template_lookup(document_type)
    for slug, ft in field_templates.items():
        if ft.field_type == FieldTemplate.FieldType.FILE:
            existing_marker = event_data.get(slug)
            if isinstance(existing_marker, dict) and (
                existing_marker.get("__file__") or existing_marker.get("__files__")
            ):
                merged[slug] = existing_marker
    return merged


@transaction.atomic
def soft_delete_event(event, user):
    """Soft-Delete + EventHistory(DELETE) + AuditLog."""
    history_payload = build_redacted_delete_history(event)
    event.is_deleted = True
    event.data_json = {}
    delete_attachments_for(event)
    event.save()
    EventHistory.objects.create(
        event=event,
        changed_by=user,
        action=EventHistory.Action.DELETE,
        data_before=history_payload,
        field_metadata=_snapshot_field_metadata(event.document_type),
    )
    audit_event(
        AuditLog.Action.DELETE,
        user=user,
        facility=event.facility,
        target_obj=event,
        detail={
            "document_type": event.document_type.name,
            # L13 (Refs #1375): neutrale client_id statt Klartext-Pseudonym —
            # Data-Min-Residuum, konsistent zum gehärteten Client-Löschpfad
            # (Refs #1093). Das append-only-AuditLog konserviert das Pseudonym
            # sonst dauerhaft, obwohl der Löschzweck es nicht braucht.
            "client_id": (str(event.client_id) if event.client_id else None),
            "occurred_at": str(event.occurred_at),
        },
    )
    log_activity(
        facility=event.facility,
        actor=user,
        verb=Activity.Verb.DELETED,
        target=event,
        summary=f"{event.document_type.name} gelöscht",
    )
