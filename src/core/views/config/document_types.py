"""Config-CRUD-Views für DocumentType (M0 P5a, Refs #1056).

Dünne ``View``-CBVs: Suche/Filter/HTMX-Partial in der Liste, alle Mutationen
über :mod:`core.services.config` (Audit im Service). Zugriff nur für die
Anwendungsbetreuung; jeder Objekt-Lookup ist über
``get_object_or_404(..., facility=request.current_facility)`` mandantengescoped
(IDOR → 404 vor jeder Payload-Validierung).
"""

import logging
import uuid
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Prefetch
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views import View

from core.forms.config import DocumentTypeFieldAddForm, DocumentTypeForm
from core.models import DocumentType, DocumentTypeField, Event, FieldGroup
from core.services import config as config_service
from core.services.config import ConfigDeleteBlocked
from core.views.mixins import (
    FacilityAdminRequiredMixin,
    HTMXPartialMixin,
    PaginatedListMixin,
)

logger = logging.getLogger(__name__)

FORM_TEMPLATE = "core/config/document_types/form.html"
BOARD_TEMPLATE = "core/config/document_types/partials/field_board.html"


# ---------------------------------------------------------------------------
# Feld-Board — gemeinsamer Render-Helper (P6)
# ---------------------------------------------------------------------------
#
# Das interaktive Feld-Board ist EIN Swap-Ziel ``#field-board``. Jede
# Board-Mutation (Feld zuordnen/entfernen, Gruppe anlegen/umbenennen/löschen)
# rendert es bei ``HX-Request`` frisch, sonst redirectet sie zur Detailseite.
# Sortierung wird hier (View/DB) vorbereitet, NICHT im Template: Gruppen nach
# ``(sort_order, name)``, Felder je Gruppe nach ``sort_order``, ungruppierte
# Felder als fixer „Ohne Gruppe"-Block zuletzt.


def board_context(doc_type, *, add_form=None, group_form=None):
    """Baut den Kontext des Feld-Boards (Gruppen + ungruppierte Felder + Formulare)."""
    from core.forms.config import FieldGroupForm

    groups = list(
        FieldGroup.objects.filter(document_type=doc_type)
        .order_by("sort_order", "name")
        .prefetch_related(
            Prefetch(
                "fields",
                queryset=DocumentTypeField.objects.select_related("field_template").order_by("sort_order"),
            )
        )
    )
    ungrouped_fields = list(
        DocumentTypeField.objects.filter(document_type=doc_type, field_group__isnull=True)
        .select_related("field_template")
        .order_by("sort_order")
    )
    return {
        "doc_type": doc_type,
        "groups": groups,
        "ungrouped_fields": ungrouped_fields,
        "add_form": add_form if add_form is not None else DocumentTypeFieldAddForm(doc_type=doc_type),
        "group_form": group_form if group_form is not None else FieldGroupForm(doc_type=doc_type),
    }


def board_response(request, doc_type, *, add_form=None, group_form=None, retarget=False, message=None, error=None):
    """HX-Request → frisch gerendertes ``field_board.html``, sonst Redirect zum Detail.

    ``retarget=True`` setzt ``HX-Retarget``/``HX-Reswap`` auf ``#field-board`` —
    genutzt vom Inline-Editier-Formular einer Gruppe, das per ``hx-target="this"``
    postet: bei Erfolg soll trotzdem das ganze Board (nicht nur das Formular)
    ersetzt werden.

    ``message``/``error`` werden **nur** im Nicht-HX-Zweig (vor dem Redirect) als
    Flash-Meldung gesetzt. Bei ``HX-Request`` rendert der Helper nur
    ``field_board.html`` (ohne Messages-Region) — eine hier gesetzte Meldung
    stünde im Store und poppte verspätet beim nächsten Vollseiten-Load auf.
    Konvention: ``workitem_actions.py`` (Messages nur im Redirect-Zweig).
    """
    if request.headers.get("HX-Request") == "true":
        context = board_context(doc_type, add_form=add_form, group_form=group_form)
        response = render(request, BOARD_TEMPLATE, context)
        if retarget:
            response["HX-Retarget"] = "#field-board"
            response["HX-Reswap"] = "outerHTML"
        return response
    if message is not None:
        messages.success(request, message)
    if error is not None:
        messages.error(request, error)
    return redirect("core:config_doctype_detail", pk=doc_type.pk)


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
    """Stammdaten + interaktives Feld-Board (Feld-Zuordnung + FieldGroup-Verwaltung)."""

    def get(self, request, pk):
        doc_type = get_object_or_404(DocumentType, pk=pk, facility=request.current_facility)
        event_count = Event.objects.filter(document_type=doc_type).count()
        context = {**board_context(doc_type), "event_count": event_count}
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


# ---------------------------------------------------------------------------
# DocumentTypeField — Zuordnung/Entfernung (P6, HTMX-Board)
# ---------------------------------------------------------------------------


class ConfigDocTypeFieldAddView(FacilityAdminRequiredMixin, View):
    """Ordnet eine Feldvorlage dem Dokumentationstyp zu (Board-Mutation)."""

    def post(self, request, pk):
        doc_type = get_object_or_404(DocumentType, pk=pk, facility=request.current_facility)

        # Eine Feldgruppe aus einem ANDEREN Dokumentationstyp (derselben
        # Einrichtung) ist ein bewusster 400 — konsistent zur Service-Semantik
        # (add_field_to_doctype/move_field: ValueError → HTTP 400). Das doc-type-
        # gescopte Formularfeld würde sie zwar als „ungültige Auswahl" ablehnen,
        # der Plan verlangt hier aber den harten 400-Pfad.
        group_pk = request.POST.get("field_group")
        if group_pk:
            # Roh in den UUID-pk-Filter gefüttert würde ein Nicht-UUID-String
            # Djangos UUIDField beim Query-Ausführen eine ``ValidationError``
            # werfen (NICHT ValueError) → ungefangen → 500. Vorab kanonisieren
            # (Muster: ``_parse_uuids`` in views/config/reorder.py).
            try:
                group_uuid = uuid.UUID(group_pk)
            except (ValueError, TypeError):
                return HttpResponseBadRequest(_("Ungültige Feldgruppen-ID."))
            foreign_group = (
                FieldGroup.objects.filter(pk=group_uuid, document_type__facility=request.current_facility)
                .exclude(document_type=doc_type)
                .first()
            )
            if foreign_group is not None:
                return HttpResponseBadRequest(_("Die Feldgruppe gehört nicht zu diesem Dokumentationstyp."))

        form = DocumentTypeFieldAddForm(request.POST, doc_type=doc_type)
        if not form.is_valid():
            return board_response(
                request, doc_type, add_form=form, error=_("Die Feldvorlage konnte nicht zugeordnet werden.")
            )

        field_template = form.cleaned_data["field_template"]
        field_group = form.cleaned_data.get("field_group")
        try:
            config_service.add_field_to_doctype(request, doc_type, field_template, field_group=field_group)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        except ValidationError as exc:
            form.add_error("field_template", exc)
            return board_response(
                request, doc_type, add_form=form, error=_("Die Feldvorlage konnte nicht zugeordnet werden.")
            )

        return board_response(
            request, doc_type, message=_("Feld „%(name)s“ wurde zugeordnet.") % {"name": field_template.name}
        )


class ConfigDocTypeFieldRemoveView(FacilityAdminRequiredMixin, View):
    """Entfernt eine Feld-Zuordnung vom Dokumentationstyp (Board-Mutation)."""

    def post(self, request, pk, dtf_pk):
        doc_type = get_object_or_404(DocumentType, pk=pk, facility=request.current_facility)
        dtf = get_object_or_404(DocumentTypeField, pk=dtf_pk, document_type=doc_type)
        name = dtf.field_template.name
        config_service.remove_field_from_doctype(request, dtf)
        return board_response(request, doc_type, message=_("Feld „%(name)s“ wurde entfernt.") % {"name": name})
