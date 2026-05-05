"""Forms for case management."""

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.forms.widgets import INPUT_CSS
from core.models import Case, Client, User


class CaseForm(forms.ModelForm):
    """Form for creating and editing cases."""

    client = forms.UUIDField(
        required=True,
        widget=forms.HiddenInput(),
        label=_("Klientel"),
        error_messages={
            "required": _("Bitte eine Person auswählen — Fälle müssen einer Person zugeordnet sein."),
        },
    )

    class Meta:
        model = Case
        fields = ["title", "description", "lead_user"]
        widgets = {
            "title": forms.TextInput(attrs={"class": INPUT_CSS}),
            "description": forms.Textarea(
                attrs={
                    "class": INPUT_CSS,
                    "rows": 4,
                }
            ),
            "lead_user": forms.Select(attrs={"class": INPUT_CSS}),
        }

    def __init__(self, *args, facility=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.facility = facility
        if facility:
            self.fields["lead_user"].queryset = User.objects.filter(  # type: ignore[attr-defined]
                facility=facility,
                role__in=[User.Role.STAFF, User.Role.LEAD, User.Role.ADMIN],
            )
        self.fields["lead_user"].required = False

    def clean_client(self):
        client_id = self.cleaned_data.get("client")
        if not client_id:
            raise ValidationError(_("Bitte eine Person auswählen — Fälle müssen einer Person zugeordnet sein."))
        try:
            client_obj = Client.objects.get(pk=client_id)
        except Client.DoesNotExist as exc:
            raise ValidationError(_("Klientel existiert nicht.")) from exc
        if self.facility and client_obj.facility_id != self.facility.pk:
            raise ValidationError(_("Klientel gehört nicht zur Einrichtung."))
        return client_obj
