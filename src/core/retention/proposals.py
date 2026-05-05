"""RetentionProposal-Lifecycle (#744 Phase 2).

Vorschlags-Verwaltung: Erzeugen (idempotent), Approve/Defer/Reject,
Bulk-Operationen, Dashboard-Aufbereitung, Reactivate-nach-Defer,
Stale-Cleanup, sowie die Pipeline ``create_proposals_for_facility``
fuer den ``--propose``-Lauf des ``enforce_retention``-Commands.
"""

from datetime import date, timedelta

from django.db import transaction
from django.db.utils import IntegrityError
from django.utils.translation import gettext_lazy as _

from core.constants import RETENTION_URGENCY_RED_DAYS, RETENTION_URGENCY_YELLOW_DAYS

# Refs #818 — Inline-Imports an Modulkopf gehoben.
from core.models import AuditLog, Event, LegalHold, RetentionProposal
from core.retention.legal_holds import get_active_hold_target_ids
from core.retention.strategies import iter_strategies

# Category labels for the dashboard grouping. Lives next to the service
# (not the view) because the context-builder needs them — Refs FND-A003.
DASHBOARD_CATEGORY_LABELS = {
    "anonymous": _("Anonym"),
    "identified": _("Identifiziert"),
    "qualified": _("Qualifiziert"),
    "document_type": _("Dokumenttyp"),
}


def _urgency_for(days_until):
    """Return ``"red" | "yellow" | "gray"`` based on days until deletion.

    Shared by the dashboard list and the per-card re-renders in
    :mod:`core.views.retention` after a status change — the 7/30-day
    thresholds used to live in four places.
    """
    if days_until <= RETENTION_URGENCY_RED_DAYS:
        return "red"
    if days_until <= RETENTION_URGENCY_YELLOW_DAYS:
        return "yellow"
    return "gray"


def annotate_urgency(proposal, *, today=None):
    """Set ``proposal.urgency`` in-place from ``deletion_due_at`` relative to today.

    Returns the proposal for convenience. Used by the per-card view handlers
    to avoid duplicating the 7/30-day thresholds.
    """
    today = today or date.today()
    proposal.urgency = _urgency_for((proposal.deletion_due_at - today).days)
    return proposal


def build_retention_dashboard_context(facility, user=None):
    """Build the template context dict for :class:`RetentionDashboardView`.

    Returns the complete dashboard context — proposals grouped by category,
    active-hold annotations, per-category counts, retention settings, and
    the date anchors the template needs. The view then layers HTMX-specific
    decisions (partial vs. full template) on top.

    Extracted from the view (Refs FND-A003) so the ~90-LOC block of
    grouping, hold-lookup and urgency-colouring can be unit-tested and
    reused without going through the HTTP layer. The ``user`` argument is
    accepted for future role-based filtering and kept in the signature so
    callers don't have to change when it becomes relevant.
    """
    del user  # currently unused; see docstring
    proposals_by_category = get_dashboard_proposals(facility)

    # Collect active holds for held proposals and attach them to the cards.
    held_target_ids = set()
    for proposals in proposals_by_category.values():
        for p in proposals:
            if p.status == RetentionProposal.Status.HELD:
                held_target_ids.add(p.target_id)

    holds_by_target = {}
    if held_target_ids:
        active_holds = LegalHold.objects.filter(
            facility=facility,
            target_id__in=held_target_ids,
            dismissed_at__isnull=True,
        )
        for hold in active_holds:
            holds_by_target[hold.target_id] = hold

    today = date.today()
    for proposals in proposals_by_category.values():
        for p in proposals:
            p.active_hold = holds_by_target.get(p.target_id)
            annotate_urgency(p, today=today)

    # Counts — flatten once, reuse the list for all status tallies.
    all_proposals = []
    for proposals in proposals_by_category.values():
        all_proposals.extend(proposals)

    pending_count = sum(1 for p in all_proposals if p.status == RetentionProposal.Status.PENDING)
    held_count = sum(1 for p in all_proposals if p.status == RetentionProposal.Status.HELD)
    approved_count = sum(1 for p in all_proposals if p.status == RetentionProposal.Status.APPROVED)
    deferred_count = sum(1 for p in all_proposals if p.status == RetentionProposal.Status.DEFERRED)
    rejected_count = sum(1 for p in all_proposals if p.status == RetentionProposal.Status.REJECTED)

    # Facility settings — mirror the defaults from the enforce_retention
    # command when no Settings row exists yet.
    try:
        settings_obj = facility.settings
        retention_settings = {
            "anonymous": settings_obj.retention_anonymous_days,
            "identified": settings_obj.retention_identified_days,
            "qualified": settings_obj.retention_qualified_days,
        }
    except facility._meta.model.settings.RelatedObjectDoesNotExist:
        retention_settings = {
            "anonymous": 90,
            "identified": 365,
            "qualified": 3650,
        }

    categories = []
    for cat_key, proposals in proposals_by_category.items():
        categories.append(
            {
                "key": cat_key,
                "label": DASHBOARD_CATEGORY_LABELS.get(cat_key, cat_key),
                "proposals": proposals,
                "count": len(proposals),
            }
        )

    return {
        "categories": categories,
        "pending_count": pending_count,
        "held_count": held_count,
        "approved_count": approved_count,
        "deferred_count": deferred_count,
        "rejected_count": rejected_count,
        "retention_settings": retention_settings,
        "today": today,
        "soon_threshold": today + timedelta(days=7),
    }


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


def build_proposal_details(event):
    """Build a details dict for a retention proposal from an event."""
    details = {
        "document_type": event.document_type.name if event.document_type else None,
        "occurred_at": str(event.occurred_at),
    }
    if event.client:
        details["pseudonym"] = event.client.pseudonym
        details["contact_stage"] = event.client.contact_stage
    return details


def create_proposals_for_facility(facility, settings_obj, now):
    """Create RetentionProposal entries for events that would be deleted under the four strategies.

    Refs #778: nutzt :func:`core.retention.strategies.iter_strategies`, damit
    die Strategie-Definitionen mit ``enforcement.collect_doomed_events`` synchron
    bleiben. ``create_proposal`` ist idempotent ueber ``unique_active_retention_proposal``,
    sodass ein Event, das mehrere Kategorien matcht, genau einen Vorschlag bekommt
    (Cross-Strategy-Deduplikation).

    Returns ``{"count": N}`` — number of newly created proposals.
    """
    held_ids = get_active_hold_target_ids(facility, "Event")
    created_count = 0

    for strategy in iter_strategies(facility, settings_obj, now):
        qs = strategy.queryset.exclude(pk__in=held_ids).select_related("client", "document_type")
        for event in qs.iterator():
            details = build_proposal_details(event)
            _, was_created = create_proposal(
                facility=facility,
                target_type="Event",
                target_id=event.pk,
                deletion_due_at=strategy.cutoff.date(),
                details=details,
                category=strategy.category,
            )
            if was_created:
                created_count += 1

    return {"count": created_count}
