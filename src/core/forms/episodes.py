"""Forms for episode management."""

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.forms.dynamic import configure_entity_document_type
from core.forms.entity_details import EntityDetailsMixin
from core.forms.mixins import TailwindFormMixin
from core.models import DocumentType
from core.models.episode import Episode
from core.services.case.episodes import episode_basis_token, validate_episode_dates
from core.utils.date_input import AppDateInput


class EpisodeForm(EntityDetailsMixin, TailwindFormMixin, forms.ModelForm):
    """Form for creating and editing episodes."""

    basis_token = forms.CharField(widget=forms.HiddenInput, required=False)

    class Meta:
        model = Episode
        fields = ["title", "started_at", "ended_at", "document_type"]
        help_texts = {
            "title": _(
                "Kurz halten: keine Klarnamen und keine Angaben nach Art. 9 DSGVO. "
                "Dieses Feld wird unverschlüsselt gespeichert."
            )
        }
        widgets = {
            "started_at": AppDateInput(
                attrs={
                    "type": "date",
                },
            ),
            "ended_at": AppDateInput(
                attrs={
                    "type": "date",
                },
            ),
        }

    def __init__(self, *args, facility=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.details_original_keys = set(self.data)
        configure_entity_document_type(self, facility, DocumentType.EntityType.EPISODE)
        self.configure_details(facility, user, "episode")
        if self.instance.pk and not self.instance._state.adding:
            self.initial["basis_token"] = episode_basis_token(self.instance)
        else:
            self.fields.pop("basis_token")
        self.fields["ended_at"].required = False
        self.fields["started_at"].label = _("Beginn")  # pragma: no mutate
        self.fields["ended_at"].label = _("Ende")  # pragma: no mutate

    def clean(self):
        cleaned_data = super().clean() or {}
        try:
            validate_episode_dates(cleaned_data.get("started_at"), cleaned_data.get("ended_at"))
        except ValidationError as exc:
            self.add_error(None, exc)
        return cleaned_data
