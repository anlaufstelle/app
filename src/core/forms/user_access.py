"""Formulare fuer den Zugaenge-Reiter der Einrichtungsverwaltung (#1871)."""

from __future__ import annotations

from typing import cast

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.forms.mixins import TailwindFormMixin
from core.models import User

_CREATE_FIELDS = (
    "username",
    "email",
    "display_name",
    "role",
    "mfa_required",
    "can_confirm_deletion",
)
_UPDATE_FIELDS = (*_CREATE_FIELDS[:4], "is_active", *_CREATE_FIELDS[4:])


class _UserAccessForm(TailwindFormMixin, forms.ModelForm):
    """Gemeinsame Guards fuer einrichtungsgebundene Benutzerformulare."""

    def __init__(self, *args, facility, actor, **kwargs):
        self.facility = facility
        self.actor = actor
        super().__init__(*args, **kwargs)
        role_field = cast(forms.ChoiceField, self.fields["role"])
        role_field.choices = [choice for choice in User.Role.choices if choice[0] != User.Role.SUPER_ADMIN]
        self.fields["email"].help_text = _("An diese Adresse wird der persönliche Setup-Link gesendet.")
        self.fields["mfa_required"].help_text = _(
            "Erzwingt die Einrichtung eines zweiten Faktors beim nächsten Zugang."
        )

    def clean_role(self):
        role = self.cleaned_data["role"]
        if role == User.Role.SUPER_ADMIN:
            raise ValidationError(_("Die Rolle Systemadministration kann hier nicht vergeben werden."))
        if self.instance.pk == getattr(self.actor, "pk", None) and role != User.Role.FACILITY_ADMIN:
            raise ValidationError(_("Die eigene Rolle kann hier nicht herabgestuft werden."))
        return role


class UserAccessCreateForm(_UserAccessForm):
    class Meta:
        model = User
        fields = _CREATE_FIELDS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].required = True


class UserAccessUpdateForm(_UserAccessForm):
    class Meta:
        model = User
        fields = _UPDATE_FIELDS

    def clean_is_active(self):
        is_active = self.cleaned_data["is_active"]
        if self.instance.pk == getattr(self.actor, "pk", None) and not is_active:
            raise ValidationError(_("Der eigene Zugang kann hier nicht deaktiviert werden."))
        return is_active
