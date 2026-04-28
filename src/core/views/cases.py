"""Views for case management — Case-CRUD + Event-Zuordnung.

Episoden liegen in :file:`views/case_episodes.py`, Wirkungsziele und
Meilensteine in :file:`views/case_goals.py` (Refs #605).
"""

import logging
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.forms.cases import CaseForm
from core.models import Case, Event
from core.services.cases import (
    assign_event_to_case,
    close_case,
    create_case,
    remove_event_from_case,
    reopen_case,
    update_case,
)
from core.services.clients import get_client_or_none
from core.services.sensitivity import get_visible_event_or_404
from core.views.mixins import LeadOrAdminRequiredMixin, StaffRequiredMixin

logger = logging.getLogger(__name__)


def _get_case_event_context(case, facility, user):
    """Return events and unassigned events for a case (shared by detail/assign/remove views).

    Both querysets are filtered through ``Event.objects.visible_to(user)`` so
    that lower roles do not see the existence of higher-sensitivity events
    they have no business knowing about (#522).
    """
    events = (
        Event.objects.visible_to(user)
        .filter(case=case, is_deleted=False)
        .select_related("document_type", "created_by")
        .order_by("-occurred_at")
    )
    unassigned_events = []
    if case.client:
        unassigned_events = (
            Event.objects.visible_to(user)
            .filter(
                client=case.client,
                case__isnull=True,
                is_deleted=False,
                facility=facility,
            )
            .select_related("document_type")
            .order_by("-occurred_at")
        )
    return {"events": events, "unassigned_events": unassigned_events}


class CaseListView(StaffRequiredMixin, View):
    """Case list with search, filter by status and pagination."""

    def get(self, request):
        facility = request.current_facility
        qs = Case.objects.for_facility(facility)

        q = request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(title__icontains=q)

        status = request.GET.get("status")
        if status:
            qs = qs.filter(status=status)

        qs = qs.select_related("client", "lead_user").order_by("-created_at")

        paginator = Paginator(qs, 25)
        page = request.GET.get("page")
        cases = paginator.get_page(page)

        pagination_params = urlencode({k: v for k, v in [("q", q), ("status", status)] if v})

        context = {
            "cases": cases,
            "q": q,
            "selected_status": status,
            "status_choices": Case.Status.choices,
            "pagination_params": pagination_params,
        }

        if request.headers.get("HX-Request"):
            return render(request, "core/cases/partials/table.html", context)
        return render(request, "core/cases/list.html", context)


class CaseCreateView(StaffRequiredMixin, View):
    """Create a new case."""

    def get(self, request):
        facility = request.current_facility
        form = CaseForm(facility=facility)

        client_id = request.GET.get("client")
        client_pseudonym = ""
        if client_id:
            form.fields["client"].initial = client_id
            client_obj = get_client_or_none(facility, client_id)
            if client_obj:
                client_pseudonym = client_obj.pseudonym

        context = {
            "form": form,
            "is_edit": False,
            "client_id": client_id or "",
            "client_pseudonym": client_pseudonym,
        }
        return render(request, "core/cases/form.html", context)

    @method_decorator(ratelimit(key="user", rate="60/h", method="POST", block=True))
    def post(self, request):
        facility = request.current_facility
        form = CaseForm(request.POST, facility=facility)
        if form.is_valid():
            client_obj = form.cleaned_data.get("client")
            case = create_case(
                facility=facility,
                user=request.user,
                client=client_obj,
                title=form.cleaned_data["title"],
                description=form.cleaned_data.get("description", ""),
                lead_user=form.cleaned_data.get("lead_user"),
            )
            messages.success(request, _("Fall wurde erstellt."))
            return redirect("core:case_detail", pk=case.pk)
        context = {
            "form": form,
            "is_edit": False,
            "client_id": request.POST.get("client", ""),
            "client_pseudonym": "",
        }
        return render(request, "core/cases/form.html", context)


class CaseDetailView(StaffRequiredMixin, View):
    """Case detail with events list and placeholder sections."""

    def get(self, request, pk):
        facility = request.current_facility
        case = get_object_or_404(
            Case.objects.select_related("client", "lead_user", "created_by"),
            pk=pk,
            facility=facility,
        )

        episodes = case.episodes.all()
        goals = case.goals.prefetch_related("milestones").all()
        context = {
            "case": case,
            "episodes": episodes,
            "goals": goals,
            **_get_case_event_context(case, facility, request.user),
        }
        return render(request, "core/cases/detail.html", context)


class CaseUpdateView(StaffRequiredMixin, View):
    """Edit a case."""

    def get(self, request, pk):
        facility = request.current_facility
        case = get_object_or_404(Case, pk=pk, facility=facility)
        form = CaseForm(instance=case, facility=facility)
        if case.client:
            form.fields["client"].initial = case.client.pk

        context = {
            "form": form,
            "case": case,
            "is_edit": True,
            "client_id": str(case.client.pk) if case.client else "",
            "client_pseudonym": case.client.pseudonym if case.client else "",
        }
        return render(request, "core/cases/form.html", context)

    def post(self, request, pk):
        facility = request.current_facility
        case = get_object_or_404(Case, pk=pk, facility=facility)
        form = CaseForm(request.POST, facility=facility)
        if form.is_valid():
            client_obj = form.cleaned_data.get("client")
            expected_updated_at = request.POST.get("expected_updated_at") or None
            try:
                update_case(
                    case,
                    request.user,
                    expected_updated_at=expected_updated_at,
                    title=form.cleaned_data["title"],
                    description=form.cleaned_data.get("description", ""),
                    lead_user=form.cleaned_data.get("lead_user"),
                    client=client_obj,
                )
            except ValidationError as e:
                messages.error(request, e.message if hasattr(e, "message") else str(e))
                return redirect("core:case_update", pk=case.pk)
            messages.success(request, _("Fall wurde aktualisiert."))
            return redirect("core:case_detail", pk=case.pk)
        context = {
            "form": form,
            "case": case,
            "is_edit": True,
            "client_id": request.POST.get("client", ""),
            "client_pseudonym": "",
        }
        return render(request, "core/cases/form.html", context)


class CaseCloseView(LeadOrAdminRequiredMixin, View):
    """Close a case (Lead or Admin only)."""

    def post(self, request, pk):
        facility = request.current_facility
        case = get_object_or_404(Case, pk=pk, facility=facility)
        close_case(case, request.user)
        messages.success(request, _("Fall wurde geschlossen."))
        return redirect("core:case_detail", pk=case.pk)


class CaseReopenView(LeadOrAdminRequiredMixin, View):
    """Reopen a case (Lead or Admin only)."""

    def post(self, request, pk):
        facility = request.current_facility
        case = get_object_or_404(Case, pk=pk, facility=facility)
        reopen_case(case, request.user)
        messages.success(request, _("Fall wurde wiedereröffnet."))
        return redirect("core:case_detail", pk=case.pk)


class CaseAssignEventView(StaffRequiredMixin, View):
    """Assign an event to a case (HTMX)."""

    def post(self, request, pk):
        facility = request.current_facility
        case = get_object_or_404(Case, pk=pk, facility=facility)
        event_id = request.POST.get("event_id")
        if not event_id:
            messages.error(request, _("Kein Ereignis ausgewählt."))
            return redirect("core:case_detail", pk=case.pk)

        event = get_visible_event_or_404(request.user, facility, event_id)
        assign_event_to_case(case, event, request.user)

        context = {"case": case, **_get_case_event_context(case, facility, request.user)}
        return render(request, "core/cases/partials/event_list.html", context)


class CaseRemoveEventView(StaffRequiredMixin, View):
    """Remove an event from a case (HTMX)."""

    def post(self, request, pk, event_pk):
        facility = request.current_facility
        case = get_object_or_404(Case, pk=pk, facility=facility)
        event = get_visible_event_or_404(request.user, facility, event_pk)
        remove_event_from_case(event, request.user)

        context = {"case": case, **_get_case_event_context(case, facility, request.user)}
        return render(request, "core/cases/partials/event_list.html", context)


class CasesForClientView(StaffRequiredMixin, View):
    """JSON endpoint: returns open cases for a given client UUID."""

    @method_decorator(ratelimit(key="user", rate="30/m", method="GET", block=True))
    def get(self, request):
        client_id = request.GET.get("client")
        if not client_id:
            return JsonResponse([], safe=False)

        facility = request.current_facility
        cases = Case.objects.filter(
            facility=facility,
            client_id=client_id,
            status=Case.Status.OPEN,
        ).values("id", "title")

        data = [{"id": str(c["id"]), "title": c["title"]} for c in cases]
        return JsonResponse(data, safe=False)
