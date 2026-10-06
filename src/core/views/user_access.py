"""Fach-UI fuer Benutzer und Zugaenge einer Einrichtung (Refs #1871)."""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView
from django_ratelimit.decorators import ratelimit

from core.constants import RATELIMIT_MUTATION
from core.forms.user_access import UserAccessCreateForm, UserAccessUpdateForm
from core.models.user import SessionRevocationError
from core.services.security import RequireSudoModeMixin, is_locked
from core.services.user_access import (
    create_invited_user,
    get_manageable_user,
    has_second_factor,
    list_user_access,
    resend_user_invitation,
    reset_managed_user_mfa,
    unlock_managed_user,
    update_user_access,
)
from core.signals.audit import get_client_ip
from core.views.mixins import FacilityAdminRequiredMixin


class _NoStoreMixin:
    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


class UserAccessListView(FacilityAdminRequiredMixin, _NoStoreMixin, TemplateView):
    template_name = "core/user_access/list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["access_rows"] = list_user_access(self.request.current_facility)
        return context


@method_decorator(ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True), name="post")
class UserAccessCreateView(FacilityAdminRequiredMixin, RequireSudoModeMixin, _NoStoreMixin, View):
    template_name = "core/user_access/form.html"

    def _form(self, request, data=None):
        return UserAccessCreateForm(data, facility=request.current_facility, actor=request.user)

    def get(self, request):
        return render(request, self.template_name, {"form": self._form(request), "is_create": True})

    def post(self, request):
        form = self._form(request, request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "is_create": True})
        result = create_invited_user(
            facility=request.current_facility,
            actor=request.user,
            ip_address=get_client_ip(request),
            **form.cleaned_data,
        )
        if result.sent:
            messages.success(request, _("Zugang wurde angelegt und die Einladung versendet."))
        else:
            messages.warning(
                request,
                _("Zugang wurde angelegt, die Einladung konnte aber nicht versendet werden. Bitte erneut senden."),
            )
        return redirect("core:user_access_update", pk=result.user.pk)


@method_decorator(ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True), name="post")
class UserAccessUpdateView(FacilityAdminRequiredMixin, RequireSudoModeMixin, _NoStoreMixin, View):
    template_name = "core/user_access/form.html"

    def _target(self, request, pk):
        return get_manageable_user(request.current_facility, pk)

    def _context(self, form, target):
        return {
            "form": form,
            "target_user": target,
            "is_create": False,
            "account_is_locked": is_locked(target),
            "can_resend_invite": bool(target.email and target.must_change_password),
            "can_reset_mfa": target.pk != self.request.user.pk and has_second_factor(target),
        }

    def get(self, request, pk):
        target = self._target(request, pk)
        form = UserAccessUpdateForm(
            instance=target,
            facility=request.current_facility,
            actor=request.user,
        )
        return render(request, self.template_name, self._context(form, target))

    def post(self, request, pk):
        target = self._target(request, pk)
        form = UserAccessUpdateForm(
            request.POST,
            instance=target,
            facility=request.current_facility,
            actor=request.user,
        )
        if not form.is_valid():
            return render(request, self.template_name, self._context(form, target))
        updated = update_user_access(
            target,
            facility=request.current_facility,
            actor=request.user,
            ip_address=get_client_ip(request),
            **form.cleaned_data,
        )
        messages.success(request, _("Zugang wurde gespeichert."))
        return redirect("core:user_access_update", pk=updated.pk)


@method_decorator(ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True), name="post")
class UserAccessInviteView(FacilityAdminRequiredMixin, RequireSudoModeMixin, _NoStoreMixin, View):
    def post(self, request, pk):
        target = get_manageable_user(request.current_facility, pk)
        try:
            sent = resend_user_invitation(
                target,
                facility=request.current_facility,
                actor=request.user,
                ip_address=get_client_ip(request),
            )
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        else:
            if sent:
                messages.success(request, _("Einladung wurde erneut versendet."))
            else:
                messages.error(request, _("Einladung konnte nicht versendet werden."))
        return redirect("core:user_access_update", pk=target.pk)


@method_decorator(ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True), name="post")
class UserAccessUnlockView(FacilityAdminRequiredMixin, RequireSudoModeMixin, _NoStoreMixin, View):
    def post(self, request, pk):
        target = get_manageable_user(request.current_facility, pk)
        if unlock_managed_user(
            target,
            facility=request.current_facility,
            actor=request.user,
            ip_address=get_client_ip(request),
        ):
            messages.success(request, _("Account-Sperre wurde aufgehoben."))
        else:
            messages.info(request, _("Der Zugang war nicht gesperrt."))
        return redirect("core:user_access_update", pk=target.pk)


@method_decorator(ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True), name="post")
class UserAccessMFAResetView(FacilityAdminRequiredMixin, RequireSudoModeMixin, _NoStoreMixin, View):
    """Reset the second factor of another account of the facility (Refs #2469).

    GET shows a confirmation page -- it is also where the sudo step returns
    to. POST removes TOTP devices, passkeys and backup codes via
    ``reset_managed_user_mfa``. The own account is refused with 403 before
    anything renders; the service repeats that check.
    """

    template_name = "core/user_access/mfa_reset_confirm.html"

    def _target(self, request, pk):
        target = get_manageable_user(request.current_facility, pk)
        if target.pk == request.user.pk:
            raise PermissionDenied("The own second factor cannot be reset through account management.")
        return target

    def get(self, request, pk):
        target = self._target(request, pk)
        return render(
            request,
            self.template_name,
            {"target_user": target, "has_second_factor": has_second_factor(target)},
        )

    def post(self, request, pk):
        target = self._target(request, pk)
        try:
            result = reset_managed_user_mfa(
                target,
                facility=request.current_facility,
                actor=request.user,
                ip_address=get_client_ip(request),
            )
        except SessionRevocationError:
            messages.error(
                request,
                _(
                    "Der zweite Faktor wurde nicht zurückgesetzt: "
                    "die Sitzungen des Zugangs ließen sich nicht sicher beenden."
                ),
            )
            return redirect("core:user_access_update", pk=target.pk)
        if result.removed_anything:
            messages.success(
                request,
                _("Der zweite Faktor wurde zurückgesetzt und alle Sitzungen des Zugangs wurden beendet."),
            )
        else:
            messages.info(request, _("Für diesen Zugang ist kein zweiter Faktor eingerichtet."))
        return redirect("core:user_access_update", pk=target.pk)
