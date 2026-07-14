"""Zentraler Helfer fuer facility-gescopte Einzelobjekt-Lookups (Refs #1346).

Vor diesem Modul war ``get_object_or_404(Model, pk=pk, facility=request.current_facility)``
~40x copy-paste ueber die View-Schicht verteilt (~13 Dateien). Ein einzelnes
vergessenes ``facility=``-Kwarg ist ein Cross-Facility-IDOR, das heute nur
noch durch Postgres-RLS aufgefangen wuerde — und RLS wird bei Dev/Test/Cron/
Superadmin bewusst umgangen (siehe ADR-005). ``get_scoped_object`` macht das
Facility-Filter strukturell unvergesslich: es wird bei jedem Aufruf zwingend
angewendet, nicht optional per Aufrufer-Kwarg.

Event ist bewusst ausgeschlossen: Events tragen zusaetzlich eine
DocumentType-Sensitivitaets-Policy (Rolle darf ein Event trotz korrektem
Facility-Scope nicht sehen), die dieser Helfer nicht kennt. Der dedizierte
Loader ``core.services.compliance.sensitivity.get_visible_event_or_404``
bleibt die einzige Stelle fuer Event-Einzel-Lookups — analog abgesichert
durch ``TestEventAccessPolicyGuard``.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from django.shortcuts import get_object_or_404


def get_scoped_object(model_or_qs: Any, request: HttpRequest, **kwargs: Any) -> Any:
    """Laedt genau ein Objekt, hart gescoped auf ``request.current_facility``.

    ``model_or_qs`` darf eine Model-Klasse ODER ein bereits vorbereitetes
    QuerySet/Manager sein (z.B. mit ``select_related``/``select_for_update``-
    Ketten) — exakt wie beim darunterliegenden ``get_object_or_404`` selbst.
    Zusaetzliche ``kwargs`` (z.B. ``pk=pk``, ``is_deleted=False``) werden
    unveraendert durchgereicht.

    Wirft:
    - ``Http404`` bei fehlendem oder facility-fremdem Objekt (Django-
      Standardverhalten von ``get_object_or_404`` — die Mandantengrenze wird
      damit von aussen ununterscheidbar von "existiert nicht").
    - ``ValueError``, wenn ``model_or_qs`` (transitiv) das ``Event``-Model
      ist, oder wenn ``request.current_facility`` fehlt — ein fehlendes
      Facility darf NIE still zu einem ungescopten Lookup fuehren.
    """
    # Lokaler Import: core.models importiert seinerseits core.services.*
    # Submodule an mehreren Stellen, ein Modul-Top-Level-Import wuerde einen
    # Zirkelimport riskieren.
    from core.models import Event

    model = model_or_qs if isinstance(model_or_qs, type) else model_or_qs.model
    if model is Event:
        raise ValueError(
            "get_scoped_object() darf nicht fuer Event verwendet werden — Events tragen "
            "eine Sensitivitaets-Policy zusaetzlich zum Facility-Scope. Bitte "
            "core.services.compliance.sensitivity.get_visible_event_or_404 nutzen."
        )

    facility = getattr(request, "current_facility", None)
    if not facility:
        raise ValueError(
            "get_scoped_object() ohne request.current_facility aufgerufen — ein ungescopter "
            "Lookup waere ein Cross-Facility-IDOR. Sicherstellen, dass die "
            "FacilityScopeMiddleware gelaufen ist und der aktuelle User einer Facility "
            "zugeordnet ist."
        )

    return get_object_or_404(model_or_qs, facility=facility, **kwargs)
