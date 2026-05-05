"""Role-based access mixins."""

import logging

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin

logger = logging.getLogger(__name__)


class AssistantOrAboveRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Access for all authenticated roles (Assistant, Staff, Lead, Admin)."""

    def test_func(self):
        return self.request.user.is_assistant_or_above


class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Access for Staff, Lead and Admin only."""

    def test_func(self):
        return self.request.user.is_staff_or_above


class LeadOrAdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Access for Lead and Admin only."""

    def test_func(self):
        return self.request.user.is_lead_or_admin


class AdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Access for Admin only."""

    def test_func(self):
        return self.request.user.is_admin


# --- Facility/HTMX Helper-Mixins (Refs #598 R-2/R-3, #745) --------------
#
# ``FacilityScopedViewMixin`` ist als Convenience für neue Views gedacht,
# eine systematische Migration der ~87 ``request.current_facility``-Sites
# wäre kosmetisch.
#
# ``HTMXPartialMixin`` ist seit #745 in allen Listen-Views mit echtem
# Partial/Full-Branching umgesetzt; verbleibende ``HX-Request``-Checks im
# Code sind bewusste Sonderfälle (s. Docstring von ``HTMXPartialMixin``).


class FacilityScopedViewMixin:
    """Stellt ``self.facility`` als Alias für ``request.current_facility``
    bereit. Keine zusätzliche Guard-Logik — Anonymous-Schutz läuft über
    ``LoginRequiredMixin`` bzw. die Role-Mixins oben, User-ohne-Facility-
    Edge-Cases regelt die jeweilige View selbst.

    Nutzen: Views, die die Facility in ``get``, ``post`` und
    ``get_context_data`` brauchen, sparen sich die wiederholte
    ``request.current_facility``-Lookup. Eine Zeile weniger pro Methode.
    """

    @property
    def facility(self):
        return self.request.current_facility


class PaginatedListMixin:
    """Refs #803 (C-36): Eine Stelle fuer ``Paginator(qs, N).get_page(...)``.

    Vorher haben drei Listen-Views (clients, cases, audit) den Paginator
    direkt gebaut, mit unterschiedlichen Page-Sizes (25 vs. 50) und
    teilweise inline-importiertem ``safe_page_param``. Mit dem Mixin
    haengt die Page-Size am View, der Paginator wird einmal gebaut und
    durch ``safe_page_param`` gegen ``page=999999``-Angriffe geschuetzt.
    """

    page_size: int | None = None

    def paginate(self, queryset, request):
        from django.core.paginator import Paginator

        from core.constants import DEFAULT_PAGE_SIZE
        from core.views.utils import safe_page_param

        size = self.page_size if self.page_size is not None else DEFAULT_PAGE_SIZE
        return Paginator(queryset, size).get_page(safe_page_param(request))


class HTMXPartialMixin:
    """Rendert je nach ``HX-Request``-Header ein Partial- oder Full-Page-
    Template. Erwartet ``partial_template_name`` und ``template_name`` als
    Klassenattribute.

    Nur für den einfachen Render-Branch ("ein Partial vs. ein Full-Page-
    Template"). Folgende Fälle bleiben bewusst inline und nutzen den
    Mixin **nicht** — die Sonderfälle nicht versehentlich migrieren:

    * **Bulk-Actions mit ``HX-Redirect``-Response** (z. B.
      ``RetentionBulkApproveView``, ``WorkItemBulkStatusView``): der HTMX-
      Pfad antwortet mit ``HX-Redirect``-Header statt einem Partial.
    * **API-Format-Negotiation** zwischen JSON und HTML (z. B.
      ``_wants_json_response`` in :file:`views/events.py`): hier ist die
      Abzweigung kein Render-Branch, sondern ein Format-Branch.
    * **Mehrere Partials pro View** oder bedingtes Rendern abhängig vom
      Request-Pfad: bleibt explizit, weil der Mixin nur ein einziges
      Partial-Template kennt.
    """

    template_name = None
    partial_template_name = None

    def is_htmx(self):
        return self.request.headers.get("HX-Request") == "true"

    def render_htmx_or_full(self, context):
        from django.shortcuts import render

        template = self.partial_template_name if self.is_htmx() else self.template_name
        if template is None:
            raise ValueError("HTMXPartialMixin erfordert partial_template_name und template_name.")
        return render(self.request, template, context)
