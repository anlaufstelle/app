"""Admin fuer DocumentType, FieldTemplate, QuickTemplate, TimeFilter (Refs #785, #958)."""

from django.contrib import admin, messages
from unfold.admin import ModelAdmin, TabularInline

from core.admin.mixins import FacilityScopedAdminMixin
from core.admin_site import anlaufstelle_admin_site
from core.models import DocumentType, DocumentTypeField, FieldGroup, FieldTemplate, QuickTemplate, TimeFilter


class DocumentTypeFieldInline(TabularInline):
    model = DocumentTypeField
    extra = 1
    autocomplete_fields = ("field_template",)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """``field_group``-Dropdown auf den Parent-DocumentType scopen (Refs #1711).

        Ohne dieses Scoping listet der Django-Admin ALLE FieldGroups (auch
        fremder Facilities/DocumentTypes) — irrefuehrende UX (das Modell-
        ``clean()`` verhindert das Speichern zwar serverseitig, s. dort).
        Pflege der Gruppen laeuft primaer ueber die Config-UI; der Admin
        bleibt Super-Admin-Fallback. ``DocumentTypeAdmin.get_form`` legt das
        Parent-Objekt vorher auf ``request._doctype_parent_obj`` ab — im
        Add-View (neuer, noch ungespeicherter DocumentType) ist das ``None``
        und das Dropdown bleibt entsprechend leer (es gibt noch keine
        Gruppen).
        """
        if db_field.name == "field_group":
            parent = getattr(request, "_doctype_parent_obj", None)
            kwargs["queryset"] = (
                FieldGroup.objects.filter(document_type=parent) if parent else FieldGroup.objects.none()
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(DocumentType, site=anlaufstelle_admin_site)
class DocumentTypeAdmin(FacilityScopedAdminMixin, ModelAdmin):
    list_display = ("name", "sensitivity", "system_type", "facility", "is_active", "sort_order")
    list_filter = ("sensitivity", "is_active", "facility", "system_type")
    search_fields = ("name",)
    inlines = [DocumentTypeFieldInline]

    def get_readonly_fields(self, request, obj=None):
        readonly = self.readonly_fields
        if obj and obj.system_type:
            readonly = (*readonly, "system_type")
        if obj and obj.report_key:
            # E3 (Refs #1849), Entscheid E-K: gesperrt wird NUR ein bereits
            # gesetzter Berichtsschluessel — leer→gesetzt bleibt auch hier der
            # Reparaturpfad. Dieselbe Mechanik wie ``system_type``: Django
            # schliesst ``readonly_fields`` von ``modelform_factory`` aus, ein
            # POST kann den Guard also gar nicht mehr erreichen. Ohne diese
            # Zeile leerte JEDER Change-Speichervorgang im Admin das Feld (der
            # POST traegt es nicht) und liefe in den Guard — HTTP 500 auf einer
            # voellig unbeteiligten Aenderung.
            readonly = (*readonly, "report_key")
        if obj:
            # Fix-Runde 1 (Refs #1058, B-1): slug/entity_type sind nach dem
            # Anlegen unveraenderlich (Guard in DocumentType.save()). Ohne
            # readonly waeren beide im Change-ModelForm editierbar — Django
            # schliesst readonly_fields dagegen von modelform_factory aus
            # (dieselbe Mechanik wie system_type oben), also kann eine
            # Aenderung ueber die Admin-UI den Guard gar nicht mehr erreichen
            # und den vormals unabgefangenen ValidationError->HTTP-500 ausloesen.
            readonly = (*readonly, "slug", "entity_type")
        return readonly

    def get_form(self, request, obj=None, **kwargs):
        """Parent-DocumentType fuer den Inline-Scope auf dem Request merken.

        Refs #1711: ``DocumentTypeFieldInline.formfield_for_foreignkey`` liest
        ``request._doctype_parent_obj``, um das ``field_group``-Dropdown auf
        die Gruppen dieses DocumentType zu beschraenken. Django ruft
        ``get_form`` (Haupt-Formular) vor den Inline-Formsets auf — der Wert
        steht rechtzeitig zur Verfuegung.
        """
        request._doctype_parent_obj = obj
        form = super().get_form(request, obj, **kwargs)
        if obj is None:
            # Fix-Runde 1 (Refs #1058, B-1, Zweitwirkung): slug ist beim
            # Anlegen kein readonly-Feld (es existiert ja noch kein Wert), war
            # aber als normales (Pflicht-)Feld im Add-ModelForm vorhanden — ein
            # Admin haette ihn von Hand tippen muessen statt die Model-
            # Auto-Generierung (``DocumentType.save()``) greifen zu lassen.
            # Ganz aus dem Add-Formular nehmen: kein POST-Wert -> ``save()``
            # generiert ihn automatisch aus dem Namen (Muster FieldTemplate).
            form.base_fields.pop("slug", None)
        return form


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
        "report_key",
        "help_text",
        "is_active",
    )
    actions = ("deactivate_selected", "activate_selected")

    def get_readonly_fields(self, request, obj=None):
        readonly = self.readonly_fields
        if obj:
            readonly = (*readonly, "slug")
        # E3 (Refs #1849), Entscheid E-K — dieselbe Regel wie am
        # Dokumentationstyp: gesperrt erst, wenn ein Wert dasteht.
        if obj and obj.report_key:
            readonly = (*readonly, "report_key")
        return readonly

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
        from core.services.dashboard import filter_prefilled_data

        obj.prefilled_data = filter_prefilled_data(obj.document_type, obj.prefilled_data or {})
        if not change and obj.created_by_id is None:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(TimeFilter, site=anlaufstelle_admin_site)
class TimeFilterAdmin(FacilityScopedAdminMixin, ModelAdmin):
    list_display = ("label", "start_time", "end_time", "is_default", "is_active", "facility")
    list_filter = ("is_active", "is_default", "facility")
