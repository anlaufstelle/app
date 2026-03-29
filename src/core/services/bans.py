"""Service for ban detection."""

import logging
from datetime import date

from django.utils import timezone

from core.models import DocumentType, Event
from core.services.encryption import safe_decrypt
from core.services.sensitivity import user_can_see_field

logger = logging.getLogger(__name__)


def get_active_bans(facility, user=None):
    """Determine active bans for a facility.

    A ban is considered active when:
    - DocumentType name="Hausverbot", category=ADMIN
    - Event is not deleted
    - Field "Aktiv" is truthy
    - Field "Bis" is empty or >= today

    If *user* is given, the ban reason (grund) is only included when the
    user's role permits viewing the document type's sensitivity level.
    """
    ban_doc_types = DocumentType.objects.filter(
        facility=facility,
        system_type="ban",
        is_active=True,
    )

    if not ban_doc_types.exists():
        return []

    events = (
        Event.objects.filter(
            facility=facility,
            document_type__in=ban_doc_types,
            is_deleted=False,
        )
        .select_related("client")
        .order_by("-occurred_at")
    )

    today = timezone.localdate()
    active_bans = []

    for event in events:
        data = event.data_json or {}

        aktiv_raw = safe_decrypt(data.get("aktiv", False), default=False)
        if not aktiv_raw:
            continue

        bis_raw = safe_decrypt(data.get("bis", ""), default="")
        bis_date = None
        if bis_raw:
            try:
                bis_date = date.fromisoformat(str(bis_raw))
                if bis_date < today:
                    continue
            except (ValueError, TypeError):
                pass

        # Only reveal the ban reason if the user's role allows the doc type sensitivity
        grund = ""
        if user is None or user_can_see_field(user, event.document_type.sensitivity, is_encrypted=False):
            grund = safe_decrypt(data.get("grund", ""), default="")

        active_bans.append(
            {
                "event": event,
                "client": event.client,
                "grund": grund,
                "bis_datum": bis_date,
            }
        )

    return active_bans


def get_active_bans_for_client(client):
    """Determine active bans for a client."""
    facility = client.facility

    ban_doc_types = DocumentType.objects.filter(
        facility=facility,
        system_type="ban",
        is_active=True,
    )

    if not ban_doc_types.exists():
        return []

    events = (
        Event.objects.filter(
            client=client,
            facility=facility,
            document_type__in=ban_doc_types,
            is_deleted=False,
        )
        .select_related("client")
        .order_by("-occurred_at")
    )

    today = timezone.localdate()
    active_bans = []

    for event in events:
        data = event.data_json or {}

        aktiv_raw = safe_decrypt(data.get("aktiv", False), default=False)
        if not aktiv_raw:
            continue

        bis_raw = safe_decrypt(data.get("bis", ""), default="")
        bis_date = None
        if bis_raw:
            try:
                bis_date = date.fromisoformat(str(bis_raw))
                if bis_date < today:
                    continue
            except (ValueError, TypeError):
                pass

        grund = safe_decrypt(data.get("grund", ""), default="")

        active_bans.append(
            {
                "event": event,
                "grund": grund,
                "bis_datum": bis_date,
            }
        )

    return active_bans
