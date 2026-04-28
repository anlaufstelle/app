"""Service layer for retention proposals and legal holds."""

from datetime import date, timedelta

from django.db import transaction
from django.db.utils import IntegrityError
from django.utils import timezone

from core.models import AuditLog, LegalHold, RetentionProposal


def create_proposal(facility, target_type, target_id, deletion_due_at, details, category):
    """Create a retention proposal idempotently (skip if active proposal exists).

    „Aktiv" umfasst PENDING, HELD und DEFERRED — dieselbe Menge, die die
    `unique_active_retention_proposal`-Constraint im Model abbildet (Refs #585).
    """
    active_statuses = [
        RetentionProposal.Status.PENDING,
        RetentionProposal.Status.HELD,
        RetentionProposal.Status.DEFERRED,
    ]
    try:
        proposal, created = RetentionProposal.objects.get_or_create(
            facility=facility,
            target_type=target_type,
            target_id=target_id,
            status__in=active_statuses,
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
            status__in=active_statuses,
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
def defer_proposal(proposal, user, days=30):
    """Defer a retention proposal by `days` (default 30) — sets DEFERRED status."""
    deferred_until = date.today() + timedelta(days=days)
    proposal.status = RetentionProposal.Status.DEFERRED
    proposal.deferred_until = deferred_until
    proposal.defer_count = (proposal.defer_count or 0) + 1
    proposal.save(update_fields=["status", "deferred_until", "defer_count"])
    AuditLog.objects.create(
        facility=proposal.facility,
        user=user,
        action=AuditLog.Action.DELETE,
        target_type=proposal.target_type,
        target_id=str(proposal.target_id),
        detail={
            "category": "retention_proposal_deferred",
            "retention_category": proposal.retention_category,
            "deferred_until": str(deferred_until),
            "defer_count": proposal.defer_count,
            "days": days,
        },
    )
    return proposal


@transaction.atomic
def reject_proposal(proposal, user):
    """Reject a retention proposal — marks it as REJECTED, no deletion will occur."""
    proposal.status = RetentionProposal.Status.REJECTED
    proposal.save(update_fields=["status"])
    AuditLog.objects.create(
        facility=proposal.facility,
        user=user,
        action=AuditLog.Action.DELETE,
        target_type=proposal.target_type,
        target_id=str(proposal.target_id),
        detail={
            "category": "retention_proposal_rejected",
            "retention_category": proposal.retention_category,
        },
    )
    return proposal


@transaction.atomic
def bulk_approve_proposals(proposals, user):
    """Approve multiple proposals in a single transaction.

    Returns count of processed proposals.
    """
    count = 0
    for proposal in proposals:
        approve_proposal(proposal, user)
        count += 1
    return count


@transaction.atomic
def bulk_defer_proposals(proposals, user, days=30):
    """Defer multiple proposals in a single transaction.

    Returns count of processed proposals.
    """
    count = 0
    for proposal in proposals:
        defer_proposal(proposal, user, days=days)
        count += 1
    return count


@transaction.atomic
def bulk_reject_proposals(proposals, user):
    """Reject multiple proposals in a single transaction.

    Returns count of processed proposals.
    """
    count = 0
    for proposal in proposals:
        reject_proposal(proposal, user)
        count += 1
    return count


@transaction.atomic
def reactivate_deferred_proposals(facility):
    """Reactivate deferred proposals whose `deferred_until` has passed.

    Behavior depends on facility settings:
    - If `retention_auto_approve_after_defer=True` AND `defer_count` has exceeded
      `retention_max_defer_count`: proposal is auto-approved.
    - Otherwise: proposal reverts to PENDING for a new manual decision.

    Returns a tuple `(reactivated_count, auto_approved_count)`.
    """
    today = date.today()
    reactivated = 0
    auto_approved = 0

    try:
        settings_obj = facility.settings
        auto_approve = settings_obj.retention_auto_approve_after_defer
        max_defer_count = settings_obj.retention_max_defer_count
    except facility._meta.model.settings.RelatedObjectDoesNotExist:
        auto_approve = False
        max_defer_count = 2

    due_proposals = RetentionProposal.objects.filter(
        facility=facility,
        status=RetentionProposal.Status.DEFERRED,
        deferred_until__lte=today,
    )

    for proposal in due_proposals:
        if auto_approve and proposal.defer_count >= max_defer_count:
            proposal.status = RetentionProposal.Status.APPROVED
            proposal.save(update_fields=["status"])
            AuditLog.objects.create(
                facility=proposal.facility,
                action=AuditLog.Action.DELETE,
                target_type=proposal.target_type,
                target_id=str(proposal.target_id),
                detail={
                    "category": "retention_proposal_auto_approved",
                    "retention_category": proposal.retention_category,
                    "defer_count": proposal.defer_count,
                    "max_defer_count": max_defer_count,
                },
            )
            auto_approved += 1
        else:
            proposal.status = RetentionProposal.Status.PENDING
            proposal.save(update_fields=["status"])
            AuditLog.objects.create(
                facility=proposal.facility,
                action=AuditLog.Action.DELETE,
                target_type=proposal.target_type,
                target_id=str(proposal.target_id),
                detail={
                    "category": "retention_proposal_reactivated",
                    "retention_category": proposal.retention_category,
                    "defer_count": proposal.defer_count,
                },
            )
            reactivated += 1

    return reactivated, auto_approved


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
