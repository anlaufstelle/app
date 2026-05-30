"""Centralized sensitivity and role-based field visibility logic.

Single source of truth for determining whether a user role may access
a field or event based on document-type sensitivity and field-level
sensitivity override.
"""

from django.http import Http404

from core.models import DocumentType, Event
from core.models.attachment import EventAttachment
from core.models.user import User

# Ordered sensitivity levels for comparison
SENSITIVITY_RANK = {
    DocumentType.Sensitivity.NORMAL: 0,
    DocumentType.Sensitivity.ELEVATED: 1,
    DocumentType.Sensitivity.HIGH: 2,
}

# Maximum sensitivity a role may see
ROLE_MAX_SENSITIVITY = {
    User.Role.ASSISTANT: 0,  # NORMAL only
    User.Role.STAFF: 1,  # up to ELEVATED
    User.Role.LEAD: 2,  # all
    User.Role.FACILITY_ADMIN: 2,  # all
}


def effective_sensitivity(doc_type_sensitivity, field_sensitivity=""):
    """Return the numeric sensitivity rank for a field.

    The effective sensitivity is the *higher* of the document-type level and
    the field-level override. An empty field_sensitivity means "inherit from
    document type".
    """
    base = SENSITIVITY_RANK.get(doc_type_sensitivity, 0)
    if field_sensitivity:
        field_rank = SENSITIVITY_RANK.get(field_sensitivity, 0)
        return max(base, field_rank)
    return base


def user_can_see_field(user, doc_type_sensitivity, field_sensitivity=""):
    """Return True if *user* may see a field with the given sensitivity."""
    max_allowed = ROLE_MAX_SENSITIVITY.get(user.role, 0)
    return effective_sensitivity(doc_type_sensitivity, field_sensitivity) <= max_allowed


def user_can_see_event(user, event):
    """Return True if *user* may see the event based on its DocumentType sensitivity."""
    doc_type = event.document_type
    return ROLE_MAX_SENSITIVITY.get(user.role, 0) >= SENSITIVITY_RANK.get(doc_type.sensitivity, 0)


def user_can_see_document_type(user, document_type):
    """Return True if *user* may create or interact with events of the given DocumentType.

    Same role-vs-sensitivity check as :func:`user_can_see_event`, but operates
    on the DocumentType directly so it can be used to filter form querysets and
    HTMX partial endpoints before any event exists.
    """
    return ROLE_MAX_SENSITIVITY.get(user.role, 0) >= SENSITIVITY_RANK.get(document_type.sensitivity, 0)


def allowed_sensitivities_for_user(user):
    """Return list of DocumentType.Sensitivity values the user may access."""
    max_rank = ROLE_MAX_SENSITIVITY.get(user.role, 0)
    return [s for s, rank in SENSITIVITY_RANK.items() if rank <= max_rank]


def get_visible_event_or_404(user, facility, pk, *, select_related=None):
    """Load an event scoped to facility and filtered by role visibility.

    Raises Http404 when the event does not exist, belongs to another facility,
    is soft-deleted, or the user's role may not see its DocumentType sensitivity.
    The 404 is intentional even for existing-but-hidden events: revealing
    existence (via 403/maskiertes 200) would leak metadata like pseudonyms or
    document type names to roles that are not entitled to know an event of that
    sensitivity exists.
    """
    qs = Event.objects.visible_to(user).filter(
        pk=pk,
        facility=facility,
        is_deleted=False,
    )
    if select_related:
        qs = qs.select_related(*select_related)
    event = qs.first()
    if event is None:
        raise Http404("Event not found")
    return event


def get_visible_attachment_or_404(user, facility, event_pk, attachment_pk):
    """Load an event attachment only if the parent event is visible to *user*.

    Event visibility is enforced first (role vs. DocumentType sensitivity).
    Field-level sensitivity for the attachment's own field template stays a
    caller responsibility since it may require :class:`PermissionDenied`
    semantics rather than 404.
    """
    event = get_visible_event_or_404(user, facility, event_pk, select_related=("document_type",))
    attachment = EventAttachment.objects.filter(pk=attachment_pk, event=event).first()
    if attachment is None:
        raise Http404("Attachment not found")
    return event, attachment
