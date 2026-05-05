"""Views for the four-eyes-principle deletion-request workflow.

Aus ``views/events.py`` ausgekoppelt (Refs #598 #603). Der direkte Staff-
Löschpfad (``EventDeleteView``) bleibt in ``events.py``, weil er dort zum
Event-Lifecycle gehört; hier liegt nur der Review-Workflow (Lead/Admin).
"""

import logging

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.constants import RATELIMIT_MUTATION
from core.models import Client, DeletionRequest, Event
from core.services.clients import approve_client_deletion, reject_client_deletion
from core.services.event import approve_deletion, reject_deletion
from core.views.mixins import LeadOrAdminRequiredMixin

logger = logging.getLogger(__name__)


class DeletionRequestListView(LeadOrAdminRequiredMixin, View):
    """List of all deletion requests."""

    def get(self, request):
        facility = request.current_facility
        all_requests = DeletionRequest.objects.for_facility(facility).select_related("requested_by", "reviewed_by")

        # Querysets zu Listen evaluieren, damit das Template |length ohne
        # zusätzliche COUNT-Queries aufrufen kann (Refs #640). Jede Liste
        # ist per status gefiltert und hat typisch < 100 Einträge — keine
        # Memory-Sorge.
        pending = list(all_requests.filter(status=DeletionRequest.Status.PENDING))
        approved = list(all_requests.filter(status=DeletionRequest.Status.APPROVED))
        rejected = list(all_requests.filter(status=DeletionRequest.Status.REJECTED))

        context = {
            "pending_requests": pending,
            "approved_requests": approved,
            "rejected_requests": rejected,
        }
        return render(request, "core/deletion_requests/list.html", context)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class DeletionRequestReviewView(LeadOrAdminRequiredMixin, View):
    """Review a deletion request (four-eyes principle)."""

    def get(self, request, pk):
        dr = get_object_or_404(
            DeletionRequest,
            pk=pk,
            facility=request.current_facility,
            status=DeletionRequest.Status.PENDING,
        )

        if dr.target_type == DeletionRequest.TargetType.CLIENT:
            try:
                client = Client.objects.get(pk=dr.target_id, facility=request.current_facility)
            except Client.DoesNotExist as exc:
                raise Http404 from exc
            return render(
                request,
                "core/clients/deletion_review.html",
                {"deletion_request": dr, "client": client},
            )

        try:
            event = Event.objects.select_related("document_type", "client").get(
                pk=dr.target_id, facility=request.current_facility
            )
        except Event.DoesNotExist as exc:
            raise Http404 from exc

        return render(
            request,
            "core/events/deletion_review.html",
            {"deletion_request": dr, "event": event},
        )

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
        is_client = dr.target_type == DeletionRequest.TargetType.CLIENT

        if action == "approve":
            if is_client:
                approve_client_deletion(dr, request.user)
            else:
                approve_deletion(dr, request.user)
            messages.success(request, _("Löschantrag wurde genehmigt."))
        elif action == "reject":
            if is_client:
                reject_client_deletion(dr, request.user)
            else:
                reject_deletion(dr, request.user)
            messages.info(request, _("Löschantrag wurde abgelehnt."))

        return redirect("core:deletion_request_list" if is_client else "core:zeitstrom")
