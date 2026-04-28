"""Views for the Retention Dashboard (GDPR retention management)."""

from datetime import date, timedelta

from django.http import HttpResponseBadRequest, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext as _
from django.views import View

from core.models import LegalHold, RetentionProposal
from core.services.retention import (
    approve_proposal,
    create_legal_hold,
    dismiss_legal_hold,
    get_dashboard_proposals,
)
from core.views.mixins import LeadOrAdminRequiredMixin

CATEGORY_LABELS = {
    "anonymous": _("Anonym"),
    "identified": _("Identifiziert"),
    "qualified": _("Qualifiziert"),
    "document_type": _("Dokumenttyp"),
}


class RetentionDashboardView(LeadOrAdminRequiredMixin, View):
    """Retention dashboard showing proposals grouped by category."""

    def get(self, request):
        facility = request.current_facility
        proposals_by_category = get_dashboard_proposals(facility)

        # Collect active holds for held proposals
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

        # Attach holds to proposals and compute urgency
        today = date.today()
        for proposals in proposals_by_category.values():
            for p in proposals:
                p.active_hold = holds_by_target.get(p.target_id)
                days_until = (p.deletion_due_at - today).days
                if days_until <= 7:
                    p.urgency = "red"
                elif days_until <= 30:
                    p.urgency = "yellow"
                else:
                    p.urgency = "gray"

        # Counts
        all_proposals = []
        for proposals in proposals_by_category.values():
            all_proposals.extend(proposals)

        pending_count = sum(1 for p in all_proposals if p.status == RetentionProposal.Status.PENDING)
        held_count = sum(1 for p in all_proposals if p.status == RetentionProposal.Status.HELD)
        approved_count = sum(1 for p in all_proposals if p.status == RetentionProposal.Status.APPROVED)

        # Facility settings
        try:
            settings = facility.settings
            retention_settings = {
                "anonymous": settings.retention_anonymous_days,
                "identified": settings.retention_identified_days,
                "qualified": settings.retention_qualified_days,
            }
        except facility._meta.model.settings.RelatedObjectDoesNotExist:
            retention_settings = {
                "anonymous": 90,
                "identified": 365,
                "qualified": 3650,
            }

        # Build category list with labels
        categories = []
        for cat_key, proposals in proposals_by_category.items():
            categories.append(
                {
                    "key": cat_key,
                    "label": CATEGORY_LABELS.get(cat_key, cat_key),
                    "proposals": proposals,
                    "count": len(proposals),
                }
            )

        context = {
            "categories": categories,
            "pending_count": pending_count,
            "held_count": held_count,
            "approved_count": approved_count,
            "retention_settings": retention_settings,
            "today": today,
            "soon_threshold": today + timedelta(days=7),
        }

        if request.headers.get("HX-Request"):
            return render(request, "core/retention/partials/dashboard_content.html", context)
        return render(request, "core/retention/dashboard.html", context)


class RetentionApproveView(LeadOrAdminRequiredMixin, View):
    """Approve a retention proposal for deletion."""

    def post(self, request, pk):
        proposal = get_object_or_404(
            RetentionProposal,
            pk=pk,
            facility=request.current_facility,
        )
        approve_proposal(proposal, request.user)

        today = date.today()
        days_until = (proposal.deletion_due_at - today).days
        if days_until <= 7:
            proposal.urgency = "red"
        elif days_until <= 30:
            proposal.urgency = "yellow"
        else:
            proposal.urgency = "gray"
        proposal.active_hold = None

        return render(request, "core/retention/partials/proposal_card.html", {"proposal": proposal})

    def get(self, request, pk):
        return HttpResponseNotAllowed(["POST"])


class RetentionHoldView(LeadOrAdminRequiredMixin, View):
    """Create a legal hold on a retention proposal."""

    def post(self, request, pk):
        proposal = get_object_or_404(
            RetentionProposal,
            pk=pk,
            facility=request.current_facility,
        )

        reason = request.POST.get("reason", "").strip()
        if not reason:
            return HttpResponseBadRequest(_("Begründung ist erforderlich."))

        expires_at_str = request.POST.get("expires_at", "").strip()
        expires_at = None
        if expires_at_str:
            try:
                expires_at = date.fromisoformat(expires_at_str)
            except ValueError:
                return HttpResponseBadRequest(_("Ungültiges Datum."))

        hold = create_legal_hold(proposal, request.user, reason, expires_at)

        # Refresh proposal from DB
        proposal.refresh_from_db()

        today = date.today()
        days_until = (proposal.deletion_due_at - today).days
        if days_until <= 7:
            proposal.urgency = "red"
        elif days_until <= 30:
            proposal.urgency = "yellow"
        else:
            proposal.urgency = "gray"
        proposal.active_hold = hold

        return render(request, "core/retention/partials/proposal_card.html", {"proposal": proposal})

    def get(self, request, pk):
        return HttpResponseNotAllowed(["POST"])


class RetentionDismissHoldView(LeadOrAdminRequiredMixin, View):
    """Dismiss an active legal hold."""

    def post(self, request, pk):
        hold = get_object_or_404(
            LegalHold,
            pk=pk,
            facility=request.current_facility,
        )

        dismiss_legal_hold(hold, request.user)

        # Find the associated proposal
        proposal = RetentionProposal.objects.filter(
            facility=request.current_facility,
            target_type=hold.target_type,
            target_id=hold.target_id,
        ).first()

        if proposal:
            proposal.refresh_from_db()
            today = date.today()
            days_until = (proposal.deletion_due_at - today).days
            if days_until <= 7:
                proposal.urgency = "red"
            elif days_until <= 30:
                proposal.urgency = "yellow"
            else:
                proposal.urgency = "gray"
            proposal.active_hold = None

        return render(request, "core/retention/partials/proposal_card.html", {"proposal": proposal})

    def get(self, request, pk):
        return HttpResponseNotAllowed(["POST"])
