"""Forms for client management."""

from typing import ClassVar

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _

from core.forms.dynamic import DynamicDataForm
from core.forms.dynamic import document_type_label as _document_type_label
from core.forms.widgets import INPUT_CSS
from core.models import Client, DocumentType

# M2 P8 (Refs #1058): ``_document_type_label`` ist nach ``forms/dynamic.py``
# gezogen (der Fall-Picker braucht dieselbe Beschriftung). Der Alt-Name bleibt
# als Import-Alias erhalten — Muster P3/P7 (Umzug + Alias-Re-Export).


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
        # ACHTUNG (Refs #1056/M0-Lehre): ``Client.id`` ist ein ``UUIDField`` mit
        # ``default=uuid.uuid4`` — eine FRISCHE, ungespeicherte Instanz hat
        # bereits eine ``pk``. ``self.instance.pk`` allein unterscheidet Anlage
        # und Bearbeitung also NICHT; dafuer ist ``_state.adding`` zustaendig.
        is_editing = bool(self.instance and self.instance.pk and not self.instance._state.adding)

        doc_type_field = self.fields["document_type"]
        doc_type_field.required = False
        doc_type_field.empty_label = _("— Kein Typ —")  # type: ignore[attr-defined]
        doc_type_field.label_from_instance = _document_type_label  # type: ignore[attr-defined]
        if facility:
            qs = DocumentType.objects.for_facility(facility).filter(entity_type=DocumentType.EntityType.CLIENT)
            # Sensitivity-Filter laeuft VOR der Inaktiv-Ausnahme unten: ein
            # zugewiesener, aber fuer diese Rolle unsichtbarer Typ darf auch
            # dann nicht in die Auswahl geraten, wenn er inaktiv ist. Fuer
            # diesen Fall entfernt die View das Feld ganz
            # (``_lock_invisible_document_type``).
            if user is not None:
                from core.services.compliance import allowed_sensitivities_for_user

                qs = qs.filter(sensitivity__in=allowed_sensitivities_for_user(user))
            # Refs #1058 (P7-Fix-Runde): Der AKTUELL zugewiesene Typ bleibt
            # waehlbar, auch wenn er zwischenzeitlich deaktiviert wurde. Ohne
            # diese Ausnahme faellt er aus dem Queryset, das Select zeigt
            # „— Kein Typ —" vorausgewaehlt, und ein ganz normaler Submit
            # loescht Typ UND alle (potenziell Art.-9-nahen) Zusatzfeld-Werte,
            # ohne dass die Bearbeiterin etwas davon merkt. Muster #1160
            # (``_select_choices``): der deaktivierte Wert des BEARBEITETEN
            # Datensatzes bleibt erhalten — Neuanlagen bekommen ihn nicht.
            assigned_pk = self.instance.document_type_id if is_editing else None
            qs = qs.filter(Q(is_active=True) | Q(pk=assigned_pk)) if assigned_pk else qs.filter(is_active=True)
        else:
            qs = DocumentType.objects.none()
        doc_type_field.queryset = qs  # type: ignore[attr-defined]

        # Refs #1058 (P7-Fix-Runde 2): Beim BEARBEITEN traegt das HTMX-Nachladen
        # die PK der Person mit. Nur damit kann die Partial-View den inaktiven
        # Ist-Typ genauso durchlassen wie das Queryset oben — sonst antwortet
        # sie 404, HTMX swapt bei 4xx nicht, und im DOM bleiben die Felder eines
        # anderen Typs stehen (stiller Datenverlust beim Speichern).
        # Widgets sind pro Formularinstanz deep-copied (``BaseForm.__init__``),
        # die Mutation ist also nicht instanzuebergreifend.
        if is_editing:
            partial_url = reverse("core:client_fields_partial")
            doc_type_field.widget.attrs["hx-get"] = f"{partial_url}?client={self.instance.pk}"

    def clean_pseudonym(self):
        pseudonym = self.cleaned_data["pseudonym"]
        qs = Client.objects.for_facility(self.facility).filter(pseudonym=pseudonym)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError(_("Eine Person mit diesem Pseudonym existiert bereits in dieser Einrichtung."))
        return pseudonym
