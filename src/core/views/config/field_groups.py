"""Config-Views für FieldGroup-Verwaltung im DocumentType-Detail (M0 P6, Refs #1056).

Dünne ``View``-CBVs für das interaktive Feld-Board: Feldgruppen anlegen,
umbenennen und löschen. Jeder Objekt-Lookup ist zweistufig mandantengescoped —
zuerst der ``DocumentType`` über ``facility=request.current_facility`` (IDOR →
404 VOR jeder weiteren Prüfung), dann die ``FieldGroup`` über
``document_type=doc_type`` (fremde Gruppe → 404). Alle Mutationen laufen über
:mod:`core.services.config` (Audit im Service). Jede erfolgreiche Mutation
antwortet über den gemeinsamen Board-Helper: bei ``HX-Request`` mit dem frisch
gerenderten Feld-Board (Swap-Ziel ``#field-board``), sonst mit einem Redirect
zur Detailseite.
"""

import logging

from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext_lazy as _
from django.views import View

from core.forms.config import FieldGroupForm
from core.models import DocumentType, FieldGroup
from core.services import config as config_service
from core.views.config.document_types import board_response
from core.views.mixins import FacilityAdminRequiredMixin

logger = logging.getLogger(__name__)

GROUP_FORM_TEMPLATE = "core/config/document_types/partials/group_form.html"


class ConfigFieldGroupCreateView(FacilityAdminRequiredMixin, View):
    """Legt eine Feldgruppe für den Dokumentationstyp an."""

    def get(self, request, pk):
        doc_type = get_object_or_404(DocumentType, pk=pk, facility=request.current_facility)
        form = FieldGroupForm(doc_type=doc_type, prefix="group-new")
        return render(
            request,
            GROUP_FORM_TEMPLATE,
            {"form": form, "doc_type": doc_type, "is_edit": False},
        )

    def post(self, request, pk):
        doc_type = get_object_or_404(DocumentType, pk=pk, facility=request.current_facility)
        # Prefix "group-new" konsistent zu GET/Board-Render: das Neu-Formular
        # trägt eindeutige Feld-IDs, kein Kollisions-`id_name` neben Edit-Form.
        form = FieldGroupForm(request.POST, doc_type=doc_type, prefix="group-new")
        if not form.is_valid():
            return board_response(
                request, doc_type, group_form=form, error=_("Die Feldgruppe konnte nicht angelegt werden.")
            )
        group = config_service.save_field_group(request, form, doc_type=doc_type, creating=True)
        return board_response(
            request, doc_type, message=_("Feldgruppe „%(name)s“ wurde angelegt.") % {"name": group.name}
        )


class ConfigFieldGroupUpdateView(FacilityAdminRequiredMixin, View):
    """Benennt eine Feldgruppe um / schaltet die Einklappbarkeit."""

    def get(self, request, pk, group_pk):
        doc_type = get_object_or_404(DocumentType, pk=pk, facility=request.current_facility)
        group = get_object_or_404(FieldGroup, pk=group_pk, document_type=doc_type)
        form = FieldGroupForm(instance=group, doc_type=doc_type, prefix=f"group-{group.pk}")
        return render(
            request,
            GROUP_FORM_TEMPLATE,
            {"form": form, "doc_type": doc_type, "group": group, "is_edit": True},
        )

    def post(self, request, pk, group_pk):
        doc_type = get_object_or_404(DocumentType, pk=pk, facility=request.current_facility)
        group = get_object_or_404(FieldGroup, pk=group_pk, document_type=doc_type)
        # Prefix group-<pk> konsistent zu GET: eindeutige Feld-IDs je Edit-Form,
        # keine Kollision mit dem Neu-Formular (Prefix group-new).
        form = FieldGroupForm(request.POST, instance=group, doc_type=doc_type, prefix=f"group-{group.pk}")
        if not form.is_valid():
            if request.headers.get("HX-Request") == "true":
                # Fehler ersetzen das Inline-Formular an Ort und Stelle (Fehlertext
                # im group_form); keine Flash-Meldung — sie stünde im Store.
                return render(
                    request,
                    GROUP_FORM_TEMPLATE,
                    {"form": form, "doc_type": doc_type, "group": group, "is_edit": True},
                )
            return board_response(request, doc_type, error=_("Die Feldgruppe konnte nicht gespeichert werden."))
        config_service.save_field_group(request, form, doc_type=doc_type, creating=False)
        # Das Editier-Formular postet per hx-target="this"; bei Erfolg soll das
        # ganze Board ersetzt werden → HX-Retarget auf #field-board.
        return board_response(
            request,
            doc_type,
            retarget=True,
            message=_("Feldgruppe „%(name)s“ wurde gespeichert.") % {"name": group.name},
        )


class ConfigFieldGroupDeleteView(FacilityAdminRequiredMixin, View):
    """Löscht eine Feldgruppe — zugeordnete Felder werden gruppenlos (SET_NULL)."""

    def post(self, request, pk, group_pk):
        doc_type = get_object_or_404(DocumentType, pk=pk, facility=request.current_facility)
        group = get_object_or_404(FieldGroup, pk=group_pk, document_type=doc_type)
        name = group.name
        config_service.delete_field_group(request, group)
        return board_response(request, doc_type, message=_("Feldgruppe „%(name)s“ wurde gelöscht.") % {"name": name})
