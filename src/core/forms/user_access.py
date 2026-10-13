"""Formulare fuer den Zugaenge-Reiter der Einrichtungsverwaltung (#1871)."""

from __future__ import annotations

from typing import cast

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.forms.mixins import TailwindFormMixin
from core.models import User
from core.services.user_access import user_access_basis_token

_CREATE_FIELDS = (
    "username",
    "email",
    "display_name",
    "role",
    "mfa_required",
    "can_confirm_deletion",
)
_UPDATE_FIELDS = (*_CREATE_FIELDS[:4], "is_active", *_CREATE_FIELDS[4:])

# Roles that receive the deletion approval right by default when an account is created.
DELETION_APPROVER_DEFAULT_ROLES = frozenset({User.Role.FACILITY_ADMIN, User.Role.LEAD})
_BY_ROLE, _YES, _NO = "role", "yes", "no"


def deletion_approval_field() -> forms.ChoiceField:
    """Creation-time choice for ``can_confirm_deletion``, preset to the role default."""
    return forms.ChoiceField(
        label=User._meta.get_field("can_confirm_deletion").verbose_name,
        choices=(
            (_BY_ROLE, _("Nach Rolle: ja für Leitung und Anwendungsbetreuung, sonst nein")),
            (_YES, _("Ja")),
            (_NO, _("Nein")),
        ),
        initial=_BY_ROLE,
        required=False,
        help_text=_(
            "Darf Löschanträge als zweite Person genehmigen; eigene Anträge bleiben ausgeschlossen. "
            "Jede Einrichtung braucht mindestens zwei Konten mit diesem Recht."
        ),
    )


def resolve_deletion_approval(choice: str | None, role: str | None) -> bool:
    """Boolean value of the creation-time choice; an empty choice follows the role."""
    if choice == _YES:
        return True
    if choice == _NO:
        return False
    return role in DELETION_APPROVER_DEFAULT_ROLES


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
    can_confirm_deletion = deletion_approval_field()

    class Meta:
        model = User
        fields = _CREATE_FIELDS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].required = True

    def clean_can_confirm_deletion(self):
        return resolve_deletion_approval(self.cleaned_data.get("can_confirm_deletion"), self.cleaned_data.get("role"))


class UserAccessUpdateForm(_UserAccessForm):
    basis_token = forms.CharField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.initial["basis_token"] = user_access_basis_token(self.instance)

    class Meta:
        model = User
        fields = _UPDATE_FIELDS

    def clean_is_active(self):
        is_active = self.cleaned_data["is_active"]
        if self.instance.pk == getattr(self.actor, "pk", None) and not is_active:
            raise ValidationError(_("Der eigene Zugang kann hier nicht deaktiviert werden."))
        return is_active
