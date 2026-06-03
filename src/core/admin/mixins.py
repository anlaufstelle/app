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

from core.models import Facility


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

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """facility-FK zentral auf die eigene Facility begrenzen (A2.2, Refs #1021).

        non-super_admin darf in JEDEM facility-gescopten Admin nur die eigene
        Facility zuweisen; der ``ModelChoiceField``-Queryset validiert die
        geposteten PK serverseitig. Single Source of Truth statt Duplikat pro
        ModelAdmin (vgl. die lokale A2.1-Loesung im ``UserAdmin``).
        """
        if db_field.name == "facility" and not request.user.is_super_admin:
            current = getattr(request, "current_facility", None)
            kwargs["queryset"] = Facility.objects.filter(pk=current.pk) if current else Facility.objects.none()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        """facility serverseitig erzwingen (A2.2, Refs #1021).

        Defense-in-Depth gegen umgangene Form-Validierung: ein non-super_admin
        kann ueber einen gefaelschten POST keine fremde Facility setzen — die
        eigene Facility wird vor dem Speichern erzwungen. super_admin behaelt
        die freie Wahl.
        """
        if not request.user.is_super_admin:
            current = getattr(request, "current_facility", None)
            if current is not None:
                obj.facility = current
        super().save_model(request, obj, form, change)
