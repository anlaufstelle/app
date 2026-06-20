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


class DeletionConfirmerRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Access for holders of the right „Löschbestätigung" (Refs #1053).

    Der Vier-Augen-Genehmiger-Pool wird über ``can_confirm_deletion``
    kuratiert statt aus der Rolle abgeleitet — löst den Deadlock bei
    einer einzelnen Leitung ohne erreichbare Anwendungsbetreuung.
    """

    def test_func(self):
        return self.request.user.can_confirm_deletion


class DeletionRequestListAccessMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Liste der Löschanträge: Leitung/Admin (Transparenz über eigene
    Anträge) sowie Träger des Rechts „Löschbestätigung" (Refs #1053)."""

    def test_func(self):
        user = self.request.user
        return user.is_lead_or_admin or user.can_confirm_deletion


class FacilityAdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Access for Anwendungsbetreuung (facility_admin) only.

    Refs #867: bisheriger ``AdminRequiredMixin`` umbenannt — die Rolle ist
    auf eine Einrichtung beschraenkt. Fuer installations-weiten Zugriff
    siehe ``SuperAdminRequiredMixin``.
    """

    def test_func(self):
        return self.request.user.is_facility_admin


class SuperAdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Access for Systemadministration (super_admin) only.

    Refs #867: oberste Rolle, installations-weit, kein Facility-Bezug.
    Persona Jonas (hostet die Installation). Wird fuer den
    ``/system/``-Bereich verwendet.
    """

    def test_func(self):
        return self.request.user.is_super_admin


# --- HTMX Helper-Mixin (Refs #745) --------------------------------------
#
# ``HTMXPartialMixin`` ist seit #745 in allen Listen-Views mit echtem
# Partial/Full-Branching umgesetzt; verbleibende ``HX-Request``-Checks im
# Code sind bewusste Sonderfälle (s. Docstring von ``HTMXPartialMixin``).


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


class FilteredPaginatedListMixin(PaginatedListMixin):
    """Refs #1164 (R5): Buendelt die q-Suche-, Equality-Filter- und
    ``pagination_params``-Boilerplate der Listen-Views (clients, cases).

    Drei kleine, opt-in Helfer — bewusst *kein* generisches Filter-
    Framework (YAGNI). Die View baut weiterhin ihr Basis-Queryset,
    ``annotate``/``select_related``/``order_by`` und den Context selbst;
    der Mixin uebernimmt nur das wiederholte Muster:

    * ``search_fields`` — Liste von Modellfeld-Lookups (z. B.
      ``["pseudonym"]``). ``apply_search`` filtert per ``__icontains``-
      OR ueber alle Felder, wenn ``?q=`` (getrimmt) nicht leer ist.
    * ``filter_fields`` — Mapping ``GET-Param -> Modellfeld`` (z. B.
      ``{"stage": "contact_stage"}``). ``apply_filters`` haengt fuer
      jeden gesetzten Param ein ``.filter(feld=wert)`` an.
    * ``pagination_params`` — baut den Querystring fuer Paginierungs-
      Links aus genau den Roh-GET-Werten neu, die auch gefiltert
      wurden (leere weggelassen). ``extra_params`` erlaubt View-eigene
      Zusatzparameter (z. B. Audit-Datumsfelder), ohne den Mixin zu
      generalisieren.

    Felder mit Sonderlogik (Datums-Parsing, Sentinels, ``__in``-Listen)
    bleiben in der View — dafuer ``extra_params`` bzw. eigener Code.
    """

    search_fields: list[str] = []
    filter_fields: dict[str, str] = {}

    def get_search_term(self, request) -> str:
        """Den getrimmten ``?q=``-Wert (oder ``""``)."""
        return request.GET.get("q", "").strip()

    def apply_search(self, queryset, request):
        """``__icontains``-OR ueber ``search_fields``, falls ``?q=`` gesetzt."""
        q = self.get_search_term(request)
        if q and self.search_fields:
            from django.db.models import Q

            condition = Q()
            for field in self.search_fields:
                condition |= Q(**{f"{field}__icontains": q})
            queryset = queryset.filter(condition)
        return queryset

    def apply_filters(self, queryset, request):
        """Equality-Filter aus ``filter_fields`` fuer jeden gesetzten Param."""
        for param, field in self.filter_fields.items():
            value = request.GET.get(param)
            if value:
                queryset = queryset.filter(**{field: value})
        return queryset

    def pagination_params(self, request, extra_params=None) -> str:
        """Querystring fuer Paginierungs-Links aus den aktiven Filtern.

        ``q`` (falls ``search_fields`` gesetzt), die ``filter_fields``-
        Params und optionale ``extra_params`` (Mapping) — leere Werte
        werden weggelassen. Reihenfolge: ``q``, dann ``filter_fields``,
        dann ``extra_params`` (wie in der Original-View aufgebaut).
        """
        from urllib.parse import urlencode

        params: dict[str, str] = {}
        if self.search_fields:
            params["q"] = self.get_search_term(request)
        for param in self.filter_fields:
            params[param] = request.GET.get(param, "")
        if extra_params:
            params.update(extra_params)
        return urlencode({k: v for k, v in params.items() if v})


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
