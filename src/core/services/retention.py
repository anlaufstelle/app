"""Service layer for retention proposals and legal holds."""

from datetime import date, timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.db.utils import IntegrityError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.constants import RETENTION_URGENCY_RED_DAYS, RETENTION_URGENCY_YELLOW_DAYS
from core.models import AuditLog, LegalHold, RetentionProposal

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


# ---------------------------------------------------------------------------
# Retention enforcement (extracted from ``enforce_retention`` management command
# for FND-A005 — pure logic, no stdout I/O).
#
# Convention: All ``enforce_*`` functions return a result dict, typically with
# ``{"count": N}``. The command layer formats/prints; the service just acts.
# ---------------------------------------------------------------------------


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


def collect_doomed_events(facility, settings_obj, now):
    """Build queryset of events that will be soft-deleted by the four strategies.

    IMPORTANT: Keep in sync with ``enforce_anonymous``, ``enforce_identified``,
    ``enforce_qualified``, and ``enforce_document_type_retention``.
    """
    from core.models import Case, Client, DocumentType, Event

    held_ids = get_active_hold_target_ids(facility, "Event")
    combined = Event.objects.none()

    # Strategy 1: Anonymous
    cutoff_anon = now - timedelta(days=settings_obj.retention_anonymous_days)
    combined = combined | Event.objects.filter(
        facility=facility,
        is_anonymous=True,
        is_deleted=False,
        occurred_at__lt=cutoff_anon,
    )

    # Strategy 2: Identified
    cutoff_ident = now - timedelta(days=settings_obj.retention_identified_days)
    identified_clients = Client.objects.filter(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
    )
    combined = combined | Event.objects.filter(
        facility=facility,
        client__in=identified_clients,
        is_deleted=False,
        occurred_at__lt=cutoff_ident,
    )

    # Strategy 3: Qualified
    case_cutoff = now - timedelta(days=settings_obj.retention_qualified_days)
    qualified_clients = Client.objects.filter(
        facility=facility,
        contact_stage=Client.ContactStage.QUALIFIED,
    )
    expired_cases = Case.objects.filter(
        facility=facility,
        client__in=qualified_clients,
        status=Case.Status.CLOSED,
        closed_at__lt=case_cutoff,
    )
    combined = combined | Event.objects.filter(
        facility=facility,
        client__in=qualified_clients,
        case__in=expired_cases,
        is_deleted=False,
    )

    # Strategy 4: DocumentType-specific
    doc_types_with_retention = DocumentType.objects.filter(
        facility=facility,
        retention_days__isnull=False,
    )
    for dt in doc_types_with_retention:
        cutoff_dt = now - timedelta(days=dt.retention_days)
        combined = combined | Event.objects.filter(
            facility=facility,
            document_type=dt,
            is_deleted=False,
            occurred_at__lt=cutoff_dt,
        )

    return combined.exclude(pk__in=held_ids).distinct()


def _soft_delete_events(qs, facility, category, retention_days, extra_detail=None):
    """Soft-delete every event in ``qs``, write ``EventHistory`` + ``AuditLog``,
    and clean up approved proposals. Returns ``(count, deleted_ids)``.

    Callers must pre-compute ``qs.count()`` before calling and skip if zero.
    Keeps the identical behavior of the original private ``_enforce_*`` helpers.
    """
    from core.models import EventHistory
    from core.services.file_vault import delete_event_attachments

    deleted_event_ids = list(qs.values_list("pk", flat=True))
    history_entries = []
    for event in qs.iterator():
        data_before = event.data_json.copy() if event.data_json else {}
        event.is_deleted = True
        event.data_json = {}
        delete_event_attachments(event)
        event.save(update_fields=["is_deleted", "data_json", "updated_at"])
        history_entries.append(
            EventHistory(
                event=event,
                changed_by=None,
                action=EventHistory.Action.DELETE,
                data_before=data_before,
            )
        )
    EventHistory.objects.bulk_create(history_entries)

    detail = {
        "command": "enforce_retention",
        "category": category,
        "count": len(deleted_event_ids),
        "retention_days": retention_days,
    }
    if extra_detail:
        detail.update(extra_detail)
    AuditLog.objects.create(
        facility=facility,
        action=AuditLog.Action.DELETE,
        target_type="Event",
        detail=detail,
    )
    # Cleanup approved proposals for deleted events
    RetentionProposal.objects.filter(
        facility=facility,
        target_type="Event",
        target_id__in=deleted_event_ids,
        status=RetentionProposal.Status.APPROVED,
    ).delete()
    return len(deleted_event_ids), deleted_event_ids


def enforce_anonymous(facility, settings_obj, now, dry_run):
    """Soft-delete anonymous events older than ``retention_anonymous_days``.

    Returns ``{"count": N}``.
    """
    from core.models import Event

    cutoff = now - timedelta(days=settings_obj.retention_anonymous_days)
    held_ids = get_active_hold_target_ids(facility, "Event")
    qs = Event.objects.filter(
        facility=facility,
        is_anonymous=True,
        is_deleted=False,
        occurred_at__lt=cutoff,
    ).exclude(pk__in=held_ids)
    count = qs.count()
    if count and not dry_run:
        _soft_delete_events(
            qs,
            facility=facility,
            category="anonymous",
            retention_days=settings_obj.retention_anonymous_days,
        )
    return {"count": count}


def enforce_identified(facility, settings_obj, now, dry_run):
    """Soft-delete events from IDENTIFIED clients older than ``retention_identified_days``.

    Returns ``{"count": N}``.
    """
    from core.models import Client, Event

    cutoff = now - timedelta(days=settings_obj.retention_identified_days)
    held_ids = get_active_hold_target_ids(facility, "Event")
    identified_clients = Client.objects.filter(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
    )
    qs = Event.objects.filter(
        facility=facility,
        client__in=identified_clients,
        is_deleted=False,
        occurred_at__lt=cutoff,
    ).exclude(pk__in=held_ids)
    count = qs.count()
    if count and not dry_run:
        _soft_delete_events(
            qs,
            facility=facility,
            category="identified",
            retention_days=settings_obj.retention_identified_days,
        )
    return {"count": count}


def enforce_qualified(facility, settings_obj, now, dry_run):
    """Soft-delete events from QUALIFIED clients whose linked closed case has exceeded retention.

    Returns ``{"count": N}``.
    """
    from core.models import Case, Client, Event

    held_ids = get_active_hold_target_ids(facility, "Event")
    qualified_clients = Client.objects.filter(
        facility=facility,
        contact_stage=Client.ContactStage.QUALIFIED,
    )

    case_cutoff = now - timedelta(days=settings_obj.retention_qualified_days)
    expired_cases = Case.objects.filter(
        facility=facility,
        client__in=qualified_clients,
        status=Case.Status.CLOSED,
        closed_at__lt=case_cutoff,
    )

    qs = Event.objects.filter(
        facility=facility,
        client__in=qualified_clients,
        case__in=expired_cases,
        is_deleted=False,
    ).exclude(pk__in=held_ids)
    count = qs.count()
    if count and not dry_run:
        _soft_delete_events(
            qs,
            facility=facility,
            category="qualified",
            retention_days=settings_obj.retention_qualified_days,
        )
    return {"count": count}


def enforce_document_type_retention(facility, now, dry_run):
    """Soft-delete events whose DocumentType has a custom ``retention_days`` that has been exceeded.

    Returns ``{"count": N}``.
    """
    from core.models import DocumentType, Event

    held_ids = get_active_hold_target_ids(facility, "Event")
    doc_types_with_retention = DocumentType.objects.filter(
        facility=facility,
        retention_days__isnull=False,
    )

    count = 0
    for dt in doc_types_with_retention:
        cutoff = now - timedelta(days=dt.retention_days)
        qs = Event.objects.filter(
            facility=facility,
            document_type=dt,
            is_deleted=False,
            occurred_at__lt=cutoff,
        ).exclude(pk__in=held_ids)
        dt_count = qs.count()
        if dt_count and not dry_run:
            _soft_delete_events(
                qs,
                facility=facility,
                category="document_type",
                retention_days=dt.retention_days,
                extra_detail={"document_type": dt.name},
            )
        count += dt_count

    return {"count": count}


def enforce_activities(facility, settings_obj, now, dry_run):
    """Hard-delete activities older than ``retention_activities_days``.

    Returns ``{"count": N}``.
    """
    from core.models import Activity

    cutoff = now - timedelta(days=settings_obj.retention_activities_days)
    qs = Activity.objects.filter(
        facility=facility,
        occurred_at__lt=cutoff,
    )
    count = qs.count()
    if count and not dry_run:
        qs.delete()
        AuditLog.objects.create(
            facility=facility,
            action=AuditLog.Action.DELETE,
            target_type="Activity",
            detail={
                "command": "enforce_retention",
                "category": "activities",
                "count": count,
                "retention_days": settings_obj.retention_activities_days,
            },
        )
    return {"count": count}


def anonymize_clients(facility, dry_run):
    """Anonymize clients whose events have all been soft-deleted.

    A client is anonymized when they have at least one event and all of them have
    ``is_deleted=True``. Already anonymized clients (pseudonym starts with
    ``Gelöscht-`` or ``k_anonymized=True``) are skipped.

    Returns ``{"count": N}``.
    """
    from core.models import Client

    candidates = (
        Client.objects.filter(facility=facility)
        .exclude(Q(pseudonym__startswith="Gelöscht-") | Q(k_anonymized=True))
        .annotate(
            total_events=Count("events"),
            active_events=Count("events", filter=Q(events__is_deleted=False)),
        )
        .filter(total_events__gt=0, active_events=0)
    )

    count = candidates.count()
    if count and not dry_run:
        for client in candidates.iterator():
            client.anonymize()
        AuditLog.objects.create(
            facility=facility,
            action=AuditLog.Action.DELETE,
            target_type="Client",
            detail={
                "command": "enforce_retention",
                "category": "client_anonymized",
                "count": count,
            },
        )
    return {"count": count}


def process_facility_retention(facility, settings_obj, now, dry_run):
    """Run all four event-soft-delete strategies for a single facility.

    Returns ``{"count": N}`` — total events affected.
    """
    count = 0
    count += enforce_anonymous(facility, settings_obj, now, dry_run)["count"]
    count += enforce_identified(facility, settings_obj, now, dry_run)["count"]
    count += enforce_qualified(facility, settings_obj, now, dry_run)["count"]
    count += enforce_document_type_retention(facility, now, dry_run)["count"]
    return {"count": count}


def create_proposals_for_facility(facility, settings_obj, now):
    """Create RetentionProposal entries for events that would be deleted under the four strategies.

    Returns ``{"count": N}`` — number of newly created proposals.
    """
    from core.models import Case, Client, DocumentType, Event

    held_ids = get_active_hold_target_ids(facility, "Event")
    created_count = 0

    # Strategy 1: Anonymous events
    cutoff_anon = now - timedelta(days=settings_obj.retention_anonymous_days)
    anon_qs = Event.objects.filter(
        facility=facility,
        is_anonymous=True,
        is_deleted=False,
        occurred_at__lt=cutoff_anon,
    ).exclude(pk__in=held_ids)
    for event in anon_qs.select_related("client", "document_type").iterator():
        details = build_proposal_details(event)
        _, was_created = create_proposal(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            deletion_due_at=cutoff_anon.date(),
            details=details,
            category="anonymous",
        )
        if was_created:
            created_count += 1

    # Strategy 2: Identified events
    cutoff_ident = now - timedelta(days=settings_obj.retention_identified_days)
    identified_clients = Client.objects.filter(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
    )
    ident_qs = Event.objects.filter(
        facility=facility,
        client__in=identified_clients,
        is_deleted=False,
        occurred_at__lt=cutoff_ident,
    ).exclude(pk__in=held_ids)
    for event in ident_qs.select_related("client", "document_type").iterator():
        details = build_proposal_details(event)
        _, was_created = create_proposal(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            deletion_due_at=cutoff_ident.date(),
            details=details,
            category="identified",
        )
        if was_created:
            created_count += 1

    # Strategy 3: Qualified events with closed case
    case_cutoff = now - timedelta(days=settings_obj.retention_qualified_days)
    qualified_clients = Client.objects.filter(
        facility=facility,
        contact_stage=Client.ContactStage.QUALIFIED,
    )
    expired_cases = Case.objects.filter(
        facility=facility,
        client__in=qualified_clients,
        status=Case.Status.CLOSED,
        closed_at__lt=case_cutoff,
    )
    qual_qs = Event.objects.filter(
        facility=facility,
        client__in=qualified_clients,
        case__in=expired_cases,
        is_deleted=False,
    ).exclude(pk__in=held_ids)
    for event in qual_qs.select_related("client", "document_type").iterator():
        details = build_proposal_details(event)
        _, was_created = create_proposal(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            deletion_due_at=case_cutoff.date(),
            details=details,
            category="qualified",
        )
        if was_created:
            created_count += 1

    # Strategy 4: DocumentType-specific retention
    doc_types_with_retention = DocumentType.objects.filter(
        facility=facility,
        retention_days__isnull=False,
    )
    for dt in doc_types_with_retention:
        cutoff_dt = now - timedelta(days=dt.retention_days)
        dt_qs = Event.objects.filter(
            facility=facility,
            document_type=dt,
            is_deleted=False,
            occurred_at__lt=cutoff_dt,
        ).exclude(pk__in=held_ids)
        for event in dt_qs.select_related("client", "document_type").iterator():
            details = build_proposal_details(event)
            _, was_created = create_proposal(
                facility=facility,
                target_type="Event",
                target_id=event.pk,
                deletion_due_at=cutoff_dt.date(),
                details=details,
                category="document_type",
            )
            if was_created:
                created_count += 1

    return {"count": created_count}
