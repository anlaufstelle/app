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
        label=_("Person"),
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
                role__in=[User.Role.STAFF, User.Role.LEAD, User.Role.FACILITY_ADMIN],
            )
        self.fields["lead_user"].required = False

    def clean_client(self):
        client_id = self.cleaned_data.get("client")
        if not client_id:
            raise ValidationError(_("Bitte eine Person auswählen — Fälle müssen einer Person zugeordnet sein."))
        # Refs #819 (R-006): scoped Query — eine Person aus einer fremden
        # Facility liefert direkt DoesNotExist statt der nachgelagerten
        # Facility-Pruefung. Defense-in-Depth gegen ID-Erraten.
        scoped = Client.objects.for_facility(self.facility) if self.facility else Client.objects.all()
        try:
            return scoped.get(pk=client_id)
        except Client.DoesNotExist as exc:
            raise ValidationError(_("Person existiert nicht.")) from exc
