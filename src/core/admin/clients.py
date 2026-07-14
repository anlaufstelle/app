"""Admin fuer Client + Case (Refs #785, #958)."""

from django.contrib import admin
from unfold.admin import ModelAdmin

from core.admin.mixins import AdminReadAuditMixin, ReadOnlyDomainAdminMixin
from core.admin_site import anlaufstelle_admin_site
from core.models import Case, Client


@admin.register(Client, site=anlaufstelle_admin_site)
class ClientAdmin(ReadOnlyDomainAdminMixin, AdminReadAuditMixin, ModelAdmin):
    list_display = ("pseudonym", "contact_stage", "age_cluster", "facility", "is_active")
    list_filter = ("contact_stage", "age_cluster", "is_active", "facility")
    search_fields = ("pseudonym",)


@admin.register(Case, site=anlaufstelle_admin_site)
class CaseAdmin(ReadOnlyDomainAdminMixin, AdminReadAuditMixin, ModelAdmin):
    list_display = ("title", "client", "status", "created_by", "created_at")
    list_filter = ("status", "facility")
    search_fields = ("title", "client__pseudonym")
    raw_id_fields = ("client", "created_by")
