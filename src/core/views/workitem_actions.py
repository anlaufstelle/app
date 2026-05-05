"""Einzel-Aktionen für WorkItems — Create, Update, StatusUpdate (Refs #605).

Abgeteilt von :file:`views/workitems.py`. Die Inbox- und Detail-Views
bleiben dort, weil sie reine Read-Pfade sind.
"""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.constants import RATELIMIT_FREQUENT, RATELIMIT_MUTATION
from core.forms.workitems import WorkItemForm
from core.models import WorkItem
from core.services.clients import get_client_or_none
from core.services.workitems import (
    create_workitem,
    update_workitem,
    update_workitem_status,
)
from core.views.mixins import AssistantOrAboveRequiredMixin, StaffRequiredMixin
from core.views.workitems import can_user_mutate_workitem


class WorkItemStatusUpdateView(AssistantOrAboveRequiredMixin, View):
    """HTMX: update WorkItem status."""

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_FREQUENT, method="POST", block=True))
    def post(self, request, pk):
        new_status = request.POST.get("status")
        valid_statuses = [s.value for s in WorkItem.Status]
        if new_status not in valid_statuses:
            return HttpResponseBadRequest(_("Ungültiger Status"))

        # Permission-Check + Service-Call innerhalb derselben Transaktion,
        # damit das gelockte Objekt aus dem Service-Layer zurueckgegeben
        # wird und kein parallel laufender Request den Stand zwischen
        # Check und Update veraendern kann (Refs #129 Teil A, Refs #733).
        with transaction.atomic():
            workitem = get_object_or_404(
                WorkItem.objects.select_for_update(),
                pk=pk,
                facility=request.current_facility,
            )
            if not can_user_mutate_workitem(request.user, workitem):
                return HttpResponseForbidden(_("Keine Berechtigung für diese Aufgabe."))

            workitem = update_workitem_status(workitem, new_status, request.user)

        if request.htmx:
            if request.POST.get("hide"):
                return HttpResponse("")
            return render(request, "core/workitems/partials/item_card.html", {"wi": workitem})

        messages.success(request, _("Status aktualisiert."))
        next_url = request.POST.get("next")
        if next_url and next_url.startswith("/"):
            return redirect(next_url)
        return redirect("core:workitem_inbox")


class WorkItemCreateView(StaffRequiredMixin, View):
    """Create a WorkItem."""

    def get(self, request):
        facility = request.current_facility
        form = WorkItemForm(facility=facility)

        client_id = request.GET.get("client")
        client_pseudonym = ""
        if client_id:
            client = get_client_or_none(facility, client_id)
            if client:
                client_pseudonym = client.pseudonym
            else:
                client_id = ""

        context = {
            "form": form,
            "client_id": client_id or "",
            "client_pseudonym": client_pseudonym,
        }
        return render(request, "core/workitems/form.html", context)

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True))
    def post(self, request):
        facility = request.current_facility
        form = WorkItemForm(request.POST, facility=facility)

        if form.is_valid():
            create_workitem(
                facility=facility,
                user=request.user,
                client=form.cleaned_data.get("client"),
                item_type=form.cleaned_data["item_type"],
                title=form.cleaned_data["title"],
                description=form.cleaned_data.get("description", ""),
                priority=form.cleaned_data["priority"],
                due_date=form.cleaned_data.get("due_date"),
                remind_at=form.cleaned_data.get("remind_at"),
                recurrence=form.cleaned_data.get("recurrence") or WorkItem.Recurrence.NONE,
                assigned_to=form.cleaned_data.get("assigned_to"),
            )
            messages.success(request, _("Aufgabe wurde erstellt."))
            return redirect("core:workitem_inbox")

        context = {
            "form": form,
            "client_id": request.POST.get("client", ""),
            "client_pseudonym": "",
        }
        return render(request, "core/workitems/form.html", context)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class WorkItemUpdateView(StaffRequiredMixin, View):
    """Edit a WorkItem."""

    def get(self, request, pk):
        workitem = get_object_or_404(
            WorkItem.objects.select_related("client"),
            pk=pk,
            facility=request.current_facility,
        )
        # Refs #735: Edit-Pfad richtet sich nach derselben Owner/Assignee-
        # Policy wie der Status-Pfad. Staff-User sehen das Form nur fuer
        # eigene oder zugewiesene WorkItems; Lead/Admin uneingeschraenkt
        # innerhalb ihrer Facility.
        if not can_user_mutate_workitem(request.user, workitem):
            return HttpResponseForbidden(_("Keine Berechtigung für diese Aufgabe."))
        form = WorkItemForm(instance=workitem, facility=request.current_facility)
        context = {
            "form": form,
            "workitem": workitem,
            "client_id": str(workitem.client.pk) if workitem.client else "",
            "client_pseudonym": workitem.client.pseudonym if workitem.client else "",
        }
        return render(request, "core/workitems/form.html", context)

    def post(self, request, pk):
        workitem = get_object_or_404(
            WorkItem,
            pk=pk,
            facility=request.current_facility,
        )
        if not can_user_mutate_workitem(request.user, workitem):
            return HttpResponseForbidden(_("Keine Berechtigung für diese Aufgabe."))
        form = WorkItemForm(request.POST, instance=workitem, facility=request.current_facility)

        if form.is_valid():
            expected_updated_at = request.POST.get("expected_updated_at") or None
            try:
                update_workitem(
                    workitem,
                    request.user,
                    expected_updated_at=expected_updated_at,
                    client=form.cleaned_data.get("client"),
                    item_type=form.cleaned_data["item_type"],
                    title=form.cleaned_data["title"],
                    description=form.cleaned_data.get("description", ""),
                    priority=form.cleaned_data["priority"],
                    due_date=form.cleaned_data.get("due_date"),
                    remind_at=form.cleaned_data.get("remind_at"),
                    recurrence=form.cleaned_data.get("recurrence") or WorkItem.Recurrence.NONE,
                    assigned_to=form.cleaned_data.get("assigned_to"),
                )
            except ValidationError as e:
                messages.error(request, e.message if hasattr(e, "message") else str(e))
                return redirect("core:workitem_update", pk=workitem.pk)
            messages.success(request, _("Aufgabe wurde aktualisiert."))
            return redirect("core:workitem_inbox")

        context = {
            "form": form,
            "workitem": workitem,
            "client_id": request.POST.get("client", ""),
            "client_pseudonym": "",
        }
        return render(request, "core/workitems/form.html", context)
