"""Forms for WorkItem management."""

from django import forms
from django.utils.translation import gettext_lazy as _

from core.models import User, WorkItem
from core.models.client import Client

# Tailwind-Klassen fuer Form-Inputs (Theme Gruen, siehe Plan #663)
INPUT_CSS = "w-full bg-canvas border border-subtle rounded-md px-3 py-2 text-[13px] text-ink"


class WorkItemForm(forms.ModelForm):
    """Form for creating and editing WorkItems."""

    client = forms.UUIDField(required=False, widget=forms.HiddenInput())

    class Meta:
        model = WorkItem
        fields = [
            "item_type",
            "title",
            "description",
            "priority",
            "due_date",
            "remind_at",
            "recurrence",
            "assigned_to",
        ]
        widgets = {
            "item_type": forms.Select(attrs={"class": INPUT_CSS}),
            "title": forms.TextInput(attrs={"class": INPUT_CSS}),
            "description": forms.Textarea(
                attrs={
                    "class": INPUT_CSS,
                    "rows": 4,
                }
            ),
            "priority": forms.Select(attrs={"class": INPUT_CSS}),
            # Explizites ISO-Format (Refs #619): HTML5 `<input type="date">`
            # akzeptiert nur `YYYY-MM-DD`; ohne `format=` nimmt Django den
            # ersten Eintrag aus DATE_INPUT_FORMATS — unter LANGUAGE_CODE=de-DE
            # ist das `%d.%m.%Y`, wodurch der Prefill beim Edit leer bliebe.
            "due_date": forms.DateInput(
                format="%Y-%m-%d",
                attrs={
                    "type": "date",
                    "class": INPUT_CSS,
                },
            ),
            "remind_at": forms.DateInput(
                format="%Y-%m-%d",
                attrs={
                    "type": "date",
                    "class": INPUT_CSS,
                },
            ),
            "recurrence": forms.Select(attrs={"class": INPUT_CSS}),
            "assigned_to": forms.Select(attrs={"class": INPUT_CSS}),
        }

    def __init__(self, *args, facility=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.facility = facility
        if facility:
            self.fields["assigned_to"].queryset = User.objects.filter(
                facility=facility,
                is_active=True,
                role__in=[User.Role.ADMIN, User.Role.LEAD, User.Role.STAFF],
            ).order_by("username")
        self.fields["assigned_to"].required = False
        self.fields["description"].required = False
        # Recurrence has a DB default (NONE) — allow omitting it in POST.
        self.fields["recurrence"].required = False

    def clean_client(self):
        client_id = self.cleaned_data.get("client")
        if not client_id:
            return None
        try:
            return Client.objects.filter(
                pk=client_id,
                facility=self.facility,
            ).get()
        except Client.DoesNotExist:
            raise forms.ValidationError(_("Ungültige Klientel-ID"))

    def clean(self):
        cleaned = super().clean()
        remind_at = cleaned.get("remind_at")
        due_date = cleaned.get("due_date")
        if remind_at and due_date and remind_at > due_date:
            raise forms.ValidationError({"remind_at": _("Die Erinnerung muss vor oder am Fälligkeitstag liegen.")})
        return cleaned
