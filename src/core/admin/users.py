"""Admin fuer User-Verwaltung inkl. Invite-Flow + Unlock-Action (Refs #785, #958)."""

import secrets
import string

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from unfold.admin import ModelAdmin
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from core.admin.mixins import FacilityScopedAdminMixin
from core.admin_site import anlaufstelle_admin_site
from core.models import User
from core.services.security import is_locked as user_is_locked
from core.services.security import send_invite_email
from core.services.security import unlock as unlock_user

_INITIAL_PASSWORD_ALPHABET = string.ascii_letters + string.digits
_INITIAL_PASSWORD_LENGTH = 12


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


@admin.register(User, site=anlaufstelle_admin_site)
class UserAdmin(FacilityScopedAdminMixin, BaseUserAdmin, ModelAdmin):
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
            password = "".join(secrets.choice(_INITIAL_PASSWORD_ALPHABET) for _ in range(_INITIAL_PASSWORD_LENGTH))
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
