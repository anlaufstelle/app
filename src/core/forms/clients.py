"""Forms for client management."""

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.forms.widgets import INPUT_CSS
from core.models import Client


class ClientForm(forms.ModelForm):
    """Form for creating and editing clients."""

    class Meta:
        model = Client
        fields = ["pseudonym", "contact_stage", "age_cluster", "notes"]
        widgets = {
            "pseudonym": forms.TextInput(attrs={"class": INPUT_CSS}),
            "contact_stage": forms.Select(attrs={"class": INPUT_CSS}),
            "age_cluster": forms.Select(attrs={"class": INPUT_CSS}),
            "notes": forms.Textarea(attrs={"class": INPUT_CSS, "rows": 4}),
        }

    def __init__(self, *args, facility=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.facility = facility

    def clean_pseudonym(self):
        pseudonym = self.cleaned_data["pseudonym"]
        qs = Client.objects.for_facility(self.facility).filter(pseudonym=pseudonym)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError(_("Ein Klientel mit diesem Pseudonym existiert bereits in dieser Einrichtung."))
        return pseudonym
