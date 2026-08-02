"""Batch-Reorder-Views für das interaktive Feld-Board (M0 P7, Refs #1056).

Drei dünne POST-``View``-CBVs für die Drag-and-Drop-Sortierung, jeweils als
gebündelter Batch-Endpunkt (nicht ein Request pro verschobenem Element):

* :class:`ConfigReorderGroupsView`  — Gruppen-Reihenfolge.
* :class:`ConfigReorderFieldsView`  — Feld-Reihenfolge innerhalb einer Gruppe
  (oder der „Ohne Gruppe"-Menge).
* :class:`ConfigMoveFieldView`      — ein Feld in eine (andere) Gruppe schieben.

Ablauf jeder View (bewusst in dieser Reihenfolge):

1. ``get_object_or_404(DocumentType, pk=…, facility=request.current_facility)`` —
   IDOR → 404 **vor** jeder Payload-Auswertung (fremder/nicht existenter
   DocType leakt nichts).
2. Payload (form-encoded) lesen und die UUIDs parsen — ein kaputter Wert wird
   als :class:`ValueError` gefangen und zu HTTP 400 (kein 500).
3. Service aufrufen (:mod:`core.services.config`) — dessen strikte
   ID-Set-Validierung wirft bei fremden/fehlenden/doppelten IDs ``ValueError``,
   das hier ebenfalls zu HTTP 400 wird; die DB bleibt dann unverändert.
4. Erfolg → frisch gerendertes ``field_board``-Partial über den P6-Helper
   :func:`~core.views.config.document_types.board_response` (HX-Request → 200
   Partial, sonst Redirect zum Detail).

Ein leerer Payload (keine ``order``-Werte bzw. kein ``moved``) ist ein bewusster
HTTP 400 — es gibt nichts umzusortieren; die AuthZ-Matrix erlaubt für berechtigte
Rollen jeden Status außer 5xx/403/404, verbotene Rollen scheitern schon am
``FacilityAdminRequiredMixin`` (403).
"""

import logging
import uuid

from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from django.views import View

from core.models import DocumentType
from core.services import config as config_service
from core.views.config.document_types import board_response
from core.views.mixins import FacilityAdminRequiredMixin

logger = logging.getLogger(__name__)


def _parse_uuids(raw_values) -> list[uuid.UUID]:
    """POST-Liste von UUID-Strings → Liste von ``uuid.UUID``.

    Ein kaputter Wert wirft ``ValueError`` (der View macht daraus HTTP 400).
    Die kanonisierten ``UUID``-Objekte matchen im Service verlässlich gegen die
    ``uuid.UUID``-Werte aus der DB (str-Vergleich in ``_validate_id_set``).
    """
    return [uuid.UUID(str(value)) for value in raw_values]


def _parse_optional_group(raw) -> uuid.UUID | None:
    """Leerer/fehlender ``group``-Wert → ``None`` („Ohne Gruppe"), sonst ``UUID``.

    Ein kaputter, nicht-leerer Wert wirft ``ValueError`` → HTTP 400.
    """
    if not raw:
        return None
    return uuid.UUID(raw)


class ConfigReorderGroupsView(FacilityAdminRequiredMixin, View):
    """Setzt die Reihenfolge der Feldgruppen eines DocumentType neu (Batch)."""

    def post(self, request, pk):
        doc_type = get_object_or_404(DocumentType, pk=pk, facility=request.current_facility)
        try:
            ordered_ids = _parse_uuids(request.POST.getlist("order"))
        except ValueError:
            return HttpResponseBadRequest(_("Ungültige Gruppen-ID im Payload."))
        if not ordered_ids:
            return HttpResponseBadRequest(_("Keine Reihenfolge übergeben."))
        try:
            config_service.reorder_groups(request, doc_type, ordered_ids)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        return board_response(request, doc_type)


class ConfigReorderFieldsView(FacilityAdminRequiredMixin, View):
    """Setzt die Feld-Reihenfolge innerhalb einer Gruppe/„Ohne Gruppe" neu (Batch)."""

    def post(self, request, pk):
        doc_type = get_object_or_404(DocumentType, pk=pk, facility=request.current_facility)
        try:
            group_id = _parse_optional_group(request.POST.get("group"))
            ordered_ids = _parse_uuids(request.POST.getlist("order"))
        except ValueError:
            return HttpResponseBadRequest(_("Ungültige ID im Payload."))
        if not ordered_ids:
            return HttpResponseBadRequest(_("Keine Reihenfolge übergeben."))
        try:
            config_service.reorder_fields(request, doc_type, group_id, ordered_ids)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        return board_response(request, doc_type)


class ConfigMoveFieldView(FacilityAdminRequiredMixin, View):
    """Verschiebt ein Feld in eine (andere) Gruppe und ordnet die Zielgruppe neu."""

    def post(self, request, pk):
        doc_type = get_object_or_404(DocumentType, pk=pk, facility=request.current_facility)
        moved_raw = request.POST.get("moved")
        if not moved_raw:
            return HttpResponseBadRequest(_("Kein zu verschiebendes Feld übergeben."))
        try:
            moved_id = uuid.UUID(moved_raw)
            target_group_id = _parse_optional_group(request.POST.get("group"))
            ordered_ids = _parse_uuids(request.POST.getlist("order"))
        except ValueError:
            return HttpResponseBadRequest(_("Ungültige ID im Payload."))
        if not ordered_ids:
            return HttpResponseBadRequest(_("Keine Reihenfolge übergeben."))
        try:
            config_service.move_field(request, doc_type, moved_id, target_group_id, ordered_ids)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        return board_response(request, doc_type)
