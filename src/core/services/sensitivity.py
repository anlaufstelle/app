"""Centralized sensitivity and role-based field visibility logic.

Single source of truth for determining whether a user role may access
a field or event based on document-type sensitivity and encryption status.
"""

from core.models import DocumentType
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
    User.Role.ADMIN: 2,  # all
}


def effective_sensitivity(doc_type_sensitivity, is_encrypted):
    """Return the numeric sensitivity rank for a field.

    The effective sensitivity is the *higher* of the document-type level and
    the field-level flag (encrypted fields are treated as HIGH).
    """
    base = SENSITIVITY_RANK.get(doc_type_sensitivity, 0)
    if is_encrypted:
        return max(base, SENSITIVITY_RANK[DocumentType.Sensitivity.HIGH])
    return base


def user_can_see_field(user, doc_type_sensitivity, is_encrypted):
    """Return True if *user* may see a field with the given sensitivity."""
    max_allowed = ROLE_MAX_SENSITIVITY.get(user.role, 0)
    return effective_sensitivity(doc_type_sensitivity, is_encrypted) <= max_allowed


def user_can_see_event(user, event):
    """Return True if *user* may see the event based on its DocumentType sensitivity."""
    doc_type = event.document_type
    return ROLE_MAX_SENSITIVITY.get(user.role, 0) >= SENSITIVITY_RANK.get(doc_type.sensitivity, 0)


def allowed_sensitivities_for_user(user):
    """Return list of DocumentType.Sensitivity values the user may access."""
    max_rank = ROLE_MAX_SENSITIVITY.get(user.role, 0)
    return [s for s, rank in SENSITIVITY_RANK.items() if rank <= max_rank]
