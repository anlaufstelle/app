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


# --- Facility/HTMX Helper-Mixins (Refs #598 R-2/R-3) --------------------
#
# Eingeführt für neue Views und den Event-God-Object-Split (#603). Alte
# Views bleiben unverändert — eine Komplett-Migration aller ~87/11
# Call-Sites brächte kaum Gewinn gegenüber dem Risiko breiter Edits.


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


class HTMXPartialMixin:
    """Rendert je nach ``HX-Request``-Header ein Partial- oder Full-Page-
    Template. Erwartet ``partial_template_name`` und ``template_name`` als
    Klassenattribute.

    Nur für den einfachen Fall (ein Partial, ein Full-Page-Template).
    Bulk-Action-Views mit ``HX-Redirect``-Response bleiben besser inline,
    weil ihr HTMX-Pfad eine ganz andere Response-Form liefert.
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
