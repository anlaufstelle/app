"""Views for the Retention Dashboard (GDPR retention management)."""

from datetime import date

from django.http import HttpResponseBadRequest, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views import View

from core.models import LegalHold, RetentionProposal
from core.services.retention import (
    annotate_urgency,
    approve_proposal,
    build_retention_dashboard_context,
    bulk_approve_proposals,
    bulk_defer_proposals,
    bulk_reject_proposals,
    create_legal_hold,
    dismiss_legal_hold,
)
from core.views.mixins import LeadOrAdminRequiredMixin


class RetentionDashboardView(LeadOrAdminRequiredMixin, View):
    """Retention dashboard showing proposals grouped by category."""

    def get(self, request):
        context = build_retention_dashboard_context(request.current_facility, request.user)
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

        annotate_urgency(proposal)
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

        annotate_urgency(proposal)
        proposal.active_hold = hold

        return render(request, "core/retention/partials/proposal_card.html", {"proposal": proposal})

    def get(self, request, pk):
        return HttpResponseNotAllowed(["POST"])


class _BulkActionMixin(LeadOrAdminRequiredMixin):
    """Shared helper for bulk actions on retention proposals.

    Subclasses must define:
    - action_fn: callable(proposals, user, **kwargs) -> int
    - allowed_statuses: iterable of RetentionProposal.Status values
    """

    action_fn = None
    allowed_statuses = (
        RetentionProposal.Status.PENDING,
        RetentionProposal.Status.DEFERRED,
    )
    extra_kwargs_fn = None  # Optional: callable(request) -> dict

    def _get_proposal_ids(self, request):
        ids = request.POST.getlist("proposal_ids") or request.POST.getlist("proposal_ids[]")
        return [i for i in ids if i]

    def _load_proposals(self, request, ids):
        return list(
            RetentionProposal.objects.filter(
                pk__in=ids,
                facility=request.current_facility,
                status__in=self.allowed_statuses,
            )
        )

    def post(self, request):
        ids = self._get_proposal_ids(request)
        if not ids:
            return HttpResponseBadRequest(_("Keine Vorschläge ausgewählt."))

        proposals = self._load_proposals(request, ids)
        if not proposals:
            return HttpResponseBadRequest(_("Keine gültigen Vorschläge gefunden."))

        extra_kwargs = self.extra_kwargs_fn(request) if self.extra_kwargs_fn else {}
        count = type(self).action_fn(proposals, request.user, **extra_kwargs)

        if request.headers.get("HX-Request"):
            response = redirect("core:retention_dashboard")
            response["HX-Redirect"] = response["Location"]
            return response

        if request.headers.get("Accept", "").startswith("application/json"):
            return JsonResponse({"processed": count})

        return redirect("core:retention_dashboard")

    def get(self, request):
        return HttpResponseNotAllowed(["POST"])


class RetentionBulkApproveView(_BulkActionMixin, View):
    """Bulk-approve retention proposals."""

    action_fn = staticmethod(bulk_approve_proposals)
    allowed_statuses = (
        RetentionProposal.Status.PENDING,
        RetentionProposal.Status.DEFERRED,
    )


class RetentionBulkDeferView(_BulkActionMixin, View):
    """Bulk-defer retention proposals (default 30 days)."""

    action_fn = staticmethod(bulk_defer_proposals)
    allowed_statuses = (
        RetentionProposal.Status.PENDING,
        RetentionProposal.Status.DEFERRED,
    )

    @staticmethod
    def extra_kwargs_fn(request):
        days_raw = request.POST.get("days", "30").strip()
        try:
            days = int(days_raw)
        except (TypeError, ValueError):
            days = 30
        if days <= 0:
            days = 30
        return {"days": days}


class RetentionBulkRejectView(_BulkActionMixin, View):
    """Bulk-reject retention proposals."""

    action_fn = staticmethod(bulk_reject_proposals)
    allowed_statuses = (
        RetentionProposal.Status.PENDING,
        RetentionProposal.Status.DEFERRED,
    )


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
            annotate_urgency(proposal)
            proposal.active_hold = None

        return render(request, "core/retention/partials/proposal_card.html", {"proposal": proposal})

    def get(self, request, pk):
        return HttpResponseNotAllowed(["POST"])
