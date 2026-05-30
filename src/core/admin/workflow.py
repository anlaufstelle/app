"""Admin fuer WorkItem + DeletionRequest (Refs #785, #958).

DeletionRequest ist Workflow-Append-Only: kein Add/Delete, nur Approve/Reject
ueber den Service-Layer.
"""

from django.contrib import admin
from unfold.admin import ModelAdmin

from core.admin.mixins import FacilityScopedAdminMixin
from core.admin_site import anlaufstelle_admin_site
from core.models import DeletionRequest, WorkItem


@admin.register(WorkItem, site=anlaufstelle_admin_site)
class WorkItemAdmin(FacilityScopedAdminMixin, ModelAdmin):
    list_display = ("title", "item_type", "status", "priority", "assigned_to", "facility")
    list_filter = ("status", "priority", "item_type", "facility")
    search_fields = ("title",)
    raw_id_fields = ("client", "created_by", "assigned_to")


@admin.register(DeletionRequest, site=anlaufstelle_admin_site)
class DeletionRequestAdmin(FacilityScopedAdminMixin, ModelAdmin):
    list_display = ("target_type", "target_id", "status", "requested_by", "created_at")
    list_filter = ("status",)
    readonly_fields = (
        "facility",
        "target_type",
        "target_id",
        "reason",
        "status",
        "requested_by",
        "reviewed_by",
        "created_at",
        "reviewed_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
