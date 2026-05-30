"""Admin-Mixins: Rollen-basierte Permissions + Facility-Scoping.

Refs #785 — Mixins fuer Custom-AdminSite mit Sudo + Rollen-Gate.
Refs #958 — aus ``core/admin.py`` extrahiert; Modul war 559 LoC mit 17
ModelAdmin-Klassen in einer Datei.
"""

from django.contrib.admin.sites import AdminSite

_ = AdminSite  # nur damit linters den Import-Pfad nicht entfernen


class RoleBasedPermissionMixin:
    """Oeffnet ModelAdmin-Permissions fuer super_admin/facility_admin (Refs #785).

    Default-Django prueft request.user.has_perm('app.view_model'), was nur
    funktioniert, wenn der User is_superuser=True hat ODER konkrete Permissions
    gepflegt sind. ``super_admin_user`` hat aber is_superuser=False (per Memory-
    Direktive), und wir verwenden keine fein-granularen Django-Permissions im
    Admin. Stattdessen entscheidet die Rolle: wenn die User-Rolle den Zugriff
    zur AdminSite ueberhaupt erlaubt (siehe AnlaufstelleAdminSite.has_permission),
    duerfen sie auch lesen/schreiben.

    Einzelne ModelAdmins (EventHistoryAdmin, AuditLogAdmin, etc.) ueberschreiben
    has_add/change/delete_permission explizit auf False — das gewinnt durch MRO
    gegen diesen Mixin.
    """

    def has_view_permission(self, request, obj=None):
        return request.user.is_super_admin or request.user.is_facility_admin

    def has_add_permission(self, request):
        return request.user.is_super_admin or request.user.is_facility_admin

    def has_change_permission(self, request, obj=None):
        return request.user.is_super_admin or request.user.is_facility_admin

    def has_delete_permission(self, request, obj=None):
        return request.user.is_super_admin or request.user.is_facility_admin


class FacilityScopedAdminMixin(RoleBasedPermissionMixin):
    """Filtert get_queryset() nach request.current_facility fuer facility_admin.

    super_admin sieht alles (kein Filter). Konsistent mit FacilityScopedManager
    in den Models. Erbt von RoleBasedPermissionMixin, damit Permissions
    rollen-basiert sind. Verwendung als zusaetzliche Base-Klasse vor `ModelAdmin`:

        @admin.register(Client, site=anlaufstelle_admin_site)
        class ClientAdmin(FacilityScopedAdminMixin, ModelAdmin):
            ...
    """

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_super_admin:
            return qs
        return qs.filter(facility=request.current_facility)
