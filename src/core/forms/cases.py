"""Forms for case management."""

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.models import Case, Client, User

# Tailwind-Klassen fuer Form-Inputs (Theme Gruen, siehe Plan #663)
INPUT_CSS = "w-full bg-canvas border border-subtle rounded-md px-3 py-2 text-[13px] text-ink"


class CaseForm(forms.ModelForm):
    """Form for creating and editing cases."""

    client = forms.UUIDField(
        required=False,
        widget=forms.HiddenInput(),
        label=_("Klientel"),
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
            self.fields["lead_user"].queryset = User.objects.filter(
                facility=facility,
                role__in=[User.Role.STAFF, User.Role.LEAD, User.Role.ADMIN],
            )
        self.fields["lead_user"].required = False

    def clean_client(self):
        client_id = self.cleaned_data.get("client")
        if not client_id:
            return None
        try:
            client_obj = Client.objects.get(pk=client_id)
        except Client.DoesNotExist:
            raise ValidationError(_("Klientel existiert nicht."))
        if self.facility and client_obj.facility_id != self.facility.pk:
            raise ValidationError(_("Klientel gehört nicht zur Einrichtung."))
        return client_obj
