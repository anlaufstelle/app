"""Shared case assignment boundary for event creation, redisplay and pickers."""

from django.core.exceptions import ValidationError
from django.db.models import Q

from core.models import Case
from core.services.case import cases_with_live_client
from core.services.compliance import allowed_sensitivities_for_user


class CaseAssignmentError(ValidationError):
    """Keep assignment failures distinct from dynamic fields named case."""


def assignable_cases(facility, user):
    """Offer live open cases using the existing role and sensitivity policy."""
    cases = cases_with_live_client().for_facility(facility).filter(status=Case.Status.OPEN, anonymized_at__isnull=True)
    if user is None or not user.is_staff_or_above or user.facility_id != facility.pk:
        return cases.none()
    return cases.filter(
        Q(document_type__isnull=True) | Q(document_type__sensitivity__in=allowed_sensitivities_for_user(user))
    )
