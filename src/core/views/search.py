"""Views for search."""

import logging

from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.services.search import search_clients_and_events, search_similar_clients
from core.views.mixins import AssistantOrAboveRequiredMixin

logger = logging.getLogger(__name__)


class SearchView(AssistantOrAboveRequiredMixin, View):
    """Search across clients and events."""

    @method_decorator(ratelimit(key="user", rate="30/m", method="GET", block=True))
    def get(self, request):
        q = request.GET.get("q", "").strip()
        facility = request.current_facility

        clients, events = search_clients_and_events(facility, request.user, q)
        similar_clients = search_similar_clients(facility, q, exclude_pks={c.pk for c in clients}, max_results=10)

        context = {
            "q": q,
            "clients": clients,
            "events": events,
            "similar_clients": similar_clients,
            "has_results": bool(clients or events or similar_clients),
        }

        if request.headers.get("HX-Request"):
            return render(request, "core/search/partials/results.html", context)
        return render(request, "core/search/index.html", context)


class GlobalSearchPartialView(AssistantOrAboveRequiredMixin, View):
    """HTMX partial: compact search results for the global search dropdown."""

    @method_decorator(ratelimit(key="user", rate="30/m", method="GET", block=True))
    def get(self, request):
        q = request.GET.get("q", "").strip()
        facility = request.current_facility

        clients, events = search_clients_and_events(facility, request.user, q, max_clients=5, max_events=5)
        similar_clients = search_similar_clients(facility, q, exclude_pks={c.pk for c in clients}, max_results=5)

        return render(
            request,
            "core/search/partials/global_results.html",
            {"q": q, "clients": clients, "events": events, "similar_clients": similar_clients},
        )
