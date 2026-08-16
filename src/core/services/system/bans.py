"""Service for ban detection."""

import logging
from datetime import date

from django.utils import timezone

from core.models import DocumentType, Event
from core.services.compliance import user_can_see_field
from core.services.file_vault import safe_decrypt

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
        active, bis_date, grund = _project_ban_data(
            data,
            today=today,
            may_see_grund=_user_may_see_grund(user, event),
        )
        if not active:
            continue

        entry = {
            "event": event,
            "grund": grund,
            "bis_datum": bis_date,
        }
        if include_client_in_result:
            entry["client"] = event.client

        active_bans.append(entry)

    return active_bans


def _project_ban_data(data, *, today, may_see_grund):
    """Shared active/expiry/reason projection for UI and watch revisions."""

    aktiv_raw = safe_decrypt(data.get("aktiv", False), default=False)
    if not aktiv_raw:
        return False, None, ""

    bis_raw = safe_decrypt(data.get("bis", ""), default="")
    bis_date = None
    if bis_raw:
        try:
            bis_date = date.fromisoformat(str(bis_raw))
            if bis_date < today:
                return False, None, ""
        except (ValueError, TypeError):
            # Defekt gespeichertes Datum -> als "kein Ablauf" behandeln,
            # aber fuer die Diagnose protokollieren (Refs #1011).
            logger.debug("Ungueltiges Ban-'bis'-Datum ignoriert: %r", bis_raw)

    grund = safe_decrypt(data.get("grund", ""), default="") if may_see_grund else ""
    return True, bis_date, grund


def get_active_bans_revision(facility, user=None):
    """Values-only fingerprint source matching :func:`get_active_bans`.

    The safety projection intentionally ignores Event sensitivity for ban
    *presence*, exactly like the banner. The reason enters the tuple only if
    the role may see it, so a reason-only edit cannot become a timing leak for
    an assistant.
    """

    today = timezone.localdate()
    rows = (
        Event.objects.for_facility(facility)
        .filter(document_type__system_type="ban", document_type__is_active=True, is_deleted=False)
        .values(
            "id",
            "client_id",
            "client__pseudonym",
            "document_type__sensitivity",
            "data_json",
        )
        .order_by("-occurred_at")
    )
    revision = []
    for row in rows:
        may_see_grund = user is not None and user_can_see_field(
            user,
            row["document_type__sensitivity"],
            field_sensitivity="",
        )
        active, bis_date, grund = _project_ban_data(
            row["data_json"] or {},
            today=today,
            may_see_grund=may_see_grund,
        )
        if not active:
            continue
        revision.append(
            (
                str(row["id"]),
                (str(row["client_id"]), row["client__pseudonym"]) if row["client_id"] else None,
                grund,
                bis_date,
            )
        )
    return tuple(revision)


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
    ban_doc_types = DocumentType.objects.for_facility(facility).filter(system_type="ban", is_active=True)

    if not ban_doc_types.exists():
        return []

    events = (
        Event.objects.for_facility(facility)
        .filter(document_type__in=ban_doc_types, is_deleted=False)
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

    ban_doc_types = DocumentType.objects.for_facility(facility).filter(system_type="ban", is_active=True)

    if not ban_doc_types.exists():
        return []

    events = (
        Event.objects.for_facility(facility)
        .filter(client=client, document_type__in=ban_doc_types, is_deleted=False)
        .select_related("client", "document_type")
        .order_by("-occurred_at")
    )

    return _collect_active_bans(events, user, include_client_in_result=False)
