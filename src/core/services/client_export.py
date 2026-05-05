"""Client data export for DSGVO Art. 15 (right of access) and Art. 20 (data portability)."""

import weasyprint
from django.template.loader import render_to_string
from django.utils import timezone

from core.models import Case as CaseModel
from core.models import DeletionRequest, Event, EventHistory, WorkItem
from core.services.encryption import safe_decrypt


def _gather_client_fields(client):
    """Serialize the client's master data."""
    return {
        "pseudonym": client.pseudonym,
        "contact_stage": client.get_contact_stage_display(),
        "age_cluster": client.get_age_cluster_display(),
        "is_active": client.is_active,
        "created_at": client.created_at.isoformat(),
        "created_by": client.created_by.username if client.created_by else None,
    }


def _serialize_event(event):
    """Serialize a single Event instance, decrypting encrypted data_json values."""
    decrypted_data = {}
    if event.data_json:
        for key, value in event.data_json.items():
            if isinstance(value, dict):
                decrypted_data[key] = safe_decrypt(value)
            else:
                decrypted_data[key] = value
    return {
        "document_type": event.document_type.name,
        "occurred_at": event.occurred_at.isoformat(),
        "created_by": event.created_by.username if event.created_by else None,
        "data": decrypted_data,
    }


def _gather_events(client, user):
    """Return (events_data, event_ids) for non-deleted events visible to ``user``.

    Refs #734: ``user`` ist Pflicht, damit ``visible_to(user)`` greift.
    Ohne den Filter wuerden HIGH-Sensitivity-Events auch bei einem
    Staff-Triggered-Export landen (heute durch Lead/Admin-Mixin auf der
    View entschaerft, aber Service-Layer muss sich nicht auf den Caller
    verlassen).
    """
    events = (
        Event.objects.visible_to(user)
        .filter(client=client, is_deleted=False)
        .select_related("document_type", "created_by")
        .order_by("-occurred_at")
    )
    events_data = []
    event_ids = []
    for event in events:
        event_ids.append(event.pk)
        events_data.append(_serialize_event(event))
    return events_data, event_ids


def _gather_cases(client):
    """Serialize all cases linked to the client."""
    cases = CaseModel.objects.filter(client=client).select_related("created_by", "lead_user").order_by("-created_at")
    return [
        {
            "title": case.title,
            "description": case.description,
            "status": case.get_status_display(),
            "created_at": case.created_at.isoformat(),
            "closed_at": case.closed_at.isoformat() if case.closed_at else None,
            "lead_user": case.lead_user.username if case.lead_user else None,
            "created_by": case.created_by.username if case.created_by else None,
        }
        for case in cases
    ]


def _gather_event_history(event_ids):
    """Serialize EventHistory entries for the given event IDs."""
    history_entries = (
        EventHistory.objects.filter(event_id__in=event_ids).select_related("changed_by").order_by("-changed_at")
    )
    return [
        {
            "action": entry.get_action_display(),
            "changed_at": entry.changed_at.isoformat(),
            "changed_by": entry.changed_by.username if entry.changed_by else None,
        }
        for entry in history_entries
    ]


def _gather_deletion_requests(event_ids):
    """Serialize DeletionRequest entries for the given event IDs."""
    deletion_requests = (
        DeletionRequest.objects.filter(target_id__in=event_ids, target_type="Event")
        .select_related("requested_by", "reviewed_by")
        .order_by("-created_at")
    )
    return [
        {
            "status": dr.get_status_display(),
            "reason": dr.reason,
            "requested_by": dr.requested_by.username if dr.requested_by else None,
            "reviewed_by": dr.reviewed_by.username if dr.reviewed_by else None,
            "created_at": dr.created_at.isoformat(),
            "reviewed_at": dr.reviewed_at.isoformat() if dr.reviewed_at else None,
        }
        for dr in deletion_requests
    ]


def _gather_workitems(client):
    """Serialize all work items linked to the client."""
    workitems = WorkItem.objects.filter(client=client).order_by("-created_at")
    return [
        {
            "title": wi.title,
            "description": wi.description,
            "status": wi.get_status_display(),
            "priority": wi.get_priority_display(),
            "created_at": wi.created_at.isoformat(),
            "due_date": wi.due_date.isoformat() if wi.due_date else None,
        }
        for wi in workitems
    ]


def _build_export_meta(facility):
    """Return export metadata (timestamp + resolved facility name)."""
    facility_name = getattr(getattr(facility, "settings", None), "facility_full_name", "") or facility.name
    return {
        "timestamp": timezone.now().isoformat(),
        "facility_name": facility_name,
    }


def export_client_data(client, facility, user):
    """Collect all personal data for a client visible to ``user``.

    Refs #734: ``user`` ist Pflicht-Parameter — die Sensitivity-
    Filterung greift via ``Event.objects.visible_to(user)`` in
    ``_gather_events``. Lead/Admin sehen alles, Staff sieht
    NORMAL+ELEVATED, Assistant nur NORMAL.
    """
    events_data, event_ids = _gather_events(client, user)
    return {
        "client": _gather_client_fields(client),
        "events": events_data,
        "cases": _gather_cases(client),
        "event_history": _gather_event_history(event_ids),
        "deletion_requests": _gather_deletion_requests(event_ids),
        "work_items": _gather_workitems(client),
        "export_meta": _build_export_meta(facility),
    }


def export_client_data_pdf(client, facility, user):
    """Generate PDF from client data. Returns bytes."""
    data = export_client_data(client, facility, user)
    html = render_to_string(
        "core/export/client_data_pdf.html",
        {
            "facility_name": data["export_meta"]["facility_name"],
            "client": data["client"],
            "events": data["events"],
            "cases": data["cases"],
            "event_history": data["event_history"],
            "deletion_requests": data["deletion_requests"],
            "work_items": data["work_items"],
            "generated_at": timezone.now(),
        },
    )
    return weasyprint.HTML(string=html).write_pdf()
