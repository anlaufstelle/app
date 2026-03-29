"""Views for client management."""

import json
import logging
from urllib.parse import urlencode

from django.contrib import messages
from django.db.models import Case, F, IntegerField, Max, Q, Value, When
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.forms.clients import ClientForm
from core.models import Activity, AuditLog, Client, Event, WorkItem
from core.models import Case as CaseModel
from core.services.activity import log_activity
from core.services.bans import get_active_bans_for_client
from core.services.client_export import export_client_data, export_client_data_pdf
from core.services.clients import create_client, track_client_visit, update_client_stage
from core.signals.audit import get_client_ip
from core.views.mixins import AssistantOrAboveRequiredMixin, LeadOrAdminRequiredMixin, StaffRequiredMixin

logger = logging.getLogger(__name__)


class ClientListView(AssistantOrAboveRequiredMixin, View):
    """Client list with search, filtering and pagination."""

    def get(self, request):
        facility = request.current_facility
        qs = Client.objects.for_facility(facility).filter(is_active=True)

        q = request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(pseudonym__icontains=q)

        stage = request.GET.get("stage")
        if stage:
            qs = qs.filter(contact_stage=stage)

        age = request.GET.get("age")
        if age:
            qs = qs.filter(age_cluster=age)

        qs = qs.annotate(last_contact=Max("events__occurred_at")).order_by("pseudonym")

        # Pagination
        from django.core.paginator import Paginator

        paginator = Paginator(qs, 25)
        page = request.GET.get("page")
        clients = paginator.get_page(page)

        pagination_params = urlencode({k: v for k, v in [("q", q), ("stage", stage), ("age", age)] if v})

        context = {
            "clients": clients,
            "q": q,
            "selected_stage": stage,
            "selected_age": age,
            "contact_stages": Client.ContactStage.choices,
            "age_clusters": Client.AgeCluster.choices,
            "pagination_params": pagination_params,
        }

        if request.headers.get("HX-Request"):
            return render(request, "core/clients/partials/table.html", context)
        return render(request, "core/clients/list.html", context)


class ClientDetailView(AssistantOrAboveRequiredMixin, View):
    """Client detail view with event timeline."""

    def get(self, request, pk):
        facility = request.current_facility
        client = get_object_or_404(Client, pk=pk, facility=facility)
        track_client_visit(request.user, client, facility)

        events = (
            Event.objects.filter(client=client, is_deleted=False)
            .select_related("document_type", "created_by")
            .order_by("-occurred_at")
        )

        workitems = (
            WorkItem.objects.filter(
                client=client,
                status__in=[WorkItem.Status.OPEN, WorkItem.Status.IN_PROGRESS],
            )
            .annotate(
                priority_order=Case(
                    When(priority=WorkItem.Priority.URGENT, then=Value(0)),
                    When(priority=WorkItem.Priority.IMPORTANT, then=Value(1)),
                    When(priority=WorkItem.Priority.NORMAL, then=Value(2)),
                    output_field=IntegerField(),
                )
            )
            .order_by("priority_order", "-created_at")
        )

        # AuditLog for qualified client
        if client.contact_stage == Client.ContactStage.QUALIFIED:
            AuditLog.objects.create(
                facility=facility,
                user=request.user,
                action=AuditLog.Action.VIEW_QUALIFIED,
                target_type="Client",
                target_id=str(client.pk),
            )

        active_bans = get_active_bans_for_client(client)

        open_cases = (
            CaseModel.objects.filter(
                facility=facility,
                client=client,
                status=CaseModel.Status.OPEN,
            )
            .select_related("lead_user")
            .order_by("-created_at")
        )

        context = {
            "client": client,
            "events": events,
            "workitems": workitems,
            "active_bans": active_bans,
            "open_cases": open_cases,
            "hide_qualified_details": not request.user.is_staff_or_above,
        }
        return render(request, "core/clients/detail.html", context)


class ClientCreateView(StaffRequiredMixin, View):
    """Create a new client."""

    def get(self, request):
        form = ClientForm(facility=request.current_facility)
        return render(request, "core/clients/form.html", {"form": form, "is_edit": False})

    def post(self, request):
        form = ClientForm(request.POST, facility=request.current_facility)
        if form.is_valid():
            client = create_client(
                facility=request.current_facility,
                user=request.user,
                pseudonym=form.cleaned_data["pseudonym"],
                contact_stage=form.cleaned_data["contact_stage"],
                age_cluster=form.cleaned_data["age_cluster"],
                notes=form.cleaned_data["notes"],
            )
            messages.success(request, _("Klientel wurde erstellt."))
            return redirect("core:client_detail", pk=client.pk)
        return render(request, "core/clients/form.html", {"form": form, "is_edit": False})


class ClientUpdateView(StaffRequiredMixin, View):
    """Edit a client."""

    def get(self, request, pk):
        client = get_object_or_404(Client, pk=pk, facility=request.current_facility)
        form = ClientForm(instance=client, facility=request.current_facility)
        return render(request, "core/clients/form.html", {"form": form, "client": client, "is_edit": True})

    def post(self, request, pk):
        client = get_object_or_404(Client, pk=pk, facility=request.current_facility)
        old_stage = client.contact_stage
        form = ClientForm(request.POST, instance=client, facility=request.current_facility)
        if form.is_valid():
            client = form.save()
            update_client_stage(client, old_stage, client.contact_stage, request.current_facility, request.user)
            if old_stage != client.contact_stage and client.contact_stage == Client.ContactStage.QUALIFIED:
                log_activity(
                    facility=client.facility,
                    actor=self.request.user,
                    verb=Activity.Verb.QUALIFIED,
                    target=client,
                    summary=f"{client.pseudonym} qualifiziert",
                )
            messages.success(request, _("Klientel wurde aktualisiert."))
            return redirect("core:client_detail", pk=client.pk)
        return render(request, "core/clients/form.html", {"form": form, "client": client, "is_edit": True})


class ClientAutocompleteView(AssistantOrAboveRequiredMixin, View):
    """JSON endpoint for client autocomplete."""

    @method_decorator(ratelimit(key="user", rate="30/m", method="GET"))
    def get(self, request):
        q = request.GET.get("q", "").strip()

        qs = Client.objects.filter(
            facility=request.current_facility,
            is_active=True,
        )
        if q:
            qs = qs.filter(pseudonym__icontains=q)
        clients = qs.annotate(
            last_contact=Max("events__occurred_at", filter=Q(events__is_deleted=False)),
        ).order_by(F("last_contact").desc(nulls_last=True), "pseudonym")[:30]

        data = [
            {
                "id": str(c.pk),
                "pseudonym": c.pseudonym,
                "stage": c.get_contact_stage_display(),
            }
            for c in clients
        ]
        return JsonResponse(data, safe=False)


class ClientDataExportJSONView(LeadOrAdminRequiredMixin, View):
    """JSON export of all client data (Art. 20 DSGVO)."""

    @method_decorator(ratelimit(key="user", rate="10/h", method="GET", block=True))
    def get(self, request, pk):
        facility = request.current_facility
        client = get_object_or_404(Client, pk=pk, facility=facility)

        data = export_client_data(client, facility)

        AuditLog.objects.create(
            facility=facility,
            user=request.user,
            action=AuditLog.Action.EXPORT,
            target_type="Client-JSON",
            target_id=str(client.pk),
            detail={"format": "JSON", "pseudonym": client.pseudonym},
            ip_address=get_client_ip(request),
        )

        response = HttpResponse(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            content_type="application/json; charset=utf-8",
        )
        response["Content-Disposition"] = f'attachment; filename="datenauskunft_{client.pseudonym}.json"'
        return response


class ClientDataExportPDFView(LeadOrAdminRequiredMixin, View):
    """PDF export of all client data (Art. 15 DSGVO)."""

    @method_decorator(ratelimit(key="user", rate="10/h", method="GET", block=True))
    def get(self, request, pk):
        facility = request.current_facility
        client = get_object_or_404(Client, pk=pk, facility=facility)

        pdf_bytes = export_client_data_pdf(client, facility)

        AuditLog.objects.create(
            facility=facility,
            user=request.user,
            action=AuditLog.Action.EXPORT,
            target_type="Client-PDF",
            target_id=str(client.pk),
            detail={"format": "PDF", "pseudonym": client.pseudonym},
            ip_address=get_client_ip(request),
        )

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="datenauskunft_{client.pseudonym}.pdf"'
        return response
