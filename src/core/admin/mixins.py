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

from core.models import Facility

logger = logging.getLogger(__name__)


class RoleBasedPermissionMixin:
    """Delegiert ModelAdmin-Permissions an ``self.admin_site.has_role_permission``.

    Einzelne ModelAdmins (EventHistoryAdmin, AuditLogAdmin, etc.) ueberschreiben
    has_add/change/delete_permission explizit auf False — das gewinnt durch MRO
    gegen diesen Mixin.
    """

    def has_view_permission(self, request, obj=None):
        return self.admin_site.has_role_permission(request)

    def has_add_permission(self, request):
        return self.admin_site.has_role_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.admin_site.has_role_permission(request)

    def has_delete_permission(self, request, obj=None):
        return self.admin_site.has_role_permission(request)


class SuperAdminOnlyAdminMixin:
    """Permissions nur fuer super_admin (A2.3, Refs #1021).

    Fuer installationsweite Modelle oberhalb der Facility-Ebene (z.B.
    ``Organization``), die ein facility_admin nicht verwalten darf. Delegiert an
    ``self.admin_site.has_super_admin_permission`` (Single Source of Truth) —
    analog ``RoleBasedPermissionMixin``.
    """

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


class AdminReadAuditMixin:
    """Auditiert Cross-Facility-PII-Reads im Admin als ``SYSTEM_VIEW`` (AUTHZ-2).

    Refs #1341: Ein super_admin liest über die Admin-Listen/Change-Views
    facility-übergreifend Klientel-PII — analog zu ``/system/`` (wo
    :class:`~core.views.system.mixins.SystemAuditMixin` jeden Zugriff als
    ``SYSTEM_VIEW`` protokolliert) muss auch dieser Lesepfad tamper-evident
    protokolliert werden.

    Nur super_admin-Reads werden geloggt: ein facility_admin ist über
    :meth:`FacilityScopedAdminMixin.get_queryset` bereits auf die eigene
    Facility beschränkt (kein facility-übergreifender Read möglich), sodass ein
    same-facility-Read keinen Audit-Spam erzeugt — und wir setzen
    ``app.is_super_admin`` nicht fälschlich in einer facility_admin-Session
    (RLS-Bypass-Vermeidung). Die Changelist erzeugt genau **einen** Sammel-
    Audit pro Request (nicht pro Zeile).
    """

    def _audit_admin_read(self, request, target_obj=None, *, target_type=None, **detail):
        """Schreibt einen SYSTEM_VIEW-Audit, falls der Leser super_admin ist.

        Setzt vor dem INSERT die Postgres-Session-GUCs so, dass die WITH-CHECK-
        Policy (Migration 0083/0085) den facility=NULL-Eintrag durchlässt —
        exakt wie ``SystemAuditMixin``. Audit-Fehler dürfen den Read-Flow nicht
        kippen (defensiv geloggt).
        """
        if not getattr(request.user, "is_super_admin", False):
            return
        # Lazy-Imports gegen Zirkularitäten (mixins <-> services/signals).
        from core.services.audit import audit_admin_view
        from core.signals.audit import _set_session_vars

        _set_session_vars(None, is_super_admin=True)
        try:
            audit_admin_view(request, target_obj, target_type=target_type, **detail)
        except Exception:
            logger.exception("Admin-Read-Audit (SYSTEM_VIEW) fehlgeschlagen")

    def change_view(self, request, object_id, form_url="", extra_context=None):
        obj = self.get_object(request, unquote(object_id))
        if obj is not None:
            self._audit_admin_read(request, obj, admin_view="change")
        return super().change_view(request, object_id, form_url, extra_context)

    def changelist_view(self, request, extra_context=None):
        # EIN Sammel-Audit pro Changelist-Request (nicht pro Zeile): target_type
        # = Model-Name, ohne target_id.
        self._audit_admin_read(request, target_type=self.model.__name__, admin_view="changelist")
        return super().changelist_view(request, extra_context)


class ReadOnlyDomainAdminMixin(FacilityScopedAdminMixin):
    """Fachobjekte im Admin strikt read-only (AUTHZ-1).

    Refs #1341: Client/Case/Event/WorkItem tragen reiche Service-Invarianten
    (Feld-Verschlüsselung, EventHistory-Diff, Vier-Augen-Löschung, Legal-Hold/
    Retention) und ihr Domänen-AuditLog entsteht ausschließlich in der
    Service-Schicht. Ein direkter Admin-Save würde all das umgehen. Deshalb ist
    Schreiben (Add/Change/Delete) gesperrt; die Read-Only-Sicht (View) bleibt
    erhalten — konsistent mit den bereits append-only gestellten Admins
    (EventHistory/EventAttachment/AuditLog/DeletionRequest).

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
