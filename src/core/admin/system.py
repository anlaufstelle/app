"""Admin fuer AuditLog, Settings, StatisticsSnapshot (Refs #785, #958).

AuditLog und StatisticsSnapshot sind append-only/read-only; Settings hat ein
spezielles save_model fuer SETTINGS_CHANGE-AuditLog (Refs #893 / FND-001).
"""

from django.contrib import admin
from unfold.admin import ModelAdmin

from core.admin.mixins import FacilityScopedAdminMixin
from core.admin_site import anlaufstelle_admin_site
from core.models import AuditLog, Settings, StatisticsSnapshot


@admin.register(AuditLog, site=anlaufstelle_admin_site)
class AuditLogAdmin(FacilityScopedAdminMixin, ModelAdmin):
    list_display = ("action", "user", "target_type", "target_id", "timestamp", "facility")
    list_filter = ("action", "facility")
    date_hierarchy = "timestamp"
    readonly_fields = (
        "facility",
        "user",
        "action",
        "target_type",
        "target_id",
        "detail",
        "ip_address",
        "timestamp",
    )
    search_fields = ("user__username", "target_type", "detail")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Settings, site=anlaufstelle_admin_site)
class SettingsAdmin(FacilityScopedAdminMixin, ModelAdmin):
    list_display = (
        "facility",
        "facility_full_name",
        "session_timeout_minutes",
        "allowed_file_types",
        "max_file_size_mb",
    )

    def has_add_permission(self, request):
        # Only allow if there are facilities without settings
        from core.models import Facility

        facilities_without = Facility.objects.filter(settings__isnull=True)
        return facilities_without.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        """Write a SETTINGS_CHANGE audit entry for every Settings update."""
        from core.services.settings import log_settings_change, snapshot_settings

        before = snapshot_settings(obj) if change and obj.pk else {}
        super().save_model(request, obj, form, change)
        if change:
            log_settings_change(obj, request.user, before)


@admin.register(StatisticsSnapshot, site=anlaufstelle_admin_site)
class StatisticsSnapshotAdmin(FacilityScopedAdminMixin, ModelAdmin):
    list_display = ("facility", "year", "month", "updated_at")
    list_filter = ("facility", "year")
    readonly_fields = ("id", "facility", "year", "month", "data", "jugendamt_data", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
