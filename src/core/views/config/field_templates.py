"""Config-CRUD-Views für FieldTemplate (M0 P5b, Refs #1056).

Dünne ``View``-CBVs mit Verwendungszählern in der Liste (Dokumentationstyp-
Zuordnungen + Anhänge), Löschschutz mit read-only vorberechneten Blockern und
einem Toggle für die Soft-Delete-Alternative „deaktivieren". Alle Mutationen
laufen über :mod:`core.services.config` (Audit im Service); jeder Objekt-Lookup
ist über ``get_object_or_404(..., facility=request.current_facility)``
mandantengescoped (IDOR → 404).
"""

import logging
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View

from core.constants import RATELIMIT_MUTATION
from core.forms.config import FieldTemplateForm
from core.models import FieldTemplate
from core.reporting.registry import describe_field_bindings, diagnose_field_bindings
from core.services import config as config_service
from core.services.compliance.sensitivity import field_protection
from core.services.config import ConfigDeleteBlocked
from core.services.option_usage import option_slugs_in_use
from core.utils.ratelimit import ratelimit
from core.views.mixins import (
    FacilityAdminRequiredMixin,
    HTMXPartialMixin,
    PaginatedListMixin,
)

logger = logging.getLogger(__name__)

FORM_TEMPLATE = "core/config/field_templates/form.html"


class ConfigFieldTemplateListView(FacilityAdminRequiredMixin, PaginatedListMixin, HTMXPartialMixin, View):
    """Liste der Feldvorlagen mit Suche + Filter (Typ/aktiv) und Verwendungszählern."""

    template_name = "core/config/field_templates/list.html"
    partial_template_name = "core/config/field_templates/partials/table.html"

    def get(self, request):
        qs = FieldTemplate.objects.for_facility(request.current_facility)

        q = request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(name__icontains=q)

        field_type = request.GET.get("field_type", "")
        if field_type:
            qs = qs.filter(field_type=field_type)

        active = request.GET.get("is_active", "")
        if active == "true":
            qs = qs.filter(is_active=True)
        elif active == "false":
            qs = qs.filter(is_active=False)

        # distinct=True je Aggregat verhindert die Join-Fanout-Multiplikation
        # bei zwei gleichzeitigen Count()-Annotationen.
        qs = (
            qs.annotate(
                doctype_count=Count("document_type_fields", distinct=True),
                attachment_count=Count("attachments", distinct=True),
            )
            .prefetch_related("document_type_fields__document_type")
            .order_by("name")
        )
        field_templates = self.paginate(qs, request)
        for template in field_templates:
            template.protection = field_protection(None, template)
            template.protection_by_type = [
                {
                    "name": assignment.document_type.name,
                    "protection": field_protection(assignment.document_type, template),
                }
                for assignment in template.document_type_fields.all()
            ]

        params = {k: v for k, v in (("q", q), ("field_type", field_type), ("is_active", active)) if v}
        context = {
            "field_templates": field_templates,
            "q": q,
            "selected_field_type": field_type,
            "selected_active": active,
            "field_types": FieldTemplate.FieldType.choices,
            "pagination_params": urlencode(params),
        }
        return self.render_htmx_or_full(context)


class ConfigFieldTemplateCreateView(FacilityAdminRequiredMixin, View):
    """Neue Feldvorlage anlegen (Slug wird automatisch erzeugt)."""

    def get(self, request):
        form = FieldTemplateForm(facility=request.current_facility)
        return render(request, FORM_TEMPLATE, {"form": form, "is_edit": False})

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True))
    def post(self, request):
        form = FieldTemplateForm(request.POST, facility=request.current_facility)
        if form.is_valid():
            obj = config_service.save_field_template(request, form, creating=True, facility=request.current_facility)
            messages.success(request, _("Feldvorlage „%(name)s“ wurde angelegt.") % {"name": obj.name})
            return redirect("core:config_fieldtemplate_list")
        return render(request, FORM_TEMPLATE, {"form": form, "is_edit": False})


class ConfigFieldTemplateUpdateView(FacilityAdminRequiredMixin, View):
    """Feldvorlage bearbeiten (Slug read-only/unveränderbar)."""

    def get(self, request, pk):
        field_template = get_object_or_404(FieldTemplate, pk=pk, facility=request.current_facility)
        form = FieldTemplateForm(
            instance=field_template,
            facility=request.current_facility,
            used_option_slugs=option_slugs_in_use(field_template),
        )
        context = {
            "form": form,
            "field_template": field_template,
            "is_edit": True,
            # E2 (Refs #1848): OHNE entity_type-Filter — FieldTemplate hat kein
            # entity_type-Feld (facility-weit, M:N über DocumentTypeField), die
            # Ereignis-Einschränkung von DocumentType gilt hier nicht.
            # facility durchgereicht (Review-Befund): nur verfügbare Backends
            # dürfen genannt werden, sonst 404-Widerspruch zu Panel/Export.
            "field_bindings": describe_field_bindings(field_template, request.current_facility),
            "report_binding_warnings": diagnose_field_bindings(field_template, request.current_facility),
        }
        return render(request, FORM_TEMPLATE, context)

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True))
    def post(self, request, pk):
        field_template = get_object_or_404(FieldTemplate, pk=pk, facility=request.current_facility)
        form = FieldTemplateForm(
            request.POST,
            instance=field_template,
            facility=request.current_facility,
            used_option_slugs=option_slugs_in_use(field_template),
        )
        if form.is_valid():
            try:
                config_service.save_field_template(request, form, creating=False, facility=request.current_facility)
            except ValidationError as error:
                form.add_error(None, error)
            else:
                messages.success(
                    request, _("Feldvorlage „%(name)s“ wurde aktualisiert.") % {"name": field_template.name}
                )
                return redirect("core:config_fieldtemplate_list")
        context = {
            "form": form,
            "field_template": field_template,
            "is_edit": True,
            "field_bindings": describe_field_bindings(field_template, request.current_facility),
            "report_binding_warnings": diagnose_field_bindings(field_template, request.current_facility),
        }
        return render(request, FORM_TEMPLATE, context)


class ConfigFieldTemplateDeleteView(FacilityAdminRequiredMixin, View):
    """Löschen mit read-only Blocker-Vorschau; primär „stattdessen deaktivieren"."""

    def get(self, request, pk):
        field_template = get_object_or_404(FieldTemplate, pk=pk, facility=request.current_facility)
        blockers = config_service.field_template_delete_blockers(field_template)
        return render(
            request,
            "core/config/field_templates/confirm_delete.html",
            {
                "field_template": field_template,
                "blockers": blockers,
                # Refs #1737 (A): Papierkorb-Anteil der Blockade → Ausweg-Hinweis.
                "client_trashed_count": config_service.field_template_trashed_client_count(field_template),
                # Refs #1818: zweiter Papierkorb, zweiter Ausweg-Link.
                "case_trashed_count": config_service.field_template_trashed_case_count(field_template),
            },
        )

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True))
    def post(self, request, pk):
        field_template = get_object_or_404(FieldTemplate, pk=pk, facility=request.current_facility)
        name = field_template.name
        try:
            config_service.delete_field_template(request, field_template)
        except ConfigDeleteBlocked as exc:
            for reason in exc.reasons:
                messages.error(request, reason)
            return redirect("core:config_fieldtemplate_delete", pk=field_template.pk)
        messages.success(request, _("Feldvorlage „%(name)s“ wurde gelöscht.") % {"name": name})
        return redirect("core:config_fieldtemplate_list")


class ConfigFieldTemplateToggleActiveView(FacilityAdminRequiredMixin, View):
    """Apply the explicitly requested field template activation state."""

    @method_decorator(ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True))
    def post(self, request, pk):
        field_template = get_object_or_404(FieldTemplate, pk=pk, facility=request.current_facility)
        active = request.POST.get("active")
        if active not in {"0", "1"}:
            return HttpResponseBadRequest(_("Ungültige Zielaktion."))
        new_state = active == "1"
        try:
            config_service.set_field_template_active(request, field_template, active=new_state)
        except ValidationError as error:
            for message in error.messages:
                messages.error(request, message)
            return redirect("core:config_fieldtemplate_list")
        if new_state:
            messages.success(request, _("Feldvorlage „%(name)s“ wurde aktiviert.") % {"name": field_template.name})
        else:
            messages.success(request, _("Feldvorlage „%(name)s“ wurde deaktiviert.") % {"name": field_template.name})
        return redirect("core:config_fieldtemplate_list")
