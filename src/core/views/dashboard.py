"""Rollenbezogene Arbeitszentrale (Refs #920, Regel N3 aus #1801).

``/start/`` ist der Rollen-Einstieg — und leitet dorthin weiter, wo ein
Bereich die bessere Heimat ist. Das Muster ist nicht neu: Fachkraft und
Assistenz landen seit #1124 im Zeitstrom, weil ihr Cockpit dort lebt. Refs
#1813 vervollständigt es nach beiden Seiten: die Anwendungsbetreuung landet
auf ``/verwaltung/`` (deren Übersicht die bisherige, fast leere Rollen-Seite
ersetzt), die Systemadministration auf ``/system/``.

Übrig bleibt **eine** eigene Arbeitszentrale — die der Leitung. Sie ist die
einzige Rolle, deren Arbeit sich nicht in einem Bereich bündeln lässt:
DSGVO-Workflows, Retention und Statistik nebeneinander.
"""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.views.generic import TemplateView

from core.services.dashboard import lead_dashboard_context


class RoleDashboardView(LoginRequiredMixin, TemplateView):
    """Rollen-Einstieg ``/start/`` — Leitungs-Arbeitszentrale oder Weiterleitung."""

    template_name = "core/dashboard/role_lead.html"

    def get(self, request, *args, **kwargs):
        user = request.user
        # Reihenfolge = Spezifität: super_admin ist kein is_lead_or_admin,
        # facility_admin schon — die engere Prüfung muss zuerst stehen.
        if user.is_super_admin:
            # F5: der Systembereich IST der Einstieg dieser Rolle. Nebenwirkung
            # (bewusst): /system/-Views tragen SystemAuditMixin, jede Landung
            # schreibt also einen SYSTEM_VIEW-Eintrag. Fachlich richtig — sie
            # betritt den Systembereich —, erhöht aber das Audit-Volumen.
            return redirect("core:system_dashboard")
        if user.is_facility_admin:
            return redirect("core:verwaltung_uebersicht")
        if not user.is_lead_or_admin:
            # Fachkraft/Assistenz: Cockpit lebt auf der Zeitstrom-Seite (Refs #1124).
            return redirect("core:zeitstrom")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        facility = getattr(self.request, "current_facility", None) or user.facility
        context.update(lead_dashboard_context(user, facility))
        return context
