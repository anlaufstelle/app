"""Contact-stage downgrade request from the person record."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.constants import RATELIMIT_MUTATION
from core.forms.legal_holds import ProtectionReasonForm
from core.models import Client
from core.services.client.stage_downgrade import request_stage_downgrade
from core.services.scoping import get_scoped_object
from core.views.mixins import LeadOrAdminRequiredMixin


@method_decorator(ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True), name="post")
class ClientStageDowngradeView(LeadOrAdminRequiredMixin, View):
    @staticmethod
    def _context(person, form):
        return {
            "client": person,
            "form": form,
            "back_link": {"label": _("Zurück zur Akte"), "url": reverse("core:client_detail", args=[person.pk])},
        }

    def get(self, request, pk):
        person = get_scoped_object(Client, request, pk=pk, is_deleted=False)
        return render(request, "core/clients/stage_downgrade.html", self._context(person, ProtectionReasonForm()))

    def post(self, request, pk):
        person = get_scoped_object(Client, request, pk=pk, is_deleted=False)
        form = ProtectionReasonForm(request.POST)
        if not form.is_valid():
            return render(request, "core/clients/stage_downgrade.html", self._context(person, form), status=400)
        try:
            request_stage_downgrade(person, request.user, form.cleaned_data["reason"])
        except ValidationError as exc:
            return HttpResponse(exc.messages[0], status=409)
        messages.success(
            request, _("Herabstufung beantragt. Eine andere Person mit Löschbestätigung muss den Antrag prüfen.")
        )
        return redirect("core:client_detail", pk=person.pk)
