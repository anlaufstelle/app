"""Admin fuer DocumentType, FieldTemplate, QuickTemplate, TimeFilter (Refs #785, #958)."""

from django.contrib import admin, messages
from unfold.admin import ModelAdmin, TabularInline

from core.admin.mixins import FacilityScopedAdminMixin
from core.admin_site import anlaufstelle_admin_site
from core.models import DocumentType, DocumentTypeField, FieldTemplate, QuickTemplate, TimeFilter


class DocumentTypeFieldInline(TabularInline):
    model = DocumentTypeField
    extra = 1
    autocomplete_fields = ("field_template",)


@admin.register(DocumentType, site=anlaufstelle_admin_site)
class DocumentTypeAdmin(FacilityScopedAdminMixin, ModelAdmin):
    list_display = ("name", "category", "sensitivity", "system_type", "facility", "is_active", "sort_order")
    list_filter = ("category", "sensitivity", "is_active", "facility", "system_type")
    search_fields = ("name",)
    inlines = [DocumentTypeFieldInline]

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.system_type:
            return (*self.readonly_fields, "system_type")
        return self.readonly_fields


@admin.register(FieldTemplate, site=anlaufstelle_admin_site)
class FieldTemplateAdmin(FacilityScopedAdminMixin, ModelAdmin):
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


@admin.register(QuickTemplate, site=anlaufstelle_admin_site)
class QuickTemplateAdmin(FacilityScopedAdminMixin, ModelAdmin):
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


@admin.register(TimeFilter, site=anlaufstelle_admin_site)
class TimeFilterAdmin(FacilityScopedAdminMixin, ModelAdmin):
    list_display = ("label", "start_time", "end_time", "is_default", "is_active", "facility")
    list_filter = ("is_active", "is_default", "facility")
