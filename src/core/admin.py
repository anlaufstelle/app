"""Django admin configuration for all core models."""

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from unfold.admin import ModelAdmin, TabularInline
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from core.models import (
    AuditLog,
    Case,
    Client,
    DeletionRequest,
    DocumentType,
    DocumentTypeField,
    Event,
    EventHistory,
    Facility,
    FieldTemplate,
    Organization,
    Settings,
    StatisticsSnapshot,
    TimeFilter,
    User,
    WorkItem,
)
from core.services.password import generate_initial_password

# --- User ---


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm
    list_display = ("username", "display_name", "role", "facility", "is_active")
    list_filter = ("role", "facility", "is_active", "is_staff")
    fieldsets = BaseUserAdmin.fieldsets + (
        (
            "Anlaufstelle",
            {
                "fields": (
                    "role",
                    "facility",
                    "display_name",
                    "phone",
                    "notes",
                    "must_change_password",
                    "preferred_language",
                ),
            },
        ),
    )
    add_fieldsets = (
        (
            None,
            {
                "fields": ("username",),
            },
        ),
        (
            "Anlaufstelle",
            {
                "fields": ("role", "facility", "display_name"),
            },
        ),
    )

    def save_model(self, request, obj, form, change):
        if not change:
            password = generate_initial_password()
            obj.set_password(password)
            obj.must_change_password = True
            super().save_model(request, obj, form, change)
            messages.success(
                request,
                f"Initialpasswort für {obj.username}: {password} — Bitte notieren, es wird nicht erneut angezeigt!",
                extra_tags="password-display",
            )
        else:
            super().save_model(request, obj, form, change)

    def response_add(self, request, obj, post_url_continue=None):
        response = super().response_add(request, obj, post_url_continue)
        response["Cache-Control"] = "no-store"
        return response


# --- Organization / Facility ---


@admin.register(Organization)
class OrganizationAdmin(ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)


@admin.register(Facility)
class FacilityAdmin(ModelAdmin):
    list_display = ("name", "organization", "is_active", "created_at")
    list_filter = ("is_active", "organization")
    search_fields = ("name",)


# --- Client ---


@admin.register(Client)
class ClientAdmin(ModelAdmin):
    list_display = ("pseudonym", "contact_stage", "age_cluster", "facility", "is_active")
    list_filter = ("contact_stage", "age_cluster", "is_active", "facility")
    search_fields = ("pseudonym",)


# --- DocumentType / FieldTemplate ---


class DocumentTypeFieldInline(TabularInline):
    model = DocumentTypeField
    extra = 1
    autocomplete_fields = ("field_template",)


@admin.register(DocumentType)
class DocumentTypeAdmin(ModelAdmin):
    list_display = ("name", "category", "sensitivity", "system_type", "facility", "is_active", "sort_order")
    list_filter = ("category", "sensitivity", "is_active", "facility", "system_type")
    search_fields = ("name",)
    inlines = [DocumentTypeFieldInline]

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.system_type:
            return (*self.readonly_fields, "system_type")
        return self.readonly_fields


@admin.register(FieldTemplate)
class FieldTemplateAdmin(ModelAdmin):
    list_display = ("name", "slug", "field_type", "is_required", "is_encrypted", "facility")
    list_filter = ("field_type", "is_encrypted", "is_required", "facility")
    search_fields = ("name",)

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return (*self.readonly_fields, "slug")
        return self.readonly_fields


# --- Event / EventHistory ---


@admin.register(Event)
class EventAdmin(ModelAdmin):
    list_display = ("document_type", "client", "occurred_at", "is_anonymous", "is_deleted", "facility")
    list_filter = ("is_anonymous", "is_deleted", "facility", "document_type")
    raw_id_fields = ("client", "created_by", "case")
    date_hierarchy = "occurred_at"
    search_fields = ("client__pseudonym",)


@admin.register(EventHistory)
class EventHistoryAdmin(ModelAdmin):
    list_display = ("event", "action", "changed_by", "changed_at")
    list_filter = ("action",)
    readonly_fields = ("event", "changed_by", "changed_at", "action", "data_before", "data_after")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# --- TimeFilter ---


@admin.register(TimeFilter)
class TimeFilterAdmin(ModelAdmin):
    list_display = ("label", "start_time", "end_time", "is_default", "is_active", "facility")
    list_filter = ("is_active", "is_default", "facility")


# --- WorkItem / DeletionRequest ---


@admin.register(WorkItem)
class WorkItemAdmin(ModelAdmin):
    list_display = ("title", "item_type", "status", "priority", "assigned_to", "facility")
    list_filter = ("status", "priority", "item_type", "facility")
    search_fields = ("title",)
    raw_id_fields = ("client", "created_by", "assigned_to")


@admin.register(DeletionRequest)
class DeletionRequestAdmin(ModelAdmin):
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


# --- Case ---


@admin.register(Case)
class CaseAdmin(ModelAdmin):
    list_display = ("title", "client", "status", "created_by", "created_at")
    list_filter = ("status", "facility")
    search_fields = ("title", "client__pseudonym")
    raw_id_fields = ("client", "created_by")


# --- AuditLog ---


@admin.register(AuditLog)
class AuditLogAdmin(ModelAdmin):
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


# --- Settings ---


@admin.register(Settings)
class SettingsAdmin(ModelAdmin):
    list_display = ("facility", "facility_full_name", "session_timeout_minutes")

    def has_add_permission(self, request):
        # Only allow if there are facilities without settings
        from core.models import Facility

        facilities_without = Facility.objects.filter(settings__isnull=True)
        return facilities_without.exists()

    def has_delete_permission(self, request, obj=None):
        return False


# --- StatisticsSnapshot ---


@admin.register(StatisticsSnapshot)
class StatisticsSnapshotAdmin(ModelAdmin):
    list_display = ("facility", "year", "month", "updated_at")
    list_filter = ("facility", "year")
    readonly_fields = ("id", "facility", "year", "month", "data", "jugendamt_data", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
