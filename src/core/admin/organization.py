"""Admin fuer Organization + Facility (Refs #785, #958)."""

from django.contrib import admin
from unfold.admin import ModelAdmin

from core.admin.mixins import SuperAdminOnlyAdminMixin
from core.admin_site import anlaufstelle_admin_site
from core.models import Facility, Organization


@admin.register(Organization, site=anlaufstelle_admin_site)
class OrganizationAdmin(SuperAdminOnlyAdminMixin, ModelAdmin):
    """Organization liegt ueber der Facility-Ebene -> nur super_admin (A2.3, Refs #1021)."""

    list_display = ("name", "created_at")
    search_fields = ("name",)


@admin.register(Facility, site=anlaufstelle_admin_site)
class FacilityAdmin(SuperAdminOnlyAdminMixin, ModelAdmin):
    """Facility liegt oberhalb der facility_admin-Zustaendigkeit -> nur super_admin (L1, Refs #1375).

    Wie ``Organization`` (A2.3) ist die Facility ein installationsweites
    Struktur-Objekt; ein facility_admin verwaltet Daten INNERHALB seiner
    Facility, nicht die Facility-Datensaetze selbst (Anlegen/Umbenennen/
    Loeschen). Vorher delegierten die Permissions an ``RoleBasedPermissionMixin``
    und liessen facility_admin vollen Zugriff. Da jetzt nur super_admin Zugriff
    hat, ist kein Facility-Scoping im Queryset mehr noetig (super_admin sieht
    ohnehin alle)."""

    list_display = ("name", "organization", "is_active", "created_at")
    list_filter = ("is_active", "organization")
    search_fields = ("name",)
