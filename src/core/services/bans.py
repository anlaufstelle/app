"""Service for ban detection."""

import logging
from datetime import date

from django.utils import timezone

from core.models import DocumentType, Event
from core.services.encryption import safe_decrypt
from core.services.sensitivity import user_can_see_field

logger = logging.getLogger(__name__)


def _user_may_see_grund(user, event):
    """Whether *user* may view the ban reason for *event*.

    Conservative default: when no user context is available, the reason
    must NOT be revealed. Callers without an authenticated user should
    pass ``user=None`` explicitly.
    """
    if user is None:
        return False
    return user_can_see_field(user, event.document_type.sensitivity, field_sensitivity="")


def _collect_active_bans(events, user, *, include_client_in_result):
    """Filter *events* to active bans and shape them for templates.

    Shared between :func:`get_active_bans` (facility scope) and
    :func:`get_active_bans_for_client` (per-client scope). The reason
    field (``grund``) is only decrypted if *user* is allowed to see it.
    """
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

        grund = ""
        if _user_may_see_grund(user, event):
            grund = safe_decrypt(data.get("grund", ""), default="")

        entry = {
            "event": event,
            "grund": grund,
            "bis_datum": bis_date,
        }
        if include_client_in_result:
            entry["client"] = event.client

        active_bans.append(entry)

    return active_bans


def get_active_bans(facility, user=None):
    """Determine active bans for a facility.

    A ban is considered active when:
    - DocumentType system_type="ban"
    - Event is not deleted
    - Field "Aktiv" is truthy
    - Field "Bis" is empty or >= today

    If *user* is given, the ban reason (grund) is only included when the
    user's role permits viewing the document type's sensitivity level.
    Without a *user* the reason is redacted.
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
        .select_related("client", "document_type")
        .order_by("-occurred_at")
    )

    return _collect_active_bans(events, user, include_client_in_result=True)


def get_active_bans_for_client(client, user=None):
    """Determine active bans for a single client.

    Same role-based reason redaction as :func:`get_active_bans`. Callers
    that render the result for a UI should always pass ``request.user``;
    omitting it conservatively redacts the reason.
    """
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
        .select_related("client", "document_type")
        .order_by("-occurred_at")
    )

    return _collect_active_bans(events, user, include_client_in_result=False)
