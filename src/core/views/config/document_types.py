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
from django.db.models import Count, IntegerField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views import View

from core.forms.config import DocumentTypeFieldAddForm, DocumentTypeForm
from core.models import (
    Case,
    Client,
    DocumentType,
    DocumentTypeField,
    Event,
    FieldCondition,
    FieldGroup,
    QuickTemplate,
)
from core.reporting.registry import describe_type_bindings
from core.services import config as config_service
from core.services.config import ConfigDeleteBlocked
from core.services.events import evaluate_tally_eligibility
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
# Sortierung wird hier (Service/DB) vorbereitet, NICHT im Template: die globale
# Block-Sequenz (#1713) interleavt Gruppen und ungruppierte Felder in EINER
# Reihenfolge (``iter_blocks_in_order``) — es gibt keinen fixen „Ohne Gruppe"-
# Block mehr, freie Felder sitzen als Top-Level-Blöcke zwischen den Gruppen.


def board_context(doc_type, *, add_form=None, group_form=None):
    """Baut den Kontext des Feld-Boards (globale Block-Sequenz + Formulare, #1713)."""
    from core.forms.config import FieldGroupForm

    # Interleavte Top-Level-Blöcke (Gruppen inkl. Innen-Feldern + freie Felder),
    # sortiert nach globaler Blockposition — gemeinsame Wahrheitsquelle mit
    # Formular/Offline/Feed.
    blocks = config_service.iter_blocks_in_order(doc_type)
    # Refs #1725(b): je Feld die Zahl der Datensätze, die beim Entfernen einen
    # „Nicht mehr zugeordnet"-Wert behielten — speist die hx-confirm-Warnung. Ein
    # Count pro dtf; das Board ist klein.
    for block in blocks:
        fields = block["fields"] if block["kind"] == "group" else [block["field"]]
        for dtf in fields:
            dtf.removal_impact = config_service.count_field_removal_impact(dtf)
    return {
        "doc_type": doc_type,
        "blocks": blocks,
        "add_form": add_form if add_form is not None else DocumentTypeFieldAddForm(doc_type=doc_type),
        # Prefix "group-new": das stets vorhandene Neu-Formular bekommt eindeutige
        # Feld-IDs (id_group-new-name), damit es nicht mit einem gleichzeitig
        # offenen Edit-Formular (Prefix group-<pk>) kollidiert.
        "group_form": group_form if group_form is not None else FieldGroupForm(doc_type=doc_type, prefix="group-new"),
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
        # P11 (Refs #1056): Jede Board-Mutation stößt die Live-Formularvorschau an.
        # Der Header feuert clientseitig ``refresh-preview`` am auslösenden Element;
        # das Event bubbelt zum Body, wo die Vorschau (``hx-trigger="load,
        # refresh-preview from:body"``) es aufnimmt und sich CSP-konform ohne
        # Inline-JS neu lädt. #1718: zusätzlich ``refresh-conditions`` (Komma-Liste,
        # HTMX-Standard) — das Bedingungs-Board lauscht darauf am Body und lädt sich
        # nach Feld-Mutationen (neues Zielfeld, Feld-Anzahl-Schwelle) ohne Reload
        # neu. Rein additiv — Body/Status/übrige Header unverändert.
        response["HX-Trigger"] = "refresh-preview, refresh-conditions"
        return response
    if message is not None:
        messages.success(request, message)
    if error is not None:
        messages.error(request, error)
    return redirect("core:config_doctype_detail", pk=doc_type.pk)


def report_bindings_context(doc_type, facility):
    """„Erscheint in …" (E2, Refs #1848) — Context-Fragment, NUR für Ereignis-Typen.

    An Personen-/Fall-Typen ist die Berichtsfrage gegenstandslos (der
    Sachbericht und seine Geschwister zählen Ereignisse, keine Personen/Fälle)
    — der Context-Schlüssel ``report_bindings`` wird für sie bewusst GAR NICHT
    gesetzt (leeres Dict, kein ``[]``-Wert): „keine Aussage" (Key fehlt) und
    „keine Bindung" (Liste leer) sollen sich im Context nicht vermischen. Das
    Template-``{% if %}`` bleibt die zweite, unabhängige Sicherung.

    ``facility`` wird durchgereicht an :func:`~core.reporting.registry.describe_type_bindings`
    (Review-Befund #1848): nur *verfügbare* Backends dürfen genannt werden,
    sonst behauptete die Auskunft „erscheint in X" für ein Backend, das Panel/
    Export mit 404 quittieren würden.
    """
    if doc_type.entity_type != DocumentType.EntityType.EVENT:
        return {}
    return {"report_bindings": describe_type_bindings(doc_type, facility)}


def _count_sub(qs, group_field="document_type"):
    """Korrelierte Aggregat-Subquery: Anzahl der Zeilen von ``qs`` je Gruppe.

    §F.2 (Refs #1813), Muster ``AttachmentListView._chain_version_counts``
    (attachments.py:242). Ein Statement statt eines Joins pro Annotation —
    drei gleichzeitige ``Count(..., distinct=True)``-Joins (Feldgruppen ×
    Felder × Bedingungen) würden ein Kreuzprodukt aufspannen, das den
    bestehenden ``event_count`` mitverzerrt und auf großen Katalogen teuer
    wird.

    ``.order_by()`` löscht das ``Meta.ordering`` der übergebenen Queryset —
    sonst landet das Sortierfeld (z. B. ``FieldGroup.sort_order``) im
    GROUP BY und die Subquery liefert je Sortierwert eine eigene Zeile statt
    einer einzigen Summe je Gruppe.
    """
    return Coalesce(
        Subquery(
            qs.order_by().values(group_field).annotate(n=Count("pk")).values("n"),
            output_field=IntegerField(),
        ),
        Value(0),
    )


class ConfigDocumentTypeListView(FacilityAdminRequiredMixin, PaginatedListMixin, HTMXPartialMixin, View):
    """Liste der Dokumentationstypen mit Suche (Name) + Filter (aktiv)."""

    template_name = "core/config/document_types/list.html"
    partial_template_name = "core/config/document_types/partials/table.html"

    def get(self, request):
        qs = DocumentType.objects.for_facility(request.current_facility)

        q = request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(name__icontains=q)

        active = request.GET.get("is_active", "")
        if active == "true":
            qs = qs.filter(is_active=True)
        elif active == "false":
            qs = qs.filter(is_active=False)

        qs = qs.annotate(
            event_count=Count("events"),
            block_count=_count_sub(FieldGroup.objects.filter(document_type=OuterRef("pk"))),
            field_count=_count_sub(DocumentTypeField.objects.filter(document_type=OuterRef("pk"))),
            condition_count=_count_sub(
                FieldCondition.objects.filter(target_field__document_type=OuterRef("pk")),
                group_field="target_field__document_type",
            ),
        ).order_by("sort_order", "name")
        doc_types = self.paginate(qs, request)

        params = {k: v for k, v in (("q", q), ("is_active", active)) if v}
        context = {
            "doc_types": doc_types,
            "q": q,
            "selected_active": active,
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
        # Lokaler Import (Muster board_context → FieldGroupForm): hält das
        # config-Views-Package importzyklenfrei (conditions.py importiert nicht
        # zurück auf dieses Modul).
        from core.views.config.conditions import condition_board_context

        doc_type = get_object_or_404(DocumentType, pk=pk, facility=request.current_facility)
        # #1728: Nutzungszähler folgt der entity_type-Weiche (Muster
        # ConfigDocumentTypeDeleteView) statt fest auf Event zu zählen — sonst
        # zeigt ein Client-/Case-Typ hier immer „Ereignisse: 0".
        if doc_type.entity_type == DocumentType.EntityType.CLIENT:
            usage_count = Client.objects.filter(document_type=doc_type).count()
        elif doc_type.entity_type == DocumentType.EntityType.CASE:
            usage_count = Case.objects.filter(document_type=doc_type).count()
        else:
            usage_count = Event.objects.filter(document_type=doc_type).count()
        context = {
            **board_context(doc_type),
            **condition_board_context(doc_type),
            "usage_count": usage_count,
            "tally_eligibility": evaluate_tally_eligibility(doc_type),
            **report_bindings_context(doc_type, request.current_facility),
        }
        return render(request, "core/config/document_types/detail.html", context)


class ConfigDocumentTypeUpdateView(FacilityAdminRequiredMixin, View):
    """Dokumentationstyp bearbeiten (system_type serverseitig unveränderbar)."""

    def get(self, request, pk):
        doc_type = get_object_or_404(DocumentType, pk=pk, facility=request.current_facility)
        form = DocumentTypeForm(instance=doc_type, facility=request.current_facility)
        context = {
            "form": form,
            "doc_type": doc_type,
            "is_edit": True,
            **report_bindings_context(doc_type, request.current_facility),
        }
        return render(request, FORM_TEMPLATE, context)

    def post(self, request, pk):
        doc_type = get_object_or_404(DocumentType, pk=pk, facility=request.current_facility)
        form = DocumentTypeForm(request.POST, instance=doc_type, facility=request.current_facility)
        if form.is_valid():
            config_service.save_document_type(request, form, creating=False)
            messages.success(request, _("Dokumentationstyp „%(name)s“ wurde aktualisiert.") % {"name": doc_type.name})
            return redirect("core:config_doctype_detail", pk=doc_type.pk)
        context = {
            "form": form,
            "doc_type": doc_type,
            "is_edit": True,
            **report_bindings_context(doc_type, request.current_facility),
        }
        return render(request, FORM_TEMPLATE, context)


class ConfigDocumentTypeDeleteView(FacilityAdminRequiredMixin, View):
    """Löschen mit Referenz-Zähler-Vorschau; Blockade → Meldung + Redirect."""

    def get(self, request, pk):
        doc_type = get_object_or_404(DocumentType, pk=pk, facility=request.current_facility)
        event_count = Event.objects.filter(document_type=doc_type).count()
        # P9-Fix-Runde 1 (V-2, Refs #1058): Client.document_type/Case.document_type
        # sind seit M2 ebenfalls PROTECT (wie Event.document_type) — die
        # Vorab-Warnung muss dieselben Blocker zeigen wie der eigentliche
        # Löschversuch (config_service.delete_document_type), sonst sieht der
        # Dialog einen aktivierten „Endgültig löschen“-Button, obwohl der POST
        # danach mit ConfigDeleteBlocked scheitert (kein 500, aber irreführende UX).
        client_count = Client.objects.filter(document_type=doc_type).count()
        case_count = Case.objects.filter(document_type=doc_type).count()
        # Refs #1737 (A): Wieviel der Blockade steckt im Papierkorb? Ohne diese
        # Zahl verlangt der Dialog einen Schritt, den man im Fach-UI gar nicht
        # gehen kann — der Datensatz ist dort nicht mehr erreichbar.
        client_trashed_count = Client.objects.filter(document_type=doc_type, is_deleted=True).count()
        # Refs #1818: seit dem Fall-Loeschpfad blockiert auch ein Fall im
        # Papierkorb — mit demselben Sackgassen-Effekt (die Fallliste zeigt ihn
        # nicht mehr, das Detail antwortet 404).
        case_trashed_count = Case.objects.filter(document_type=doc_type, is_deleted=True).count()
        # QuickTemplate.document_type ist FK CASCADE: zugehörige Schnellvorlagen
        # werden beim Löschen still mitentfernt — die Kaskade im Dialog ausweisen.
        quick_template_count = QuickTemplate.objects.filter(document_type=doc_type).count()
        return render(
            request,
            "core/config/document_types/confirm_delete.html",
            {
                "doc_type": doc_type,
                "event_count": event_count,
                "client_count": client_count,
                "case_count": case_count,
                "client_trashed_count": client_trashed_count,
                "case_trashed_count": case_trashed_count,
                "quick_template_count": quick_template_count,
            },
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
