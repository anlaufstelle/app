"""LegalHold-Lifecycle (#744).

Aus ``services/retention.py`` ausgekoppelt — eigenes Thema (Sperre
gegen Loeschung; orthogonal zu Vorschlaegen und Enforcement).
"""

from django.db import transaction
from django.utils import timezone

from core.models import AuditLog, LegalHold, RetentionProposal
from core.services.audit import audit_retention_decision


def create_legal_hold(proposal, user, reason, expires_at=None):
    """Create a legal hold and set the proposal to held status."""
    hold = LegalHold.objects.create(
        facility=proposal.facility,
        target_type=proposal.target_type,
        target_id=proposal.target_id,
        reason=reason,
        expires_at=expires_at,
        created_by=user,
    )
    proposal.status = RetentionProposal.Status.HELD
    proposal.save(update_fields=["status"])
    audit_retention_decision(
        proposal.facility,
        target_type=proposal.target_type,
        target_id=proposal.target_id,
        action=AuditLog.Action.LEGAL_HOLD,
        category="legal_hold_created",
        user=user,
        reason=reason,
        expires_at=str(expires_at) if expires_at else None,
        hold_id=str(hold.pk),
    )
    return hold


@transaction.atomic
def dismiss_legal_hold(hold, user):
    """Dismiss a legal hold and revert the proposal to pending."""
    hold.dismissed_at = timezone.now()
    hold.dismissed_by = user
    hold.save(update_fields=["dismissed_at", "dismissed_by"])

    # Revert associated proposal to pending
    RetentionProposal.objects.filter(
        facility=hold.facility,
        target_type=hold.target_type,
        target_id=hold.target_id,
        status=RetentionProposal.Status.HELD,
    ).update(status=RetentionProposal.Status.PENDING)

    audit_retention_decision(
        hold.facility,
        target_type=hold.target_type,
        target_id=hold.target_id,
        action=AuditLog.Action.LEGAL_HOLD,
        category="legal_hold_dismissed",
        user=user,
        hold_id=str(hold.pk),
        reason=hold.reason,
    )


def has_active_hold(facility, target_type, target_id):
    """Check if a target has an active (not dismissed, not expired) legal hold.

    Uses ``timezone.localdate()`` (Europe/Berlin) rather than the naive,
    server-local ``date.today()`` so the active/expired boundary stays in
    lockstep with ``LegalHold.is_active`` and the dashboard SQL filter near
    midnight (UTC vs. Berlin); see #1191 (Refs #1192).
    """
    return (
        LegalHold.objects.filter(
            facility=facility,
            target_type=target_type,
            target_id=target_id,
            dismissed_at__isnull=True,
        )
        .exclude(
            expires_at__lt=timezone.localdate(),
        )
        .exists()
    )


def get_active_hold_target_ids(facility, target_type="Event"):
    """Return set of target_ids with active legal holds for a facility.

    Uses ``timezone.localdate()`` (Europe/Berlin) for the active/expired
    boundary, in lockstep with ``has_active_hold`` and the dashboard filter;
    see #1191 (Refs #1192).
    """
    qs = LegalHold.objects.filter(
        facility=facility,
        target_type=target_type,
        dismissed_at__isnull=True,
    ).exclude(
        expires_at__lt=timezone.localdate(),
    )
    return set(qs.values_list("target_id", flat=True))
