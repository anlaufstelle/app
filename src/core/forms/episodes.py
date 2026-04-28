"""Forms for episode management."""

from django import forms
from django.utils.translation import gettext_lazy as _

from core.models.episode import Episode


class EpisodeForm(forms.ModelForm):
    """Form for creating and editing episodes."""

    class Meta:
        model = Episode
        fields = ["title", "description", "started_at", "ended_at"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "w-full border border-gray-300 rounded-md px-3 py-2"}),
            "description": forms.Textarea(
                attrs={
                    "class": "w-full border border-gray-300 rounded-md px-3 py-2",
                    "rows": 4,
                }
            ),
            "started_at": forms.DateInput(
                attrs={
                    "type": "date",
                    "class": "w-full border border-gray-300 rounded-md px-3 py-2",
                },
            ),
            "ended_at": forms.DateInput(
                attrs={
                    "type": "date",
                    "class": "w-full border border-gray-300 rounded-md px-3 py-2",
                },
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["ended_at"].required = False
        self.fields["description"].required = False
        self.fields["started_at"].label = _("Beginn")
        self.fields["ended_at"].label = _("Ende")
