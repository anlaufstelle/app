"""Forms for client management."""

from typing import TYPE_CHECKING, Any, cast

from django import forms
from django.core.exceptions import ValidationError
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _

from core.forms.dynamic import DynamicDataForm, configure_entity_document_type
from core.forms.dynamic import document_type_label as _document_type_label  # noqa: F401
from core.forms.mixins import TailwindFormMixin
from core.models import Client, DocumentType
from core.services.entity_details import changes_hidden_source_values, visible_stored_details

# M2 P8 (Refs #1058): ``_document_type_label`` ist nach ``forms/dynamic.py``
# gezogen (der Fall-Picker braucht dieselbe Beschriftung). Der Alt-Name bleibt
# als Import-Alias erhalten — Muster P3/P7 (Umzug + Alias-Re-Export).


class DynamicClientDataForm(DynamicDataForm):
    """Personen-Auspraegung der dynamischen ``data_json``-Form (M2 P7, Refs #1058).

    Bewusst ohne eigenen Body — der komplette Feldaufbau liegt in
    :class:`~core.forms.dynamic.DynamicDataForm`.

    M24/AP4 (Refs #1738, ADR-039 §9): Der Skip ``exclude_file_fields = True``
    ist **ersatzlos entfallen**. Anhaenge haengen seit ADR-039 an Ereignis,
    Person oder Fall; ein FILE-Feld an einem Personen-Typ ist kein
    Fehlkonfigurations-Rest mehr, sondern ein regulaerer Upload mit
    Download-Route und Versionskette. Die Klasse bleibt (leer) bestehen —
    stabiler Importpfad und stabiler Logname.
    """


class _FacilityScopedPseudonymMixin:
    """Shared facility-local uniqueness check for full and inline forms."""

    if TYPE_CHECKING:
        cleaned_data: dict[str, Any]
        facility: Any
        instance: Client

    def clean_pseudonym(self):
        pseudonym = self.cleaned_data["pseudonym"]
        qs = Client.objects.for_facility(self.facility).filter(pseudonym=pseudonym)
        if self.instance and self.instance.pk and not self.instance._state.adding:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError(_("Eine Person mit diesem Pseudonym existiert bereits in dieser Einrichtung."))
        return pseudonym


class ClientInlineCreateForm(_FacilityScopedPseudonymMixin, TailwindFormMixin, forms.ModelForm):
    """Minimal client form embedded in the event-create flow (Refs #2039)."""

    min_stage = forms.ChoiceField(
        choices=Client.ContactStage.choices,
        required=False,
        widget=forms.HiddenInput,
    )

    class Meta:
        model = Client
        fields = ["pseudonym", "contact_stage"]
        widgets = {"pseudonym": forms.TextInput(attrs={"autofocus": True})}

    def __init__(self, *args, facility=None, min_stage=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.facility = facility
        if self.is_bound:
            self.fields["pseudonym"].widget.attrs.pop("autofocus", None)

        # Refs #2039/#507: Die Beschneidung vertraut nie ``self.data``. Die
        # View reicht den Suchkontext getrennt ein; fehlt er im POST oder ist
        # er manipuliert, gilt fail-closed die hoechste bekannte Stufe. Ein
        # ausdruecklicher Leerwert bleibt dagegen der regulaere Kontext eines
        # Dokumentationstyps ohne Mindeststufe. Das versteckte ChoiceField
        # validiert den uebermittelten Wert unabhaengig davon weiter.
        from core.services.events import CONTACT_STAGE_ORDER, stage_index

        raw_min_stage = None if min_stage is None else str(min_stage).strip()
        if raw_min_stage is None or (raw_min_stage and stage_index(raw_min_stage) < 0):
            effective_min_stage = str(CONTACT_STAGE_ORDER[-1])
        else:
            effective_min_stage = raw_min_stage

        self.initial["min_stage"] = effective_min_stage
        required_index = stage_index(effective_min_stage)
        if effective_min_stage and required_index >= 0:
            allowed_stages = [stage for stage in CONTACT_STAGE_ORDER if stage_index(stage) >= required_index]
            contact_stage_field = cast(forms.ChoiceField, self.fields["contact_stage"])
            contact_stage_field.choices = [(stage, Client.ContactStage(stage).label) for stage in allowed_stages]
            if stage_index(self.initial.get("contact_stage")) < required_index:
                self.initial["contact_stage"] = effective_min_stage

    def clean(self):
        cleaned_data = super().clean()
        # ``min_stage`` ist versteckt und sein Feldfehler waere im Modal nicht
        # sichtbar. Als Non-Field-Error erreicht dieselbe Validierung den
        # gemeinsamen Fehlerblock mit ``role=alert`` (Refs #2039).
        for error in self.errors.as_data().get("min_stage", ()):
            self.add_error(None, error)
        return cleaned_data


class ClientForm(_FacilityScopedPseudonymMixin, TailwindFormMixin, forms.ModelForm):
    """Form for creating and editing clients."""

    class Meta:
        model = Client
        fields = ["pseudonym", "contact_stage", "age_cluster", "document_type"]
        help_texts = {
            "pseudonym": _(
                "Kurz halten: keine Klarnamen und keine Angaben nach Art. 9 DSGVO. "
                "Dieses Feld wird unverschlüsselt gespeichert."
            )
        }
        widgets = {
            # M2 P7 (Refs #1058): HTMX-Attribute exakt wie am Event-Select
            # (``core/events/create.html``) — CSP-strikt, also KEIN Inline-
            # Handler; das Nachladen der Zusatzfelder macht htmx selbst.
            # ``reverse_lazy``: die URLconf ist zur Import-Zeit des Formulars
            # noch nicht aufgeloest.
            "document_type": forms.Select(
                attrs={
                    "hx-get": reverse_lazy("core:client_fields_partial"),
                    "hx-target": "#dynamic-fields",
                    "hx-trigger": "change",
                    "hx-include": "[name='document_type']",
                }
            ),
        }

    def clean_contact_stage(self):
        stage = self.cleaned_data["contact_stage"]
        if not self.instance._state.adding and self.facility is not None:
            current = Client.objects.for_facility(self.facility).filter(pk=self.instance.pk).first()
            if (
                current is not None
                and current.contact_stage == Client.ContactStage.QUALIFIED
                and stage != current.contact_stage
                and current.events.exists()
            ):
                raise forms.ValidationError(
                    _("Bei vorhandenen Ereignissen ist ein Herabstufungsantrag mit Vier-Augen-Freigabe erforderlich.")
                )
        return stage

    def __init__(self, *args, facility=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.facility = facility
        self.user = user

        # M2 P7 (Refs #1058): Typ-Auswahl nach dem Muster ``EventMetaForm`` —
        # facility-scoped, nur aktive Typen, nur ``entity_type=client`` und nur
        # Sensitivitaeten, die die Rolle sehen darf. Ohne Facility bleibt die
        # Auswahl leer (fail-closed): ein ungescoptes Queryset waere ein
        # Cross-Tenant-Leck der Konfigurationsnamen.
        # ACHTUNG (Refs #1056/M0-Lehre): ``Client.id`` ist ein ``UUIDField`` mit
        # ``default=uuid.uuid4`` — eine FRISCHE, ungespeicherte Instanz hat
        # bereits eine ``pk``. ``self.instance.pk`` allein unterscheidet Anlage
        # und Bearbeitung also NICHT; dafuer ist ``_state.adding`` zustaendig.
        is_editing = bool(self.instance and self.instance.pk and not self.instance._state.adding)

        configure_entity_document_type(self, facility, DocumentType.EntityType.CLIENT, user=user)
        doc_type_field = self.fields["document_type"]

        # Refs #1058 (P7): Beim BEARBEITEN traegt das HTMX-Nachladen
        # die PK der Person mit. Nur damit kann die Partial-View den inaktiven
        # Ist-Typ genauso durchlassen wie das Queryset oben — sonst antwortet
        # sie 404, HTMX swapt bei 4xx nicht, und im DOM bleiben die Felder eines
        # anderen Typs stehen (stiller Datenverlust beim Speichern).
        # Widgets sind pro Formularinstanz deep-copied (``BaseForm.__init__``),
        # die Mutation ist also nicht instanzuebergreifend.
        if is_editing:
            partial_url = reverse("core:client_fields_partial")
            doc_type_field.widget.attrs["hx-get"] = f"{partial_url}?client={self.instance.pk}"

    def clean_document_type(self):
        document_type = self.cleaned_data["document_type"]
        if not self.instance._state.adding and changes_hidden_source_values(
            old_type_id=self.instance.document_type_id,
            selected_type_id=document_type.pk if document_type else None,
            old_data=self.instance.data_json or {},
            visible_old_data=visible_stored_details(self.instance, self.user),
        ):
            raise ValidationError(_("Keine Berechtigung für diesen Dokumentationstyp."))
        return document_type
