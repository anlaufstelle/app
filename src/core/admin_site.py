"""Custom AdminSite mit Rollen-Gate + Sudo-Mode-Pflicht (Refs #785).

Default-Django-AdminSite prueft nur ``is_staff``. Anlaufstelle erweitert:

- Rolle muss ``super_admin`` oder ``facility_admin`` sein
  (``lead``/``staff``/``assistant`` werden geblockt, auch wenn ``is_staff=True``).
- Sudo-Mode muss aktiv sein (Re-Auth-Schutz, Refs #683).

Modelle werden mit dem Singleton ``anlaufstelle_admin_site`` registriert
(``@admin.register(Model, site=anlaufstelle_admin_site)``). URL-Hookup in
``src/anlaufstelle/urls.py`` zeigt ``/admin-mgmt/`` auf diese Site.

Refs #958 — ``has_role_permission`` und ``scope_to_facility`` zentralisieren
die Rollen-/Facility-Logik, die vorher in den Mixins (``core/admin/mixins.py``)
dupliziert war. Die Mixins delegieren jetzt an diese Site-Methoden, damit es
nur eine Definition pro Regel gibt.
"""

from __future__ import annotations

from django.conf import settings
from django.shortcuts import redirect
from unfold.sites import UnfoldAdminSite

from core.services.security import is_in_sudo


class AnlaufstelleAdminSite(UnfoldAdminSite):
    """AdminSite mit Rollen-Gate (super_admin/facility_admin) + Sudo-Pflicht.

    Erbt von ``UnfoldAdminSite``, damit das Unfold-Theme (Search-Endpoint,
    each_context-Variablen, Login-Form) ohne Brueche funktioniert.
    """

    site_header = "Anlaufstelle Verwaltung"
    site_title = "Anlaufstelle"
    index_title = "Datenverwaltung"

    @staticmethod
    def _has_admin_role(user) -> bool:
        """Single source of truth: hat der User eine Admin-Rolle?"""
        if not user.is_authenticated:
            return False
        return user.is_super_admin or user.is_facility_admin

    def has_role_permission(self, request) -> bool:
        """Public API fuer ModelAdmin-Mixins.

        ModelAdmin-Klassen rufen ``self.admin_site.has_role_permission(request)``
        in ``has_view/add/change/delete_permission`` auf — so wird die Rollen-
        Logik nur an einer Stelle gepflegt.
        """
        return self._has_admin_role(request.user)

    def scope_to_facility(self, queryset, request):
        """Public API fuer ModelAdmin.get_queryset() bei facility-gescopten Models.

        super_admin sieht alles, facility_admin sieht nur ``request.current_facility``.
        Konsistent mit ``FacilityScopedManager`` in den Models.
        """
        if request.user.is_super_admin:
            return queryset
        return queryset.filter(facility=request.current_facility)

    def has_permission(self, request):
        """Zugriff nur fuer super_admin/facility_admin mit aktivem Sudo-Mode."""
        if not self._has_admin_role(request.user):
            return False
        if not getattr(settings, "SUDO_MODE_ENABLED", True):
            return True
        return is_in_sudo(request)

    def login(self, request, extra_context=None):
        """Login-View: wenn User eingeloggt + Rolle OK, aber Sudo fehlt -> /sudo/."""
        if (
            self._has_admin_role(request.user)
            and getattr(settings, "SUDO_MODE_ENABLED", True)
            and not is_in_sudo(request)
        ):
            return redirect(f"/sudo/?next={request.get_full_path()}")
        return super().login(request, extra_context)


# Namespace "admin" — Unfold-Theme-Templates verwenden {% url 'admin:...' %},
# das nur mit diesem Namespace aufgeloest werden kann. Das ist OK, weil wir die
# Default-django.contrib.admin.site aus dem URLconf entfernen (kein Konflikt).
anlaufstelle_admin_site = AnlaufstelleAdminSite(name="admin")
