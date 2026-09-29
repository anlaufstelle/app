"""Config-Views für den Regel-Editor bedingter Feldsichtbarkeit (M1 P6, Refs #1057).

Dünne ``View``-CBVs für das Bedingungs-Board im DocumentType-Detail: eine
Feldbedingung anlegen, ändern, löschen sowie das typisierte Wert-Eingabe-Partial
(HTMX-GET) ausliefern. Jeder Objekt-Lookup ist zweistufig mandantengescoped —
zuerst der ``DocumentType`` über ``facility=request.current_facility`` (IDOR →
404 VOR jeder weiteren Prüfung), dann die ``FieldCondition`` über
``target_field__document_type=doc_type`` (fremde Bedingung → 404). Alle
Mutationen laufen über :mod:`core.services.config` (Audit + ``full_clean`` im
Service); ein ``ValidationError`` (Zyklus, Operator×Typ, Wert) wird als
Board-Formularfehler im HTMX-Reswap sichtbar. Antwort-Muster analog
``board_response`` (M0): bei ``HX-Request`` das frisch gerenderte Board
(Swap-Ziel ``#condition-board``), sonst Redirect zur Detailseite.
"""

import contextlib
import logging
import uuid

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views import View

from core.forms.config import FieldConditionForm
from core.models import DocumentType, DocumentTypeField, FieldCondition
from core.services import config as config_service
from core.views.config._context import (
    _operator_input_context,
    _value_for_input,
    _value_input_context,
    condition_board_context,
    condition_id_prefix,
    operator_input_context_for_form,
    prepare_condition_form,
)
from core.views.mixins import FacilityAdminRequiredMixin, htmx_absage

logger = logging.getLogger(__name__)

BOARD_TEMPLATE = "core/config/document_types/partials/condition_board.html"
FORM_TEMPLATE = "core/config/document_types/partials/condition_form.html"
VALUE_INPUT_TEMPLATE = "core/config/document_types/partials/condition_value_input.html"
OPERATOR_INPUT_TEMPLATE = "core/config/document_types/partials/condition_operator_input.html"


def condition_board_response(request, doc_type, *, form=None, retarget=False, message=None, error=None):
    """HX-Request → frisch gerendertes ``condition_board.html``, sonst Redirect zum Detail.

    ``message``/``error`` werden — wie beim Feld-Board — **nur** im Nicht-HX-Zweig
    als Flash gesetzt (im HX-Zweig würden sie verspätet beim nächsten
    Vollseiten-Load auftauchen). ``retarget=True`` ersetzt vom Inline-Editier-
    Formular (``hx-target="this"``) aus trotzdem das ganze Board.
    """
    if request.headers.get("HX-Request") == "true":
        response = render(request, BOARD_TEMPLATE, condition_board_context(doc_type, form=form))
        if retarget:
            response["HX-Retarget"] = "#condition-board"
            response["HX-Reswap"] = "outerHTML"
        # Board-Mutation stößt die Live-Formularvorschau an (Muster board_response).
        response["HX-Trigger"] = "refresh-preview"
        return response
    if message is not None:
        messages.success(request, message)
    if error is not None:
        messages.error(request, error)
    return redirect("core:config_doctype_detail", pk=doc_type.pk)


def _apply_model_errors(form, exc):
    """Überträgt einen Modell-``ValidationError`` (full_clean) auf die Board-Form.

    Der ``FieldCondition.clean()`` meldet u.a. den Zykluspfad auf
    ``depends_on_field`` — die Feldnamen decken sich mit denen des Formulars, so
    dass die Meldung an der richtigen Stelle im wieder-gerenderten Board steht.
    """
    if hasattr(exc, "error_dict"):
        for field, errors in exc.message_dict.items():
            target = field if field in form.fields else None
            for message in errors:
                form.add_error(target, message)
    else:
        form.add_error(None, exc.messages)


# ---------------------------------------------------------------------------
# Board-Refresh-View (#1718)
# ---------------------------------------------------------------------------


class ConditionBoardView(FacilityAdminRequiredMixin, View):
    """Rendert NUR das Bedingungs-Board frisch (GET) — HTMX-Refresh nach Feld-Board-Mutation.

    Der Board-Wurzel-Div (``condition_board.html``) lauscht per
    ``hx-trigger="refresh-conditions from:body"`` auf das Event, das die
    Feld-Board-Mutationen (``board_response``) jetzt zusätzlich zu
    ``refresh-preview`` feuern, und ersetzt sich per outerHTML über diesen
    Endpoint (#1718). Ohne Reload werden so Empty-State ↔ Regel-Editor-Karte und
    die Zielfeld-Dropdowns nachgezogen. Zugriffskontrolle wie die übrigen
    Condition-Views: FacilityAdmin + DocType über ``facility=request.current_facility``
    (IDOR → 404). Der Endpoint selbst setzt KEINEN HX-Trigger (kein Selbst-Refresh-Loop).
    """

    def get(self, request, pk):
        doc_type = get_object_or_404(DocumentType, pk=pk, facility=request.current_facility)
        return render(request, BOARD_TEMPLATE, condition_board_context(doc_type))


# ---------------------------------------------------------------------------
# Mutations-Views
# ---------------------------------------------------------------------------


class ConditionCreateView(FacilityAdminRequiredMixin, View):
    """Legt eine Feldbedingung für den Dokumentationstyp an (Board-Mutation).

    GET rendert das Neu-Formular für die beiden Board-Affordanzen (Plan § 5):
    ``?group_index=<n>`` (+ optional ``?target=<dtf-pk>``) belegt die verdeckte
    OR-Gruppe vor — „UND-Bedingung hinzufügen" reicht den Index der bestehenden
    Gruppe durch, „ODER-Gruppe hinzufügen" den server-vorbelegten ``max+1``.
    """

    def get(self, request, pk):
        doc_type = get_object_or_404(DocumentType, pk=pk, facility=request.current_facility)
        initial: dict = {}
        raw_group = request.GET.get("group_index")
        if raw_group is not None:
            with contextlib.suppress(ValueError, TypeError):
                initial["group_index"] = max(0, int(raw_group))
        raw_target = request.GET.get("target")
        if raw_target:
            with contextlib.suppress(ValueError, TypeError):
                initial["target_field"] = uuid.UUID(raw_target)
        form = FieldConditionForm(document_type=doc_type, initial=initial)
        prefix = condition_id_prefix()
        prepare_condition_form(form, prefix)
        return render(
            request,
            FORM_TEMPLATE,
            {
                "condition_form": form,
                "doc_type": doc_type,
                "is_edit": False,
                "condition_id_prefix": prefix,
                **operator_input_context_for_form(form, doc_type, condition_id_prefix=prefix),
            },
        )

    def post(self, request, pk):
        doc_type = get_object_or_404(DocumentType, pk=pk, facility=request.current_facility)
        form = FieldConditionForm(request.POST, document_type=doc_type)
        if not form.is_valid():
            return condition_board_response(
                request, doc_type, form=form, error=_("Die Bedingung konnte nicht angelegt werden.")
            )
        try:
            config_service.create_field_condition(
                request,
                doc_type,
                target_field=form.cleaned_data["target_field"],
                depends_on_field=form.cleaned_data["depends_on_field"],
                operator=form.cleaned_data["operator"],
                value=form.cleaned_data.get("value_json"),
                group_index=form.cleaned_data.get("group_index") or 0,
            )
        except ValidationError as exc:
            _apply_model_errors(form, exc)
            return condition_board_response(
                request, doc_type, form=form, error=_("Die Bedingung konnte nicht angelegt werden.")
            )
        return condition_board_response(request, doc_type, message=_("Bedingung wurde angelegt."))


class ConditionUpdateView(FacilityAdminRequiredMixin, View):
    """Bearbeitet eine bestehende Feldbedingung (GET = Formular, POST = speichern)."""

    def get(self, request, pk, condition_pk):
        doc_type = get_object_or_404(DocumentType, pk=pk, facility=request.current_facility)
        condition = get_object_or_404(FieldCondition, pk=condition_pk, target_field__document_type=doc_type)
        current_value = _value_for_input(condition.value)
        form = FieldConditionForm(
            document_type=doc_type,
            initial={
                "target_field": condition.target_field_id,
                "depends_on_field": condition.depends_on_field_id,
                "operator": condition.operator,
                "value": current_value,
                "group_index": condition.group_index,
            },
        )
        prefix = condition_id_prefix(condition=condition)
        prepare_condition_form(form, prefix)
        # Value-Slot-Kontext mitgeben → der Edit-Prefill rendert das TYPISIERTE Input
        # (number→type=number etc.) statt des generischen Textfelds (Minor 2).
        context = {
            "condition_form": form,
            "doc_type": doc_type,
            "condition": condition,
            "is_edit": True,
            "condition_id_prefix": prefix,
            **_value_input_context(
                condition.depends_on_field.field_template,
                condition.operator,
                current_value,
                condition_id_prefix=prefix,
            ),
            # #1715: Operator-Slot typgefiltert vorbefüllt (Quelle aus dem Prefill bekannt).
            **operator_input_context_for_form(form, doc_type, condition_id_prefix=prefix),
        }
        return render(request, FORM_TEMPLATE, context)

    def post(self, request, pk, condition_pk):
        doc_type = get_object_or_404(DocumentType, pk=pk, facility=request.current_facility)
        condition = get_object_or_404(FieldCondition, pk=condition_pk, target_field__document_type=doc_type)
        form = FieldConditionForm(request.POST, document_type=doc_type)
        if not form.is_valid():
            return self._error_response(request, doc_type, condition, form)
        try:
            config_service.update_field_condition(
                request,
                condition,
                target_field=form.cleaned_data["target_field"],
                depends_on_field=form.cleaned_data["depends_on_field"],
                operator=form.cleaned_data["operator"],
                value=form.cleaned_data.get("value_json"),
                group_index=form.cleaned_data.get("group_index") or 0,
            )
        except ValidationError as exc:
            _apply_model_errors(form, exc)
            return self._error_response(request, doc_type, condition, form)
        return condition_board_response(request, doc_type, retarget=True, message=_("Bedingung wurde gespeichert."))

    def _error_response(self, request, doc_type, condition, form):
        # Das Editier-Formular postet per hx-target="this": Fehler ersetzen es an
        # Ort und Stelle (kein Flash — er stünde im Store), sonst Redirect.
        if request.headers.get("HX-Request") == "true":
            prefix = condition_id_prefix(condition=condition)
            prepare_condition_form(form, prefix)
            return render(
                request,
                FORM_TEMPLATE,
                {
                    "condition_form": form,
                    "doc_type": doc_type,
                    "condition": condition,
                    "is_edit": True,
                    "condition_id_prefix": prefix,
                    **operator_input_context_for_form(form, doc_type, condition_id_prefix=prefix),
                },
            )
        return condition_board_response(request, doc_type, error=_("Die Bedingung konnte nicht gespeichert werden."))


class ConditionDeleteView(FacilityAdminRequiredMixin, View):
    """Löscht eine Feldbedingung (Board-Mutation)."""

    def post(self, request, pk, condition_pk):
        doc_type = get_object_or_404(DocumentType, pk=pk, facility=request.current_facility)
        condition = get_object_or_404(FieldCondition, pk=condition_pk, target_field__document_type=doc_type)
        config_service.delete_field_condition(request, condition)
        return condition_board_response(request, doc_type, message=_("Bedingung wurde gelöscht."))


def _resolve_source_dtf(request, pk):
    """Löst ``DocumentType`` + Quell-``DocumentTypeField`` + Operator aus der Query.

    Gemeinsamer Ablauf von ``ConditionValueInputView.get`` und
    ``ConditionOperatorInputView.get`` (#1715/E19): ``DocumentType`` per
    ``pk``+facility (IDOR → 404), dann ``depends_on_field``/``source``-Alias
    als Quell-DTF-UUID, 400 bei fehlendem/ungültigem Wert. Rückgabe entweder
    ``(doc_type, dtf, operator)`` oder eine fertige HTMX-Absage (400 +
    ``HX-Reswap: none``, Refs #2385: beide Views swappen in einen Slot, ein
    roher Klartext ersetzte ihn sonst) — die Aufrufstelle unterscheidet per
    ``isinstance``.
    """
    doc_type = get_object_or_404(DocumentType, pk=pk, facility=request.current_facility)
    source_raw = request.GET.get("depends_on_field") or request.GET.get("source") or ""
    operator = request.GET.get("operator", "")
    if not source_raw:
        return htmx_absage(_("Kein Quellfeld angegeben."), status=400)
    try:
        source_uuid = uuid.UUID(source_raw)
    except (ValueError, TypeError):
        return htmx_absage(_("Ungültige Quellfeld-ID."), status=400)
    dtf = get_object_or_404(DocumentTypeField, pk=source_uuid, document_type=doc_type)
    return doc_type, dtf, operator


class ConditionValueInputView(FacilityAdminRequiredMixin, View):
    """Liefert das typisierte Wert-Eingabe-Partial (E19) — HTMX-GET.

    Parameter: ``depends_on_field`` (Quell-DTF-pk; ``source`` als Alias) und
    ``operator``. Rendert je Quelltyp+Operator das passende Eingabefeld
    (select→Options-Dropdown aus ``FieldTemplate.choices``, boolean→Ja/Nein,
    number/date/time→typisierte Inputs, ``VALUELESS_OPERATORS``→kein Input).
    Ungültige/fehlende Parameter → 400.
    """

    def get(self, request, pk):
        result = _resolve_source_dtf(request, pk)
        if isinstance(result, HttpResponse):
            return result
        _doc_type, dtf, operator = result
        return render(
            request,
            VALUE_INPUT_TEMPLATE,
            _value_input_context(
                dtf.field_template,
                operator,
                request.GET.get("value", ""),
                condition_id_prefix=condition_id_prefix(request.GET.get("condition_id_prefix")),
            ),
        )


class ConditionOperatorInputView(FacilityAdminRequiredMixin, View):
    """Liefert das typabhängige Operator-Slot-Partial (#1715) — HTMX-GET.

    Analog ``ConditionValueInputView`` (``source``-Alias, 400 bei fehlender/
    ungültiger Quelle). Parameter: ``depends_on_field`` (Quell-DTF-pk) und
    ``operator`` (aktuelle Auswahl). Rendert nur die für den Quelltyp zulässigen
    Operatoren; das Partial trägt ``operator_autoload=True`` → nach dem Swap zieht
    ``load`` automatisch den Value-Slot nach (Kette Quelle→Operator→Value).
    """

    def get(self, request, pk):
        result = _resolve_source_dtf(request, pk)
        if isinstance(result, HttpResponse):
            return result
        doc_type, dtf, operator = result
        return render(
            request,
            OPERATOR_INPUT_TEMPLATE,
            _operator_input_context(
                dtf.field_template,
                operator,
                doc_type,
                autoload=True,
                condition_id_prefix=condition_id_prefix(request.GET.get("condition_id_prefix")),
            ),
        )
