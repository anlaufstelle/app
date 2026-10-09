"""Paginated contacts metadata list."""

from urllib.parse import urlencode

from django.views import View

from core.services.events.list_filters import event_list_options, event_list_queryset, resolve_event_list_filters
from core.views.mixins import AssistantOrAboveRequiredMixin, HTMXPartialMixin, PaginatedListMixin


class EventListView(AssistantOrAboveRequiredMixin, PaginatedListMixin, HTMXPartialMixin, View):
    """Show the same event visibility as the Zeitstrom without access audits."""

    template_name = "core/events/list.html"
    partial_template_name = "core/events/partials/table.html"

    def get(self, request):
        facility = request.current_facility
        options = {key: list(values) for key, values in event_list_options(facility, request.user).items()}
        filters = resolve_event_list_filters(request.GET, facility, request.user, options=options)
        events = self.paginate(event_list_queryset(facility, request.user, filters, options=options), request)
        return self.render_htmx_or_full(
            {
                **options,
                "events": events,
                "filters": filters,
                "show_cases": request.user.is_staff_or_above,
                "pagination_params": urlencode(filters),
            }
        )
