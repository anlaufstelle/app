"""Bereichs-Übersichten „Datenschutz" und „Verwaltung" (Refs #1813, Konzept #1801 §4).

Die beiden einzigen neuen Routen des Hub-Umbaus. Alle Mitgliedsseiten behalten
ihre Pfade: null Redirects, null Bookmark-Bruch, null Änderung an
Service-Worker-Routen — und **keine** neuen Rechte.

Gate und Weiterleitung kommen aus derselben Quelle wie die Navigation
(:mod:`core.nav`). Das ist der Punkt: würde die View ihr Gate handschreiben,
könnten Sidebar-Eintrag und Seite auseinanderlaufen — genau die Klasse Defekt,
die #1736 (B) für die Löschanträge-Liste beheben musste.
"""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import redirect
from django.views.generic import TemplateView

from core import nav
from core.services.hubs import datenschutz_overview, verwaltung_overview
from core.views.mixins import FacilityAdminRequiredMixin


def _facility(request):
    return getattr(request, "current_facility", None) or request.user.facility


class DatenschutzUebersichtView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    """`/datenschutz/` — Betroffenenrechte, Fristen und Nachweise.

    Zugriff nach Regel N2: erreichbar ⟺ mindestens ein Reiter des Bereichs ist
    sichtbar. Damit deckt das Gate ohne Sonderfall auch die Fachkraft ab, die
    durch einen eigenen offenen Löschantrag genau einen Reiter sieht
    (Konzept §3.3).
    """

    template_name = "core/hubs/datenschutz.html"

    def test_func(self):
        return nav.section_is_visible(nav.DATENSCHUTZ, self.request.user)

    def get(self, request, *args, **kwargs):
        # Wer den Übersichts-Reiter nicht sieht, bekommt keine leere Hülle,
        # sondern direkt seinen einzigen sichtbaren Reiter — dasselbe Muster,
        # mit dem ``RoleDashboardView`` Fachkraft/Assistenz zum Zeitstrom
        # schickt. Abgeleitet statt handgeschrieben: das deckt neben der
        # antragstellenden Fachkraft auch die Trägerin des Rechts
        # „Löschbestätigung" ohne Leitungsrolle ab (Refs #1053).
        if not nav.is_visible(nav.DATENSCHUTZ.tabs[0], request.user):
            # ``test_func`` garantiert mindestens einen sichtbaren Reiter.
            target = nav.first_visible_tab(nav.DATENSCHUTZ, request.user)
            return redirect(target.url_name)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["metrics"] = datenschutz_overview(_facility(self.request), self.request.user)
        return context


class VerwaltungUebersichtView(FacilityAdminRequiredMixin, TemplateView):
    """`/verwaltung/` — Anwendungsbetreuung: Formulare, Felder, Datenübernahme, Zugänge.

    Keine Einzelreiter-Weiterleitung nötig: ``is_facility_admin`` sieht alle
    vier Reiter. Die Seite ersetzt die bisherige, fast leere
    ``role_facility_admin.html`` — die Rolle bekommt erstmals eine Heimat
    statt zweier halbleerer Seiten (Konzept §4.2).
    """

    template_name = "core/hubs/verwaltung.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(verwaltung_overview(_facility(self.request), self.request.user))
        return context
