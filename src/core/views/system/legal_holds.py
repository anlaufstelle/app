"""Cross-Facility-Legal-Hold-Uebersicht fuer super_admin (Refs #877)."""

from urllib.parse import urlencode

from django.views.generic import TemplateView

from core.constants import DEFAULT_PAGE_SIZE
from core.models import Facility
from core.models.retention import LegalHold
from core.views.mixins import PaginatedListMixin
from core.views.system.mixins import SystemAuditMixin


class SystemLegalHoldListView(SystemAuditMixin, PaginatedListMixin, TemplateView):
    """Cross-Facility-Liste der ``LegalHold``-Eintraege.

    Filter:
    - ``facility``: Dropdown mit allen Einrichtungen.
    - ``status``: ``active`` (``dismissed_at IS NULL``) / ``dismissed``
      (``dismissed_at IS NOT NULL``).

    Sortierung: ``created_at DESC`` (Default). Pagination ueber
    ``PaginatedListMixin``.
    """

    template_name = "core/system/legal_holds.html"
    page_size = DEFAULT_PAGE_SIZE

    # Wir bauen GET-basiert — ein direktes ``get_context_data`` ohne
    # eigene ``get``-Methode reicht, da ``request`` ueber ``self.request``
    # erreichbar ist und der ``PaginatedListMixin`` per ``self.paginate``
    # damit umgeht.
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        request = self.request

        queryset = (
            LegalHold.objects.all().select_related("facility", "created_by", "dismissed_by").order_by("-created_at")
        )

        # Filter: facility
        facility_id = request.GET.get("facility", "")
        if facility_id:
            queryset = queryset.filter(facility_id=facility_id)

        # Filter: status (active/dismissed)
        status = request.GET.get("status", "")
        if status == "active":
            queryset = queryset.filter(dismissed_at__isnull=True)
        elif status == "dismissed":
            queryset = queryset.filter(dismissed_at__isnull=False)

        page = self.paginate(queryset, request)

        all_facilities = Facility.objects.order_by("name")

        pagination_params = urlencode({k: v for k, v in [("facility", facility_id), ("status", status)] if v})

        context.update(
            {
                "page_obj": page,
                "all_facilities": all_facilities,
                "filter_facility": facility_id,
                "filter_status": status,
                "pagination_params": pagination_params,
            }
        )
        return context
