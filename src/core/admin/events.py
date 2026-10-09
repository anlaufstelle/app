"""Admin fuer Event + EventHistory (Refs #785, #958).

EventHistory ist append-only (Audit-Trail-Charakter): keine Add/Change/Delete-
Permissions, Facility-Scoping ueber ``event__facility``, weil die History kein
direktes facility-FK hat.

M24/AP2 (Refs #1738): ``AttachmentAdmin`` ist nach :mod:`core.admin.attachments`
umgezogen — ``Attachment`` traegt seit ADR-039 eine eigene ``facility`` und
haengt nicht mehr zwingend an einem Ereignis.
"""

from django.contrib import admin
from unfold.admin import ModelAdmin

from core.admin.mixins import ReadOnlyDomainAdminMixin, RoleBasedPermissionMixin
from core.admin_site import anlaufstelle_admin_site
from core.models import Event, EventHistory


@admin.register(Event, site=anlaufstelle_admin_site)
class EventAdmin(ReadOnlyDomainAdminMixin, ModelAdmin):
    list_display = ("document_type", "client", "occurred_at", "is_anonymous", "is_deleted", "facility")
    list_filter = ("is_anonymous", "is_deleted", "facility", "document_type")
    raw_id_fields = ("client", "created_by", "case")
    date_hierarchy = "occurred_at"
    search_fields = ("client__pseudonym",)
    fields = (
        "id",
        "facility",
        "document_type",
        "client",
        "case",
        "created_by",
        "occurred_at",
        "is_anonymous",
        "is_deleted",
        "deleted_at",
        "deleted_by",
        "created_at",
        "updated_at",
    )
    readonly_fields = fields


@admin.register(EventHistory, site=anlaufstelle_admin_site)
class EventHistoryAdmin(RoleBasedPermissionMixin, ModelAdmin):
    list_display = ("event", "action", "changed_by", "changed_at")
    list_filter = ("action",)
    fields = ("id", "event", "changed_by", "changed_at", "action", "redacted_at")
    readonly_fields = fields

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
