"""Rollenbezogene Arbeitszentrale (Refs #920).

``RoleDashboardView`` dispatched anhand der Rolle des angemeldeten Users
zu einem von vier Templates und liefert pro Rolle einen Daten-Context
aus :mod:`core.services.dashboard`. Kein eigenes Rollen-Gate —
``LoginRequiredMixin`` schliesst nur nicht angemeldete Zugriffe aus; jede
authentifizierte Rolle erhaelt ihre Landingpage (``assistant`` faellt in
den Staff-/else-Zweig).
"""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.views.generic import TemplateView

from core.services.dashboard import (
    facility_admin_dashboard_context,
    lead_dashboard_context,
    staff_dashboard_context,
    super_admin_dashboard_context,
)


class RoleDashboardView(LoginRequiredMixin, TemplateView):
    """Rollenspezifische Landingpage `/start/`.

    Wahl des Templates anhand ``user.role``; Daten-Aggregation in
    :mod:`core.services.dashboard`.
    """

    def get(self, request, *args, **kwargs):
        # Fachkraft/Assistenz: Cockpit lebt auf der Start-Seite (Refs #1124).
        if not request.user.is_super_admin and not request.user.is_lead_or_admin:
            return redirect("core:zeitstrom")
        return super().get(request, *args, **kwargs)

    def get_template_names(self) -> list[str]:
        user = self.request.user
        if user.is_super_admin:
            return ["core/dashboard/role_super_admin.html"]
        if user.is_facility_admin:
            return ["core/dashboard/role_facility_admin.html"]
        if user.is_lead_or_admin:
            return ["core/dashboard/role_lead.html"]
        return ["core/dashboard/role_staff.html"]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        facility = getattr(self.request, "current_facility", None) or user.facility

        if user.is_super_admin:
            context.update(super_admin_dashboard_context(user))
        elif user.is_facility_admin:
            context.update(facility_admin_dashboard_context(user, facility))
        elif user.is_lead_or_admin:
            context.update(lead_dashboard_context(user, facility))
        else:
            context.update(staff_dashboard_context(user, facility))

        return context
