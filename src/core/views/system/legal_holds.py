"""Cross-Facility-Legal-Hold-Uebersicht fuer super_admin (Refs #877)."""

from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.http import require_http_methods
from django.views.generic import TemplateView
from django_ratelimit import UNSAFE

from core.constants import DEFAULT_PAGE_SIZE, RATELIMIT_MUTATION
from core.models import Facility
from core.models.retention import LegalHold
from core.retention.legal_holds import dismiss_legal_hold
from core.utils.ratelimit import ratelimit
from core.views.mixins import PaginatedListMixin
from core.views.system.mixins import SystemAuditMixin


@method_decorator(require_http_methods(["GET", "HEAD", "OPTIONS"]), name="dispatch")
class SystemLegalHoldListView(SystemAuditMixin, PaginatedListMixin, TemplateView):
    """List holds across facilities, newest first, with pagination.

    The active filter excludes dismissed and expired holds. The dismissed
    filter shows completed dismissals independently of their expiry date.
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
            queryset = queryset.filter(dismissed_at__isnull=True).exclude(expires_at__lt=timezone.localdate())
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


@method_decorator(ratelimit(key="user", rate=RATELIMIT_MUTATION, method=UNSAFE, block=True), name="dispatch")
class SystemLegalHoldDismissView(SystemAuditMixin, View):
    """Dismiss an explicitly selected hold as the installation administrator."""

    http_method_names = ["post"]

    def post(self, request, pk):
        hold = get_object_or_404(LegalHold.objects.select_related("facility"), pk=pk)
        try:
            dismiss_legal_hold(hold, request.user)
        except ValidationError as exc:
            return HttpResponse(exc.messages[0], status=409)
        messages.success(request, _("Legal Hold wurde aufgehoben."))
        return redirect("core:system_legal_hold_list")
