"""Custom AdminSite mit Rollen-Gate + Sudo-Mode-Pflicht (Refs #785).

Default-Django-AdminSite prueft nur ``is_staff``. Anlaufstelle erweitert:

- Rolle muss ``super_admin`` oder ``facility_admin`` sein
  (``lead``/``staff``/``assistant`` werden geblockt, auch wenn ``is_staff=True``).
- Sudo-Mode muss aktiv sein (Re-Auth-Schutz, Refs #683).

Modelle werden mit dem Singleton ``anlaufstelle_admin_site`` registriert
(``@admin.register(Model, site=anlaufstelle_admin_site)``). URL-Hookup in
``src/anlaufstelle/urls.py`` zeigt ``/admin-mgmt/`` auf diese Site.
"""

from __future__ import annotations

from django.conf import settings
from django.shortcuts import redirect
from unfold.sites import UnfoldAdminSite

from core.services.sudo_mode import is_in_sudo


class AnlaufstelleAdminSite(UnfoldAdminSite):
    """AdminSite mit Rollen-Gate (super_admin/facility_admin) + Sudo-Pflicht.

    Erbt von ``UnfoldAdminSite``, damit das Unfold-Theme (Search-Endpoint,
    each_context-Variablen, Login-Form) ohne Brueche funktioniert.
    """

    site_header = "Anlaufstelle Verwaltung"
    site_title = "Anlaufstelle"
    index_title = "Datenverwaltung"

    def has_permission(self, request):
        """Zugriff nur fuer super_admin/facility_admin mit aktivem Sudo-Mode."""
        if not request.user.is_authenticated:
            return False
        if not (request.user.is_super_admin or request.user.is_facility_admin):
            return False
        if not getattr(settings, "SUDO_MODE_ENABLED", True):
            return True
        return is_in_sudo(request)

    def login(self, request, extra_context=None):
        """Login-View: wenn User eingeloggt + Rolle OK, aber Sudo fehlt -> /sudo/."""
        if (
            request.user.is_authenticated
            and (request.user.is_super_admin or request.user.is_facility_admin)
            and getattr(settings, "SUDO_MODE_ENABLED", True)
            and not is_in_sudo(request)
        ):
            return redirect(f"/sudo/?next={request.get_full_path()}")
        return super().login(request, extra_context)


# Namespace "admin" — Unfold-Theme-Templates verwenden {% url 'admin:...' %},
# das nur mit diesem Namespace aufgeloest werden kann. Das ist OK, weil wir die
# Default-django.contrib.admin.site aus dem URLconf entfernen (kein Konflikt).
anlaufstelle_admin_site = AnlaufstelleAdminSite(name="admin")
