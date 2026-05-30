"""Admin fuer Event, EventHistory, EventAttachment (Refs #785, #958).

EventHistory und EventAttachment sind append-only (Audit-Trail-Charakter):
keine Add/Change/Delete-Permissions. Facility-Scoping ueber event__facility,
weil weder History noch Attachment ein direktes facility-FK haben.
"""

from django.contrib import admin
from unfold.admin import ModelAdmin

from core.admin.mixins import FacilityScopedAdminMixin, RoleBasedPermissionMixin
from core.admin_site import anlaufstelle_admin_site
from core.models import Event, EventHistory
from core.models.attachment import EventAttachment


@admin.register(Event, site=anlaufstelle_admin_site)
class EventAdmin(FacilityScopedAdminMixin, ModelAdmin):
    list_display = ("document_type", "client", "occurred_at", "is_anonymous", "is_deleted", "facility")
    list_filter = ("is_anonymous", "is_deleted", "facility", "document_type")
    raw_id_fields = ("client", "created_by", "case")
    date_hierarchy = "occurred_at"
    search_fields = ("client__pseudonym",)


@admin.register(EventHistory, site=anlaufstelle_admin_site)
class EventHistoryAdmin(RoleBasedPermissionMixin, ModelAdmin):
    list_display = ("event", "action", "changed_by", "changed_at")
    list_filter = ("action",)
    readonly_fields = ("event", "changed_by", "changed_at", "action", "data_before", "data_after")

    def get_queryset(self, request):
        """EventHistory hat kein direktes facility-FK -> ueber event__facility (Refs #785)."""
        qs = super().get_queryset(request)
        if request.user.is_super_admin:
            return qs
        return qs.filter(event__facility=request.current_facility)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EventAttachment, site=anlaufstelle_admin_site)
class EventAttachmentAdmin(RoleBasedPermissionMixin, ModelAdmin):
    list_display = (
        "storage_filename",
        "event",
        "field_template",
        "mime_type",
        "file_size",
        "created_by",
        "created_at",
    )
    list_filter = ("mime_type",)
    readonly_fields = (
        "id",
        "event",
        "field_template",
        "storage_filename",
        "original_filename_encrypted",
        "file_size",
        "mime_type",
        "created_by",
        "created_at",
    )
    search_fields = ("storage_filename",)

    def get_queryset(self, request):
        """EventAttachment hat kein direktes facility-FK -> ueber event__facility (Refs #785)."""
        qs = super().get_queryset(request)
        if request.user.is_super_admin:
            return qs
        return qs.filter(event__facility=request.current_facility)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
