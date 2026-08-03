"""Forms for client management."""

from typing import ClassVar

from django import forms
from django.core.exceptions import ValidationError
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

from core.forms.dynamic import DynamicDataForm
from core.forms.widgets import INPUT_CSS
from core.models import Client, DocumentType


class DynamicClientDataForm(DynamicDataForm):
    """Personen-Auspraegung der dynamischen ``data_json``-Form (M2 P7, Refs #1058).

    Bewusst ohne eigenen Body — der komplette Feldaufbau liegt in
    :class:`~core.forms.dynamic.DynamicDataForm`. Einziger Unterschied zum
    Event-Pendant ist :attr:`exclude_file_fields`: FILE-Felder haben nur im
    Event-Pfad eine Attachment-/File-Vault-Pipeline (Entscheid E8/E9). Ein per
    Konfiguration dennoch zugeordnetes FILE-Feld wird beim Aufbau geloggt und
    uebersprungen, statt ein Feld zu erzeugen, dessen Upload nirgends landet.
    """

    exclude_file_fields: ClassVar[bool] = True


class ClientForm(forms.ModelForm):
    """Form for creating and editing clients."""

    class Meta:
        model = Client
        fields = ["pseudonym", "contact_stage", "age_cluster", "notes", "document_type"]
        widgets = {
            "pseudonym": forms.TextInput(attrs={"class": INPUT_CSS}),
            "contact_stage": forms.Select(attrs={"class": INPUT_CSS}),
            "age_cluster": forms.Select(attrs={"class": INPUT_CSS}),
            "notes": forms.Textarea(attrs={"class": INPUT_CSS, "rows": 4}),
            # M2 P7 (Refs #1058): HTMX-Attribute exakt wie am Event-Select
            # (``core/events/create.html``) — CSP-strikt, also KEIN Inline-
            # Handler; das Nachladen der Zusatzfelder macht htmx selbst.
            # ``reverse_lazy``: die URLconf ist zur Import-Zeit des Formulars
            # noch nicht aufgeloest.
            "document_type": forms.Select(
                attrs={
                    "class": INPUT_CSS,
                    "hx-get": reverse_lazy("core:client_fields_partial"),
                    "hx-target": "#dynamic-fields",
                    "hx-trigger": "change",
                    "hx-include": "[name='document_type']",
                }
            ),
        }

    def __init__(self, *args, facility=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.facility = facility
        self.user = user

        # M2 P7 (Refs #1058): Typ-Auswahl nach dem Muster ``EventMetaForm`` —
        # facility-scoped, nur aktive Typen, nur ``entity_type=client`` und nur
        # Sensitivitaeten, die die Rolle sehen darf. Ohne Facility bleibt die
        # Auswahl leer (fail-closed): ein ungescoptes Queryset waere ein
        # Cross-Tenant-Leck der Konfigurationsnamen.
        doc_type_field = self.fields["document_type"]
        doc_type_field.required = False
        doc_type_field.empty_label = _("— Kein Typ —")  # type: ignore[attr-defined]
        if facility:
            qs = DocumentType.objects.for_facility(facility).filter(
                is_active=True, entity_type=DocumentType.EntityType.CLIENT
            )
            if user is not None:
                from core.services.compliance import allowed_sensitivities_for_user

                qs = qs.filter(sensitivity__in=allowed_sensitivities_for_user(user))
        else:
            qs = DocumentType.objects.none()
        doc_type_field.queryset = qs  # type: ignore[attr-defined]

    def clean_pseudonym(self):
        pseudonym = self.cleaned_data["pseudonym"]
        qs = Client.objects.for_facility(self.facility).filter(pseudonym=pseudonym)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError(_("Eine Person mit diesem Pseudonym existiert bereits in dieser Einrichtung."))
        return pseudonym
