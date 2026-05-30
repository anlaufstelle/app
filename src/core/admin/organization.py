"""Admin fuer Organization + Facility (Refs #785, #958)."""

from django.contrib import admin
from unfold.admin import ModelAdmin

from core.admin.mixins import RoleBasedPermissionMixin
from core.admin_site import anlaufstelle_admin_site
from core.models import Facility, Organization


@admin.register(Organization, site=anlaufstelle_admin_site)
class OrganizationAdmin(RoleBasedPermissionMixin, ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)


@admin.register(Facility, site=anlaufstelle_admin_site)
class FacilityAdmin(RoleBasedPermissionMixin, ModelAdmin):
    list_display = ("name", "organization", "is_active", "created_at")
    list_filter = ("is_active", "organization")
    search_fields = ("name",)

    def get_queryset(self, request):
        """facility_admin sieht nur eigene Facility, super_admin alle (Refs #785)."""
        qs = super().get_queryset(request)
        if request.user.is_super_admin:
            return qs
        return qs.filter(pk=request.current_facility.pk if request.current_facility else None)
