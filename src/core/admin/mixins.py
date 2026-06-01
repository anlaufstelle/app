"""Admin-Mixins: Delegates an die zentralisierte AdminSite-Logik.

Refs #785 — Custom AdminSite mit Sudo + Rollen-Gate.
Refs #958 (M-1) — aus ``core/admin.py`` extrahiert.
Refs #958 (M-2) — Rollen-/Facility-Logik in ``AnlaufstelleAdminSite`` zentralisiert;
die Mixins delegieren jetzt an ``self.admin_site.has_role_permission(...)`` und
``self.admin_site.scope_to_facility(...)``, damit es nur eine Definition gibt.

Wenn sich die Rollen-Logik aendert (Stichwort: 5-Rollen-Modell, weitere Sub-Rollen)
gibt es genau **einen** Ort, an dem das passieren muss:
``core/admin_site.AnlaufstelleAdminSite``.
"""


class RoleBasedPermissionMixin:
    """Delegiert ModelAdmin-Permissions an ``self.admin_site.has_role_permission``.

    Einzelne ModelAdmins (EventHistoryAdmin, AuditLogAdmin, etc.) ueberschreiben
    has_add/change/delete_permission explizit auf False — das gewinnt durch MRO
    gegen diesen Mixin.
    """

    def has_view_permission(self, request, obj=None):
        return self.admin_site.has_role_permission(request)

    def has_add_permission(self, request):
        return self.admin_site.has_role_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.admin_site.has_role_permission(request)

    def has_delete_permission(self, request, obj=None):
        return self.admin_site.has_role_permission(request)


class FacilityScopedAdminMixin(RoleBasedPermissionMixin):
    """Delegiert Facility-Scoping an ``self.admin_site.scope_to_facility``.

    Verwendung als zusaetzliche Base-Klasse vor `ModelAdmin`:

        @admin.register(Client, site=anlaufstelle_admin_site)
        class ClientAdmin(FacilityScopedAdminMixin, ModelAdmin):
            ...
    """

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return self.admin_site.scope_to_facility(qs, request)
