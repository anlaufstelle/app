"""Service layer for retention proposals and legal holds."""

from django.db import transaction
from django.db.utils import IntegrityError
from django.utils import timezone

from core.models import AuditLog, LegalHold, RetentionProposal


def create_proposal(facility, target_type, target_id, deletion_due_at, details, category):
    """Create a retention proposal idempotently (skip if active proposal exists)."""
    try:
        proposal, created = RetentionProposal.objects.get_or_create(
            facility=facility,
            target_type=target_type,
            target_id=target_id,
            status__in=[RetentionProposal.Status.PENDING, RetentionProposal.Status.HELD],
            defaults={
                "deletion_due_at": deletion_due_at,
                "details": details,
                "retention_category": category,
                "status": RetentionProposal.Status.PENDING,
            },
        )
        return proposal, created
    except IntegrityError:
        # Race condition: another process created it
        proposal = RetentionProposal.objects.get(
            facility=facility,
            target_type=target_type,
            target_id=target_id,
            status__in=[RetentionProposal.Status.PENDING, RetentionProposal.Status.HELD],
        )
        return proposal, False


@transaction.atomic
def approve_proposal(proposal, user):
    """Approve a retention proposal for deletion on next enforce_retention run."""
    proposal.status = RetentionProposal.Status.APPROVED
    proposal.save(update_fields=["status"])
    AuditLog.objects.create(
        facility=proposal.facility,
        user=user,
        action=AuditLog.Action.DELETE,
        target_type=proposal.target_type,
        target_id=str(proposal.target_id),
        detail={
            "category": "retention_proposal_approved",
            "retention_category": proposal.retention_category,
            "deletion_due_at": str(proposal.deletion_due_at),
        },
    )
    return proposal


@transaction.atomic
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
    AuditLog.objects.create(
        facility=proposal.facility,
        user=user,
        action=AuditLog.Action.LEGAL_HOLD,
        target_type=proposal.target_type,
        target_id=str(proposal.target_id),
        detail={
            "category": "legal_hold_created",
            "reason": reason,
            "expires_at": str(expires_at) if expires_at else None,
            "hold_id": str(hold.pk),
        },
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

    AuditLog.objects.create(
        facility=hold.facility,
        user=user,
        action=AuditLog.Action.LEGAL_HOLD,
        target_type=hold.target_type,
        target_id=str(hold.target_id),
        detail={
            "category": "legal_hold_dismissed",
            "hold_id": str(hold.pk),
            "reason": hold.reason,
        },
    )


def has_active_hold(facility, target_type, target_id):
    """Check if a target has an active (not dismissed, not expired) legal hold."""
    from datetime import date

    return (
        LegalHold.objects.filter(
            facility=facility,
            target_type=target_type,
            target_id=target_id,
            dismissed_at__isnull=True,
        )
        .exclude(
            expires_at__lt=date.today(),
        )
        .exists()
    )


def get_active_hold_target_ids(facility, target_type="Event"):
    """Return set of target_ids with active legal holds for a facility."""
    from datetime import date

    qs = LegalHold.objects.filter(
        facility=facility,
        target_type=target_type,
        dismissed_at__isnull=True,
    ).exclude(
        expires_at__lt=date.today(),
    )
    return set(qs.values_list("target_id", flat=True))


def get_dashboard_proposals(facility):
    """Get proposals grouped by retention_category for the dashboard."""
    proposals = RetentionProposal.objects.for_facility(facility).select_related("facility").order_by("deletion_due_at")

    grouped = {}
    for proposal in proposals:
        cat = proposal.retention_category
        if cat not in grouped:
            grouped[cat] = []
        grouped[cat].append(proposal)

    return grouped


def cleanup_stale_proposals(facility):
    """Remove proposals for events that have already been deleted."""
    from core.models import Event

    pending_proposals = RetentionProposal.objects.for_facility(facility).filter(
        target_type=RetentionProposal.TargetType.EVENT,
        status__in=[RetentionProposal.Status.PENDING, RetentionProposal.Status.APPROVED],
    )

    stale_ids = []
    for proposal in pending_proposals.iterator():
        if not Event.objects.filter(pk=proposal.target_id, is_deleted=False).exists():
            stale_ids.append(proposal.pk)

    if stale_ids:
        RetentionProposal.objects.filter(pk__in=stale_ids).delete()

    return len(stale_ids)
