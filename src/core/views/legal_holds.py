"""Facility-scoped legal holds from person, case and event records."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View

from core.constants import RATELIMIT_MUTATION
from core.forms.legal_holds import LegalHoldForm
from core.models import Client, LegalHold
from core.retention.legal_holds import create_hold_for_target, dismiss_legal_hold
from core.services.case import cases_with_live_client
from core.services.compliance import get_visible_event_or_404
from core.services.scoping import get_scoped_object
from core.utils.ratelimit import ratelimit
from core.views.mixins import LeadOrAdminRequiredMixin


@method_decorator(ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True), name="post")
class RecordLegalHoldView(LeadOrAdminRequiredMixin, View):
    """Show, set and dismiss protection independently of retention proposals."""

    target_type = "Client"
    dismiss = False

    def _target(self, request, pk):
        if self.target_type == "Event":
            return get_visible_event_or_404(request.user, request.current_facility, pk)
        model = Client if self.target_type == "Client" else cases_with_live_client()
        owner_filters = {"is_active": True} if self.target_type == "Client" else {}
        return get_scoped_object(model, request, pk=pk, is_deleted=False, **owner_filters)

    def _context(self, request, target, form):
        prefix = {"Client": "client", "Case": "case", "Event": "event"}[self.target_type]
        holds = LegalHold.objects.for_facility(request.current_facility).filter(
            target_type=self.target_type,
            target_id=target.pk,
        )
        return {
            "form": form,
            "target": target,
            "holds": holds,
            "back_url": reverse(f"core:{prefix}_detail", args=[target.pk]),
            "back_link": {"label": _("Zurück zur Akte"), "url": reverse(f"core:{prefix}_detail", args=[target.pk])},
            "dismiss_url_name": f"core:{prefix}_legal_hold_dismiss",
        }

    def get(self, request, pk, hold_pk=None):
        if self.dismiss:
            return self.http_method_not_allowed(request, pk=pk)
        target = self._target(request, pk)
        return render(request, "core/retention/record_hold.html", self._context(request, target, LegalHoldForm()))

    def post(self, request, pk, hold_pk=None):
        target = self._target(request, pk)
        if self.dismiss:
            hold = get_scoped_object(
                LegalHold,
                request,
                pk=hold_pk,
                target_type=self.target_type,
                target_id=target.pk,
            )
            try:
                dismiss_legal_hold(hold, request.user)
            except ValidationError as exc:
                return HttpResponse(exc.messages[0], status=409)
            messages.success(request, _("Legal Hold wurde aufgehoben."))
        else:
            form = LegalHoldForm(request.POST)
            if not form.is_valid():
                return render(
                    request, "core/retention/record_hold.html", self._context(request, target, form), status=400
                )
            try:
                hold = create_hold_for_target(
                    request.current_facility,
                    target_type=self.target_type,
                    target_id=target.pk,
                    user=request.user,
                    **form.cleaned_data,
                )
            except IntegrityError as exc:
                if "unique_active_legal_hold" not in str(exc):
                    raise
                return HttpResponse(
                    _("Für diesen Datensatz besteht bereits ein nicht aufgehobener Legal Hold."), status=409
                )
            except ValidationError as exc:
                return HttpResponse(exc.messages[0], status=409)
            messages.success(request, _("Legal Hold wurde gesetzt."))
            if hold.history_already_redacted:
                messages.warning(
                    request, _("Die Historie wurde bereits teilweise redigiert. Der Hold stellt sie nicht wieder her.")
                )
        return redirect(self._context(request, target, LegalHoldForm())["back_url"])
