"""Config-CRUD-Views für DocumentType (M0 P5a, Refs #1056).

Dünne ``View``-CBVs: Suche/Filter/HTMX-Partial in der Liste, alle Mutationen
über :mod:`core.services.config` (Audit im Service). Zugriff nur für die
Anwendungsbetreuung; jeder Objekt-Lookup ist über
``get_object_or_404(..., facility=request.current_facility)`` mandantengescoped
(IDOR → 404 vor jeder Payload-Validierung).
"""

import logging
from urllib.parse import urlencode

from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views import View

from core.forms.config import DocumentTypeForm
from core.models import DocumentType, Event
from core.services import config as config_service
from core.services.config import ConfigDeleteBlocked
from core.views.mixins import (
    FacilityAdminRequiredMixin,
    HTMXPartialMixin,
    PaginatedListMixin,
)

logger = logging.getLogger(__name__)

FORM_TEMPLATE = "core/config/document_types/form.html"


class ConfigDocumentTypeListView(FacilityAdminRequiredMixin, PaginatedListMixin, HTMXPartialMixin, View):
    """Liste der Dokumentationstypen mit Suche (Name) + Filter (Kategorie/aktiv)."""

    template_name = "core/config/document_types/list.html"
    partial_template_name = "core/config/document_types/partials/table.html"

    def get(self, request):
        qs = DocumentType.objects.for_facility(request.current_facility)

        q = request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(name__icontains=q)

        category = request.GET.get("category", "")
        if category:
            qs = qs.filter(category=category)

        active = request.GET.get("is_active", "")
        if active == "true":
            qs = qs.filter(is_active=True)
        elif active == "false":
            qs = qs.filter(is_active=False)

        qs = qs.annotate(event_count=Count("events")).order_by("sort_order", "name")
        doc_types = self.paginate(qs, request)

        params = {k: v for k, v in (("q", q), ("category", category), ("is_active", active)) if v}
        context = {
            "doc_types": doc_types,
            "q": q,
            "selected_category": category,
            "selected_active": active,
            "categories": DocumentType.Category.choices,
            "pagination_params": urlencode(params),
        }
        return self.render_htmx_or_full(context)


class ConfigDocumentTypeCreateView(FacilityAdminRequiredMixin, View):
    """Neuen Dokumentationstyp anlegen (system_type nur hier editierbar)."""

    def get(self, request):
        form = DocumentTypeForm(facility=request.current_facility)
        return render(request, FORM_TEMPLATE, {"form": form, "is_edit": False})

    def post(self, request):
        form = DocumentTypeForm(request.POST, facility=request.current_facility)
        if form.is_valid():
            obj = config_service.save_document_type(request, form, creating=True)
            messages.success(request, _("Dokumentationstyp „%(name)s“ wurde angelegt.") % {"name": obj.name})
            return redirect("core:config_doctype_detail", pk=obj.pk)
        return render(request, FORM_TEMPLATE, {"form": form, "is_edit": False})


class ConfigDocumentTypeDetailView(FacilityAdminRequiredMixin, View):
    """Stammdaten + read-only Feldliste (interaktives Feld-Board kommt in P6)."""

    def get(self, request, pk):
        doc_type = get_object_or_404(DocumentType, pk=pk, facility=request.current_facility)
        fields = doc_type.fields.select_related("field_template", "field_group").order_by(
            "field_group__sort_order", "sort_order"
        )
        groups = doc_type.field_groups.order_by("sort_order", "name")
        event_count = Event.objects.filter(document_type=doc_type).count()
        context = {
            "doc_type": doc_type,
            "fields": fields,
            "groups": groups,
            "event_count": event_count,
        }
        return render(request, "core/config/document_types/detail.html", context)


class ConfigDocumentTypeUpdateView(FacilityAdminRequiredMixin, View):
    """Dokumentationstyp bearbeiten (system_type serverseitig unveränderbar)."""

    def get(self, request, pk):
        doc_type = get_object_or_404(DocumentType, pk=pk, facility=request.current_facility)
        form = DocumentTypeForm(instance=doc_type, facility=request.current_facility)
        return render(request, FORM_TEMPLATE, {"form": form, "doc_type": doc_type, "is_edit": True})

    def post(self, request, pk):
        doc_type = get_object_or_404(DocumentType, pk=pk, facility=request.current_facility)
        form = DocumentTypeForm(request.POST, instance=doc_type, facility=request.current_facility)
        if form.is_valid():
            config_service.save_document_type(request, form, creating=False)
            messages.success(request, _("Dokumentationstyp „%(name)s“ wurde aktualisiert.") % {"name": doc_type.name})
            return redirect("core:config_doctype_detail", pk=doc_type.pk)
        return render(request, FORM_TEMPLATE, {"form": form, "doc_type": doc_type, "is_edit": True})


class ConfigDocumentTypeDeleteView(FacilityAdminRequiredMixin, View):
    """Löschen mit Referenz-Zähler-Vorschau; Blockade → Meldung + Redirect."""

    def get(self, request, pk):
        doc_type = get_object_or_404(DocumentType, pk=pk, facility=request.current_facility)
        event_count = Event.objects.filter(document_type=doc_type).count()
        return render(
            request,
            "core/config/document_types/confirm_delete.html",
            {"doc_type": doc_type, "event_count": event_count},
        )

    def post(self, request, pk):
        doc_type = get_object_or_404(DocumentType, pk=pk, facility=request.current_facility)
        name = doc_type.name
        try:
            config_service.delete_document_type(request, doc_type)
        except ConfigDeleteBlocked as exc:
            for reason in exc.reasons:
                messages.error(request, reason)
            return redirect("core:config_doctype_detail", pk=doc_type.pk)
        messages.success(request, _("Dokumentationstyp „%(name)s“ wurde gelöscht.") % {"name": name})
        return redirect("core:config_doctype_list")
