"""Views for search."""

import logging
from urllib.parse import urlencode

from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View

from core.services.dashboard import (
    MIN_QUERY_LENGTH,
    client_search_queryset,
    event_search_queryset,
    search_clients_and_events,
    search_result_counts,
    search_similar_clients,
)
from core.utils.ratelimit import ratelimit
from core.views.mixins import AssistantOrAboveRequiredMixin, HTMXPartialMixin, PaginatedListMixin

logger = logging.getLogger(__name__)

#: Refs #1803 (Konzept §3.5): Die Uebersicht ist eine Uebersicht — zehn Zeilen
#: je Gruppe, darunter der Sprung in die vollstaendige, paginierte Liste. Die
#: Zahl in der Ueberschrift bleibt die Gesamtzahl (Regel L7).
OVERVIEW_GROUP_SIZE = 10

#: Werteraum von ``?type=``. Alles andere faellt auf die Uebersicht zurueck —
#: fail-closed auf den harmlosen Zustand statt auf einen Fehler.
RESULT_TYPES = ("person", "event")


class SearchView(AssistantOrAboveRequiredMixin, PaginatedListMixin, HTMXPartialMixin, View):
    """Search across clients and events.

    Refs #1803 (Konzept §3.4–§3.7): Die Seite war eine gekappte Vorschau, die
    sich als vollstaendige Liste ausgab — ``search_clients_and_events`` lief in
    die Defaults 20/20/10, die Gruppenueberschrift zeigte die Kappe als waere
    sie die Gesamtzahl, und weder Typ-Filter noch Paginierung existierten.

    Der Suchzustand steht jetzt vollstaendig in der Adresse (Regel L5):
    ``?q=<begriff>[&type=person|event][&page=<n>]``. ``type`` leer heisst
    Gruppenuebersicht, gesetzt heisst eine flache, paginierte Liste.
    """

    template_name = "core/search/index.html"
    partial_template_name = "core/search/partials/results.html"

    @method_decorator(ratelimit(key="user", rate="30/m", method="GET", block=True))
    def get(self, request):
        q = request.GET.get("q", "").strip()
        result_type = request.GET.get("type", "")
        if result_type not in RESULT_TYPES:
            result_type = ""
        facility = request.current_facility
        too_short = bool(q) and len(q) < MIN_QUERY_LENGTH

        context = {
            "q": q,
            "result_type": result_type,
            "too_short": too_short,
            "counts": {"clients": 0, "events": 0},
            "clients": [],
            "events": [],
            "similar_clients": [],
            "page": None,
            "has_results": False,
            "show_client_group": False,
            "show_event_group": False,
            "overview_group_size": OVERVIEW_GROUP_SIZE,
            "pagination_params": "",
        }

        if q and not too_short:
            context.update(self._results(request, facility, q, result_type))

        return self.render_htmx_or_full(context)

    def _results(self, request, facility, q, result_type):
        counts = search_result_counts(facility, request.user, q)
        similar_clients = []

        if result_type == "person":
            page = self.paginate(client_search_queryset(facility, q), request)
            clients, events = list(page.object_list), []
        elif result_type == "event":
            page = self.paginate(event_search_queryset(facility, request.user, q), request)
            clients, events = [], list(page.object_list)
        else:
            page = None
            clients, events = search_clients_and_events(
                facility,
                request.user,
                q,
                max_clients=OVERVIEW_GROUP_SIZE,
                max_events=OVERVIEW_GROUP_SIZE,
            )
            # Fuzzy ist ein Hinweis, kein Ergebnisraum (Konzept §3.5): der
            # Block erscheint nur in der Uebersicht und nur, solange die
            # exakte Trefferzahl die Gruppe nicht ohnehin fuellt.
            if counts["clients"] < OVERVIEW_GROUP_SIZE:
                similar_clients = search_similar_clients(
                    facility, q, exclude_pks={c.pk for c in clients}, max_results=OVERVIEW_GROUP_SIZE
                )

        return {
            "counts": counts,
            "clients": clients,
            "events": events,
            "similar_clients": similar_clients,
            "page": page,
            "has_results": bool(counts["clients"] or counts["events"] or similar_clients),
            "show_client_group": result_type != "event" and bool(counts["clients"] or similar_clients),
            "show_event_group": result_type != "person" and bool(counts["events"]),
            "pagination_params": urlencode({k: v for k, v in (("q", q), ("type", result_type)) if v}),
        }


class GlobalSearchPartialView(AssistantOrAboveRequiredMixin, View):
    """HTMX partial: compact search results for the global search dropdown.

    Refs #1803: ``MIN_QUERY_LENGTH`` galt hier bisher nur fuer die Fusszeile
    und den Fuzzy-Zweig. Bei einem einzelnen Zeichen lief der volle
    ``icontains``-Scan weiter und das Dropdown zeigte dessen Treffer — direkt
    ueber der Zeile „Bitte mindestens 2 Zeichen eingeben.". Unterhalb der
    Schwelle wird deshalb gar nicht gesucht: dieselbe Grenze wie auf der
    Vollseite (``SearchView.too_short``), kein Tabellenscan, kein
    Ratelimit-Budget fuer Treffer, die der beworbene Sprung ins ``/search/``
    ohnehin nicht wiederholen wuerde.
    """

    @method_decorator(ratelimit(key="user", rate="30/m", method="GET", block=True))
    def get(self, request):
        q = request.GET.get("q", "").strip()
        facility = request.current_facility
        too_short = bool(q) and len(q) < MIN_QUERY_LENGTH

        clients, events, similar_clients = [], [], []
        if q and not too_short:
            clients, events = search_clients_and_events(facility, request.user, q, max_clients=5, max_events=5)
            similar_clients = search_similar_clients(facility, q, exclude_pks={c.pk for c in clients}, max_results=5)

        return render(
            request,
            "core/search/partials/global_results.html",
            {
                "q": q,
                "too_short": too_short,
                "clients": clients,
                "events": events,
                "similar_clients": similar_clients,
            },
        )
