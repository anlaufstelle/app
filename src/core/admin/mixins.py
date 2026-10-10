"""Admin-Mixins: Delegates an die zentralisierte AdminSite-Logik.

Refs #785 — Custom AdminSite mit Sudo + Rollen-Gate.
Refs #958 (M-1) — aus ``core/admin.py`` extrahiert.
Refs #958 (M-2) — Rollen-/Facility-Logik in ``AnlaufstelleAdminSite`` zentralisiert;
die Mixins delegieren jetzt an ``self.admin_site.has_role_permission(...)`` und
``self.admin_site.scope_to_facility(...)``, damit es nur eine Definition gibt.

Wenn sich die Rollen-Logik aendert (Stichwort: 5-Rollen-Modell, weitere Sub-Rollen)
gibt es genau **einen** Ort, an dem das passieren muss:
``core/admin_site.AnlaufstelleAdminSite``.
"""

import logging

from django.contrib.admin.utils import unquote
from django.core.exceptions import PermissionDenied
from django.db import transaction

from core.models import Facility

logger = logging.getLogger(__name__)


def require_admin_read_audit(request, target_obj=None, *, target_type=None, **detail):
    """Deny super-admin reads if the shared audit writer is unavailable."""
    if not getattr(request.user, "is_super_admin", False):
        return
    from core.services.audit import audit_admin_view
    from core.signals.audit import _set_session_vars

    try:
        _set_session_vars(None, is_super_admin=True)
        audit_admin_view(request, target_obj, target_type=target_type, **detail)
    except Exception as exc:
        logger.exception("Admin read audit failed")
        raise PermissionDenied from exc


class AdminReadAuditMixin:
    """Require a successful audit before super-admin content is returned."""

    def _audit_admin_read(self, request, target_obj=None, *, target_type=None, **detail):
        require_admin_read_audit(request, target_obj, target_type=target_type, **detail)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        self._audit_admin_read(
            request, target_type=self.model.__name__, target_id=unquote(object_id), admin_view="change"
        )
        return super().change_view(request, object_id, form_url, extra_context)

    def changelist_view(self, request, extra_context=None):
        self._audit_admin_read(request, target_type=self.model.__name__, admin_view="changelist")
        return super().changelist_view(request, extra_context)

    def add_view(self, request, form_url="", extra_context=None):
        self._audit_admin_read(request, target_type=self.model.__name__, admin_view="add")
        return super().add_view(request, form_url, extra_context)

    def delete_view(self, request, object_id, extra_context=None):
        self._audit_admin_read(
            request, target_type=self.model.__name__, target_id=unquote(object_id), admin_view="delete"
        )
        return super().delete_view(request, object_id, extra_context)

    def history_view(self, request, object_id, extra_context=None):
        self._audit_admin_read(
            request, target_type=self.model.__name__, target_id=unquote(object_id), admin_view="history"
        )
        return super().history_view(request, object_id, extra_context)

    def get_search_results(self, request, queryset, search_term):
        if getattr(request, "resolver_match", None) and request.resolver_match.url_name == "autocomplete":
            self._audit_admin_read(request, target_type=self.model.__name__, admin_view="autocomplete")
        return super().get_search_results(request, queryset, search_term)


class AdminWriteAuditMixin:
    """Audit admin mutations atomically, including bulk deletion and inlines."""

    admin_audit_save_actions = ("create", "update")

    def get_admin_audit_changed_fields(self, request, obj, form, change):
        return list(getattr(form, "changed_data", ()))

    def _audit_admin_write(self, request, obj, action, changed_fields, *, target_id=None):
        from core.models import AuditLog
        from core.services.audit import audit_event
        from core.signals.audit import get_client_ip

        facility = (
            (None if action == "delete" else obj) if isinstance(obj, Facility) else getattr(obj, "facility", None)
        )
        if facility is None and hasattr(obj, "document_type"):
            facility = obj.document_type.facility
        audit_event(
            getattr(AuditLog.Action, f"CONFIG_{action.upper()}"),
            user=request.user,
            actor=request.user,
            facility=facility,
            target_obj=obj,
            target_id=target_id,
            detail={"changed_fields": changed_fields},
            ip_address=get_client_ip(request),
        )

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        action = "update" if change else "create"
        changed_fields = self.get_admin_audit_changed_fields(request, obj, form, change)
        super().save_model(request, obj, form, change)
        if action in self.admin_audit_save_actions and changed_fields is not None:
            self._audit_admin_write(request, obj, action, changed_fields)

    @transaction.atomic
    def delete_model(self, request, obj):
        from core.models import DocumentType, FieldTemplate
        from core.services.dynamic_protection import lock_dynamic_protection

        if isinstance(obj, (DocumentType, FieldTemplate)):
            lock_dynamic_protection(obj.facility_id, exclusive=True)
        target_id = str(obj.pk)
        # Record the target before deletion clears its primary key.
        # The transaction rolls this entry back if deletion is rejected.
        self._audit_admin_write(request, obj, "delete", [], target_id=target_id)
        super().delete_model(request, obj)

    @transaction.atomic
    def delete_queryset(self, request, queryset):
        from core.services.audit.chain import advisory_lock_id

        objects = list(queryset.order_by("pk"))
        from core.models import DocumentType, FieldTemplate
        from core.services.dynamic_protection import lock_dynamic_protection_many

        protection_facilities = {obj.facility_id for obj in objects if isinstance(obj, (DocumentType, FieldTemplate))}
        if protection_facilities:
            lock_dynamic_protection_many(protection_facilities, exclusive=True)
            # Configuration may have changed while waiting for the gates.
            objects = list(queryset.order_by("pk"))
        facility_ids = {obj.pk if isinstance(obj, Facility) else getattr(obj, "facility_id", None) for obj in objects}
        lock_ids = {fid: advisory_lock_id(fid) or 0 for fid in facility_ids}
        objects.sort(
            key=lambda obj: (
                lock_ids[obj.pk if isinstance(obj, Facility) else getattr(obj, "facility_id", None)],
                str(obj.pk),
            )
        )
        for obj in objects:
            self.delete_model(request, obj)

    @transaction.atomic
    def save_formset(self, request, form, formset, change):
        mutations = []
        for inline_form in formset.forms:
            if not inline_form.has_changed():
                continue
            obj = inline_form.instance
            if inline_form.cleaned_data.get("DELETE"):
                if not obj._state.adding:
                    mutations.append((obj, "delete", [], str(obj.pk)))
            else:
                action = "create" if obj._state.adding else "update"
                fields = [name for name in inline_form.changed_data if name != "DELETE"]
                mutations.append((obj, action, fields, None))
        super().save_formset(request, form, formset, change)
        for obj, action, changed_fields, target_id in mutations:
            self._audit_admin_write(request, obj, action, changed_fields, target_id=target_id)


class RoleBasedPermissionMixin(AdminReadAuditMixin, AdminWriteAuditMixin):
    """Delegiert ModelAdmin-Permissions an ``self.admin_site.has_role_permission``.

    Einzelne ModelAdmins (EventHistoryAdmin, AuditLogAdmin, etc.) ueberschreiben
    has_add/change/delete_permission explizit auf False — das gewinnt durch MRO
    gegen diesen Mixin.
    """

    def has_module_permission(self, request):
        return self.admin_site.has_role_permission(request)

    def has_view_permission(self, request, obj=None):
        return self.admin_site.has_role_permission(request)

    def has_add_permission(self, request):
        return self.admin_site.has_role_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.admin_site.has_role_permission(request)

    def has_delete_permission(self, request, obj=None):
        return self.admin_site.has_role_permission(request)


class SuperAdminOnlyAdminMixin(AdminReadAuditMixin, AdminWriteAuditMixin):
    """Permissions nur fuer super_admin (A2.3, Refs #1021).

    Fuer installationsweite Modelle oberhalb der Facility-Ebene (z.B.
    ``Organization``), die ein facility_admin nicht verwalten darf. Delegiert an
    ``self.admin_site.has_super_admin_permission`` (Single Source of Truth) —
    analog ``RoleBasedPermissionMixin``.
    """

    def has_module_permission(self, request):
        return self.admin_site.has_super_admin_permission(request)

    def has_view_permission(self, request, obj=None):
        return self.admin_site.has_super_admin_permission(request)

    def has_add_permission(self, request):
        return self.admin_site.has_super_admin_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.admin_site.has_super_admin_permission(request)

    def has_delete_permission(self, request, obj=None):
        return self.admin_site.has_super_admin_permission(request)


class FacilityScopedAdminMixin(RoleBasedPermissionMixin):
    """Delegiert Facility-Scoping an ``self.admin_site.scope_to_facility``.

    Verwendung als zusaetzliche Base-Klasse vor `ModelAdmin`:

        @admin.register(Client, site=anlaufstelle_admin_site)
        class ClientAdmin(FacilityScopedAdminMixin, ModelAdmin):
            ...
    """

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return self.admin_site.scope_to_facility(qs, request)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """facility-FK zentral auf die eigene Facility begrenzen (A2.2, Refs #1021).

        non-super_admin darf in JEDEM facility-gescopten Admin nur die eigene
        Facility zuweisen; der ``ModelChoiceField``-Queryset validiert die
        geposteten PK serverseitig. Single Source of Truth statt Duplikat pro
        ModelAdmin (vgl. die lokale A2.1-Loesung im ``UserAdmin``).
        """
        if db_field.name == "facility" and not request.user.is_super_admin:
            current = getattr(request, "current_facility", None)
            kwargs["queryset"] = Facility.objects.filter(pk=current.pk) if current else Facility.objects.none()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        """facility serverseitig erzwingen (A2.2, Refs #1021).

        Defense-in-Depth gegen umgangene Form-Validierung: ein non-super_admin
        kann ueber einen gefaelschten POST keine fremde Facility setzen — die
        eigene Facility wird vor dem Speichern erzwungen. super_admin behaelt
        die freie Wahl.
        """
        if not request.user.is_super_admin:
            current = getattr(request, "current_facility", None)
            if current is not None:
                obj.facility = current
        super().save_model(request, obj, form, change)


class ReadOnlyDomainAdminMixin(FacilityScopedAdminMixin):
    """Fachobjekte im Admin strikt read-only (AUTHZ-1).

    Refs #1341: Client/Case/Event/WorkItem tragen reiche Service-Invarianten
    (Feld-Verschlüsselung, EventHistory-Diff, Vier-Augen-Löschung, Legal-Hold/
    Retention) und ihr Domänen-AuditLog entsteht ausschließlich in der
    Service-Schicht. Ein direkter Admin-Save würde all das umgehen. Deshalb ist
    Schreiben (Add/Change/Delete) gesperrt; die Read-Only-Sicht (View) bleibt
    erhalten — konsistent mit den bereits append-only gestellten Admins
    (EventHistory/Attachment/AuditLog/DeletionRequest).

    Erbt das Facility-Scoping (get_queryset) aus
    :class:`FacilityScopedAdminMixin`; die Write-Pfade (save_model,
    formfield_for_foreignkey) bleiben ererbt, greifen aber wegen der gesperrten
    Permissions nicht mehr.
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
