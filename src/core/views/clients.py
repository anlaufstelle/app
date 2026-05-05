"""Views for client management."""

import json
import logging
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Case, F, IntegerField, Max, Q, Value, When
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.constants import DEFAULT_PAGE_SIZE, RATELIMIT_MUTATION
from core.forms.clients import ClientForm
from core.models import AuditLog, Client, Event, WorkItem
from core.models import Case as CaseModel
from core.services.audit import log_audit_event
from core.services.bans import get_active_bans_for_client
from core.services.client_export import export_client_data, export_client_data_pdf
from core.services.clients import create_client, track_client_visit, update_client
from core.services.sudo_mode import RequireSudoModeMixin
from core.utils.downloads import safe_download_response
from core.views.mixins import AssistantOrAboveRequiredMixin, LeadOrAdminRequiredMixin, StaffRequiredMixin

logger = logging.getLogger(__name__)


class ClientListView(AssistantOrAboveRequiredMixin, View):
    """Client list with search, filtering and pagination."""

    def get(self, request):
        facility = request.current_facility
        qs = Client.objects.for_facility(facility).filter(is_active=True, is_deleted=False)

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

        from core.views.utils import safe_page_param

        paginator = Paginator(qs, DEFAULT_PAGE_SIZE)
        clients = paginator.get_page(safe_page_param(request))

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
        # Soft-deletete Personen werden in der Detail-View nicht ausgespielt;
        # Admins kommen ueber die Papierkorb-Ansicht (#626) an den Datensatz.
        client = get_object_or_404(Client, pk=pk, facility=facility, is_deleted=False)
        track_client_visit(request.user, client, facility)

        events = (
            Event.objects.visible_to(request.user)
            .filter(client=client, is_deleted=False)
            .select_related("document_type", "created_by")
            .order_by("-occurred_at")
        )

        workitems = (
            WorkItem.objects.filter(
                client=client,
                status__in=[WorkItem.Status.OPEN, WorkItem.Status.IN_PROGRESS],
            )
            .select_related("client", "assigned_to", "created_by")
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
            log_audit_event(request, AuditLog.Action.VIEW_QUALIFIED, target_obj=client)

        active_bans = get_active_bans_for_client(client, user=request.user)

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


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
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
            messages.success(request, _("Person wurde angelegt."))
            return redirect("core:client_detail", pk=client.pk)
        return render(request, "core/clients/form.html", {"form": form, "is_edit": False})


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
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
            expected_updated_at = request.POST.get("expected_updated_at") or None
            try:
                client = update_client(
                    client,
                    request.user,
                    old_stage=old_stage,
                    expected_updated_at=expected_updated_at,
                    **form.cleaned_data,
                )
            except ValidationError as e:
                messages.error(request, e.message if hasattr(e, "message") else str(e))
                return redirect("core:client_update", pk=client.pk)
            messages.success(request, _("Person wurde aktualisiert."))
            return redirect("core:client_detail", pk=client.pk)
        return render(request, "core/clients/form.html", {"form": form, "client": client, "is_edit": True})


class ClientAutocompleteView(AssistantOrAboveRequiredMixin, View):
    """JSON endpoint for client autocomplete."""

    # Refs #737: block=True liefert 429 bei Limit-Verstoss (sonst 200 trotz
    # Ueberschreitung — der Limit waere effektiv unwirksam).
    @method_decorator(ratelimit(key="user", rate="30/m", method="GET", block=True))
    def get(self, request):
        from core.services.event import CONTACT_STAGE_ORDER, stage_index

        q = request.GET.get("q", "").strip()

        qs = Client.objects.filter(
            facility=request.current_facility,
            is_active=True,
        )
        if q:
            qs = qs.filter(pseudonym__icontains=q)

        # Issue #507: filter by min_contact_stage from the selected DocumentType.
        # Frontend passes data-min-stage from the DocumentType <option>; we drop
        # all clients whose contact_stage is below the requirement.
        min_stage = request.GET.get("min_stage", "").strip()
        if min_stage:
            required = stage_index(min_stage)
            if required >= 0:
                allowed = [s for s in CONTACT_STAGE_ORDER if stage_index(s) >= required]
                qs = qs.filter(contact_stage__in=allowed)

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


class ClientDataExportJSONView(LeadOrAdminRequiredMixin, RequireSudoModeMixin, View):
    """JSON export of all client data (Art. 20 DSGVO).

    Refs #683: SudoMode erzwingt Re-Auth — gestohlene Session reicht
    nicht fuer Daten-Export.
    """

    @method_decorator(ratelimit(key="user", rate="10/h", method="GET", block=True))
    def get(self, request, pk):
        facility = request.current_facility
        client = get_object_or_404(Client, pk=pk, facility=facility)

        data = export_client_data(client, facility, request.user)

        log_audit_event(
            request,
            AuditLog.Action.EXPORT,
            target_obj=client,
            target_type="Client-JSON",
            detail={"format": "JSON", "pseudonym": client.pseudonym},
        )

        return safe_download_response(
            f"datenauskunft_{client.pseudonym}.json",
            "application/json; charset=utf-8",
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
        )


class ClientDataExportPDFView(LeadOrAdminRequiredMixin, RequireSudoModeMixin, View):
    """PDF export of all client data (Art. 15 DSGVO).

    Refs #683: SudoMode erzwingt Re-Auth.
    """

    @method_decorator(ratelimit(key="user", rate="10/h", method="GET", block=True))
    def get(self, request, pk):
        facility = request.current_facility
        client = get_object_or_404(Client, pk=pk, facility=facility)

        pdf_bytes = export_client_data_pdf(client, facility, request.user)

        log_audit_event(
            request,
            AuditLog.Action.EXPORT,
            target_obj=client,
            target_type="Client-PDF",
            detail={"format": "PDF", "pseudonym": client.pseudonym},
        )

        return safe_download_response(
            f"datenauskunft_{client.pseudonym}.pdf",
            "application/pdf",
            pdf_bytes,
        )
