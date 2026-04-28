"""Views for the four-eyes-principle deletion-request workflow.

Aus ``views/events.py`` ausgekoppelt (Refs #598 #603). Der direkte Staff-
Löschpfad (``EventDeleteView``) bleibt in ``events.py``, weil er dort zum
Event-Lifecycle gehört; hier liegt nur der Review-Workflow (Lead/Admin).
"""

import logging

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views import View

from core.models import DeletionRequest, Event
from core.services.event import approve_deletion, reject_deletion
from core.views.mixins import LeadOrAdminRequiredMixin

logger = logging.getLogger(__name__)


class DeletionRequestListView(LeadOrAdminRequiredMixin, View):
    """List of all deletion requests."""

    def get(self, request):
        facility = request.current_facility
        all_requests = DeletionRequest.objects.for_facility(facility).select_related("requested_by", "reviewed_by")

        pending = all_requests.filter(status=DeletionRequest.Status.PENDING)
        approved = all_requests.filter(status=DeletionRequest.Status.APPROVED)
        rejected = all_requests.filter(status=DeletionRequest.Status.REJECTED)

        context = {
            "pending_requests": pending,
            "approved_requests": approved,
            "rejected_requests": rejected,
        }
        return render(request, "core/deletion_requests/list.html", context)


class DeletionRequestReviewView(LeadOrAdminRequiredMixin, View):
    """Review a deletion request (four-eyes principle)."""

    def get(self, request, pk):
        dr = get_object_or_404(
            DeletionRequest,
            pk=pk,
            facility=request.current_facility,
            status=DeletionRequest.Status.PENDING,
        )

        try:
            event = Event.objects.select_related("document_type", "client").get(
                pk=dr.target_id, facility=request.current_facility
            )
        except Event.DoesNotExist:
            raise Http404

        context = {
            "deletion_request": dr,
            "event": event,
        }
        return render(request, "core/events/deletion_review.html", context)

    def post(self, request, pk):
        dr = get_object_or_404(
            DeletionRequest,
            pk=pk,
            facility=request.current_facility,
            status=DeletionRequest.Status.PENDING,
        )

        # Reviewer must not be the requester
        if dr.requested_by == request.user:
            messages.error(request, _("Sie können Ihren eigenen Löschantrag nicht genehmigen."))
            return redirect("core:deletion_review", pk=pk)

        action = request.POST.get("action")
        if action == "approve":
            approve_deletion(dr, request.user)
            messages.success(request, _("Löschantrag wurde genehmigt."))
        elif action == "reject":
            reject_deletion(dr, request.user)
            messages.info(request, _("Löschantrag wurde abgelehnt."))

        return redirect("core:zeitstrom")
