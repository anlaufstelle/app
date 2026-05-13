"""Views for the four-eyes-principle client-deletion workflow (#626).

Aufbau analog ``views/event_deletion.py``:
- Antrag (Fachkraft+): :class:`ClientDeleteRequestView`
- Genehmigung (Leitung+): laeuft ueber :class:`DeletionRequestReviewView`
  in event_deletion.py — die View wurde um Client-Targets erweitert.
- Wiederherstellung (Admin): :class:`ClientRestoreView`
- Papierkorb-Liste (Admin): :class:`ClientTrashView`
"""

import logging

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.constants import RATELIMIT_MUTATION
from core.models import Client
from core.services.clients import request_client_deletion, restore_client
from core.views.mixins import FacilityAdminRequiredMixin, StaffRequiredMixin

logger = logging.getLogger(__name__)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class ClientDeleteRequestView(StaffRequiredMixin, View):
    """Fachkraft stellt Vier-Augen-Loeschantrag fuer eine Person."""

    def get(self, request, pk):
        facility = request.current_facility
        client = get_object_or_404(Client, pk=pk, facility=facility, is_deleted=False)
        return render(
            request,
            "core/clients/delete_request_confirm.html",
            {"client": client},
        )

    def post(self, request, pk):
        facility = request.current_facility
        client = get_object_or_404(Client, pk=pk, facility=facility, is_deleted=False)
        reason = (request.POST.get("reason") or "").strip()
        if not reason:
            messages.error(request, _("Bitte eine Begruendung angeben."))
            return redirect("core:client_delete_request", pk=pk)
        request_client_deletion(client, request.user, reason)
        messages.success(
            request,
            _("Loeschantrag gestellt — Leitung wird benachrichtigt."),
        )
        return redirect("core:client_detail", pk=pk)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class ClientRestoreView(FacilityAdminRequiredMixin, View):
    """Admin stellt eine soft-geloeschte Person aus dem Papierkorb wieder her."""

    def post(self, request, pk):
        facility = request.current_facility
        client = get_object_or_404(Client, pk=pk, facility=facility, is_deleted=True)
        restore_client(client, request.user)
        messages.success(request, _("Person wiederhergestellt."))
        return redirect("core:client_detail", pk=pk)


class ClientTrashView(FacilityAdminRequiredMixin, View):
    """Papierkorb-Liste: alle soft-deleteten Personen einer Einrichtung."""

    def get(self, request):
        facility = request.current_facility
        clients = (
            Client.objects.for_facility(facility)
            .filter(is_deleted=True)
            .select_related("deleted_by")
            .order_by("-deleted_at")
        )
        return render(
            request,
            "core/clients/trash.html",
            {"clients": clients},
        )
