"""Paginated contacts with role-visible timeline contents."""

from urllib.parse import urlencode

from django.views import View

from core.services.events.list_filters import (
    event_list_options,
    event_list_page_contents,
    event_list_queryset,
    resolve_event_list_filters,
)
from core.views.mixins import AssistantOrAboveRequiredMixin, HTMXPartialMixin, PaginatedListMixin


class EventListView(AssistantOrAboveRequiredMixin, PaginatedListMixin, HTMXPartialMixin, View):
    """Show the same content visibility as the Zeitstrom; no access audit."""

    template_name = "core/events/list.html"
    partial_template_name = "core/events/partials/table.html"

    def is_htmx(self):
        """A history restore swaps the response into the body, so it needs the full page."""
        return super().is_htmx() and self.request.headers.get("HX-History-Restore-Request") != "true"

    def get(self, request):
        facility = request.current_facility
        options = {key: list(values) for key, values in event_list_options(facility, request.user).items()}
        filters = resolve_event_list_filters(request.GET, facility, request.user, options=options)
        events = self.paginate(event_list_queryset(facility, request.user, filters, options=options), request)
        events.object_list = event_list_page_contents(facility, request.user, events.object_list)
        return self.render_htmx_or_full(
            {
                **options,
                "events": events,
                "filters": filters,
                "show_cases": request.user.is_staff_or_above,
                "pagination_params": urlencode(filters),
                "filter_form_oob": self.is_htmx(),
            }
        )
