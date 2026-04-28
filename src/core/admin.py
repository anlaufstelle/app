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
    QuickTemplate,
    Settings,
    StatisticsSnapshot,
    TimeFilter,
    User,
    WorkItem,
)
from core.models.attachment import EventAttachment
from core.services.invite import send_invite_email
from core.services.login_lockout import is_locked as user_is_locked
from core.services.login_lockout import unlock as unlock_user
from core.services.password import generate_initial_password

# --- User ---


@admin.action(description="Account-Sperre aufheben (Login freigeben)")
def unlock_selected_users(modeladmin, request, queryset):
    """Admin-Action: LOGIN_UNLOCK-AuditLog schreiben, damit is_locked() künftig
    alle bisherigen LOGIN_FAILED-Einträge dieser User ignoriert."""
    from core.signals.audit import get_client_ip

    unlocked = 0
    for user in queryset:
        if user_is_locked(user):
            unlock_user(user, unlocked_by=request.user, ip_address=get_client_ip(request))
            unlocked += 1
    if unlocked:
        messages.success(request, f"{unlocked} Account-Sperre(n) aufgehoben.")
    else:
        messages.info(request, "Keine der ausgewählten Accounts war gesperrt.")


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm
    list_display = ("username", "display_name", "role", "facility", "is_active")
    list_filter = ("role", "facility", "is_active", "is_staff")
    actions = [unlock_selected_users]
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
                "fields": ("username", "email"),
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
        """Token-basierter Invite-Flow beim User-Anlegen.

        Primär: User ohne Passwort anlegen (`set_unusable_password`) und
        Einladungs-E-Mail mit Password-Reset-Token an `obj.email` versenden.

        Fallback ohne E-Mail: Klartext-Initialpasswort generieren und in
        der Admin-Oberfläche anzeigen — mit Warnhinweis, dass dieser Weg
        unsicherer ist. Siehe Issue #528.
        """
        if change:
            super().save_model(request, obj, form, change)
            return

        obj.must_change_password = True

        if obj.email:
            obj.set_unusable_password()
            super().save_model(request, obj, form, change)
            try:
                send_invite_email(obj, request=request)
            except Exception as exc:  # pragma: no cover - Mail-Backend-Fehler
                messages.error(
                    request,
                    f"Einladungs-E-Mail konnte nicht versendet werden: {exc}. "
                    f"Bitte über „Setup-Link erneut senden“ erneut versuchen.",
                )
                return
            messages.success(
                request,
                f"Einladungslink wurde an {obj.email} gesendet.",
            )
        else:
            password = generate_initial_password()
            obj.set_password(password)
            super().save_model(request, obj, form, change)
            messages.warning(
                request,
                "Kein E-Mail-Adresse hinterlegt — Fallback auf Klartext-Initialpasswort. "
                "Sicherer Weg: E-Mail nachtragen und Einladung erneut versenden.",
            )
            messages.success(
                request,
                f"Initialpasswort für {obj.username}: {password} — Bitte notieren, es wird nicht erneut angezeigt!",
                extra_tags="password-display",
            )

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
    list_display = (
        "name",
        "slug",
        "field_type",
        "is_active",
        "is_required",
        "is_encrypted",
        "sensitivity",
        "facility",
    )
    list_filter = ("is_active", "field_type", "is_encrypted", "sensitivity", "is_required", "facility")
    search_fields = ("name",)
    fields = (
        "facility",
        "name",
        "slug",
        "field_type",
        "is_required",
        "is_encrypted",
        "sensitivity",
        "options_json",
        "default_value",
        "statistics_category",
        "help_text",
        "is_active",
    )
    actions = ("deactivate_selected", "activate_selected")

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return (*self.readonly_fields, "slug")
        return self.readonly_fields

    def delete_model(self, request, obj):
        """ProtectedError aus dem pre_delete-Signal abfangen und als Admin-Meldung anzeigen (Issue #356)."""
        from django.db.models import ProtectedError

        try:
            super().delete_model(request, obj)
        except ProtectedError as exc:
            messages.error(request, str(exc.args[0]))

    def delete_queryset(self, request, queryset):
        """Bulk-Delete: ProtectedError pro FieldTemplate abfangen und in der Admin-UI zeigen."""
        from django.db.models import ProtectedError

        for obj in queryset:
            try:
                obj.delete()
            except ProtectedError as exc:
                messages.error(request, str(exc.args[0]))

    @admin.action(description="Ausgewählte Feldvorlagen deaktivieren")
    def deactivate_selected(self, request, queryset):
        updated = queryset.update(is_active=False)
        messages.success(request, f"{updated} Feldvorlage(n) deaktiviert.")

    @admin.action(description="Ausgewählte Feldvorlagen aktivieren")
    def activate_selected(self, request, queryset):
        updated = queryset.update(is_active=True)
        messages.success(request, f"{updated} Feldvorlage(n) aktiviert.")


@admin.register(QuickTemplate)
class QuickTemplateAdmin(ModelAdmin):
    """Admin für Quick-Templates (vorbefüllte Event-Vorlagen).

    ``prefilled_data`` wird beim Speichern über den Service auf NORMAL-Felder
    gefiltert (Whitelist). So kann der Admin-User Werte pflegen, ohne die
    Sensitivitäts-Regeln zu umgehen.
    """

    list_display = ("name", "document_type", "facility", "is_active", "sort_order")
    list_filter = ("is_active", "facility", "document_type")
    search_fields = ("name",)
    autocomplete_fields = ("document_type",)
    readonly_fields = ("created_at",)

    def save_model(self, request, obj, form, change):
        from core.services.quick_templates import filter_prefilled_data

        obj.prefilled_data = filter_prefilled_data(obj.document_type, obj.prefilled_data or {})
        if not change and obj.created_by_id is None:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


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


# --- EventAttachment ---


@admin.register(EventAttachment)
class EventAttachmentAdmin(ModelAdmin):
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
