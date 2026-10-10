"""Record-level protection and downgrade request forms."""

from django import forms
from django.utils.translation import gettext_lazy as _

from core.forms.mixins import TailwindFormMixin


class ProtectionReasonForm(TailwindFormMixin, forms.Form):
    reason = forms.CharField(
        label=_("Begründung"),
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Bitte keine Klarnamen oder besonders schützenswerten Angaben eintragen."),
    )


class LegalHoldForm(ProtectionReasonForm):
    expires_at = forms.DateField(
        label=_("Ablaufdatum"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
