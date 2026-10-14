"""Fixtures for guards that exercise the final manual retention decision."""

from django.utils import timezone

from core.models import User


def first_approval_fields(facility):
    """Supply a distinct eligible first reviewer without taking the audit-chain lock."""
    reviewer, _created = User.objects.get_or_create(
        facility=facility,
        username=f"retention-first-reviewer-{facility.pk}",
        defaults={"role": User.Role.FACILITY_ADMIN, "can_confirm_deletion": True},
    )
    return {"first_approved_by": reviewer, "first_approved_at": timezone.now()}


def prime_first_approval(proposal):
    """Seed a first decision so existing guards still verify the final approval."""
    fields = first_approval_fields(proposal.facility)
    type(proposal).objects.filter(facility=proposal.facility, pk=proposal.pk).update(**fields)
    proposal.refresh_from_db()
