"""Forms for episode management."""

from django import forms
from django.utils.translation import gettext_lazy as _

from core.forms.mixins import TailwindFormMixin
from core.models.episode import Episode


class EpisodeForm(TailwindFormMixin, forms.ModelForm):
    """Form for creating and editing episodes."""

    class Meta:
        model = Episode
        fields = ["title", "description", "started_at", "ended_at"]
        widgets = {
            "description": forms.Textarea(
                attrs={
                    "rows": 4,
                }
            ),
            "started_at": forms.DateInput(
                attrs={
                    "type": "date",
                },
            ),
            "ended_at": forms.DateInput(
                attrs={
                    "type": "date",
                },
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["ended_at"].required = False
        self.fields["description"].required = False
        self.fields["started_at"].label = _("Beginn")  # pragma: no mutate
        self.fields["ended_at"].label = _("Ende")  # pragma: no mutate
