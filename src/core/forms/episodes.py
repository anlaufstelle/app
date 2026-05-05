"""Forms for episode management."""

from django import forms
from django.utils.translation import gettext_lazy as _

from core.forms.widgets import INPUT_CSS
from core.models.episode import Episode


class EpisodeForm(forms.ModelForm):
    """Form for creating and editing episodes."""

    class Meta:
        model = Episode
        fields = ["title", "description", "started_at", "ended_at"]
        widgets = {
            "title": forms.TextInput(attrs={"class": INPUT_CSS}),
            "description": forms.Textarea(
                attrs={
                    "class": INPUT_CSS,
                    "rows": 4,
                }
            ),
            "started_at": forms.DateInput(
                attrs={
                    "type": "date",
                    "class": INPUT_CSS,
                },
            ),
            "ended_at": forms.DateInput(
                attrs={
                    "type": "date",
                    "class": INPUT_CSS,
                },
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["ended_at"].required = False
        self.fields["description"].required = False
        self.fields["started_at"].label = _("Beginn")
        self.fields["ended_at"].label = _("Ende")
