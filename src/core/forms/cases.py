"""Forms for case management."""

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _

from core.forms.dynamic import DynamicDataForm
from core.forms.dynamic import document_type_label as _document_type_label
from core.forms.widgets import INPUT_CSS
from core.models import Case, DocumentType, User


class DynamicCaseDataForm(DynamicDataForm):
    """Fall-Auspraegung der dynamischen ``data_json``-Form (M2 P8, Refs #1058).

    Bewusst ohne eigenen Body — der komplette Feldaufbau liegt in
    :class:`~core.forms.dynamic.DynamicDataForm`.

    M24/AP4 (Refs #1738, ADR-039 §9): Der Skip ``exclude_file_fields = True``
    ist **ersatzlos entfallen** (Spiegel aus
    :class:`~core.forms.clients.DynamicClientDataForm`). Die Klasse bleibt
    (leer) bestehen — stabiler Importpfad und stabiler Logname.
    """


class CaseForm(forms.ModelForm):
    """Form for creating and editing cases."""

    client = forms.UUIDField(
        required=True,
        widget=forms.HiddenInput(),
        label=_("Person"),  # pragma: no mutate
        error_messages={
            "required": _("Bitte eine Person auswählen — Fälle müssen einer Person zugeordnet sein."),
        },
    )

    class Meta:
        model = Case
        fields = ["title", "description", "lead_user", "document_type"]
        widgets = {
            "title": forms.TextInput(attrs={"class": INPUT_CSS}),
            "description": forms.Textarea(
                attrs={
                    "class": INPUT_CSS,
                    "rows": 4,
                }
            ),
            "lead_user": forms.Select(attrs={"class": INPUT_CSS}),
            # M2 P8 (Refs #1058): HTMX-Attribute exakt wie am Personen-/Event-
            # Select (``forms/clients.py`` / ``core/events/create.html``) —
            # CSP-strikt, also KEIN Inline-Handler; das Nachladen der
            # Zusatzfelder macht htmx selbst. ``reverse_lazy``: die URLconf ist
            # zur Import-Zeit des Formulars noch nicht aufgeloest.
            "document_type": forms.Select(
                attrs={
                    "class": INPUT_CSS,
                    "hx-get": reverse_lazy("core:case_fields_partial"),
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
        if facility:
            self.fields["lead_user"].queryset = User.objects.filter(  # type: ignore[attr-defined]
                facility=facility,
                role__in=[User.Role.STAFF, User.Role.LEAD, User.Role.FACILITY_ADMIN],
            )
        self.fields["lead_user"].required = False

        # M2 P8 (Refs #1058): Typ-Auswahl 1:1 nach dem Personen-Muster
        # (``ClientForm``, P7) — facility-scoped, nur aktive Typen, nur
        # ``entity_type=case`` und nur Sensitivitaeten, die die Rolle sehen darf.
        # Ohne Facility bleibt die Auswahl leer (fail-closed): ein ungescoptes
        # Queryset waere ein Cross-Tenant-Leck der Konfigurationsnamen.
        # ACHTUNG (Refs #1056/M0-Lehre, P7-Entscheid E-13): ``Case.id`` ist ein
        # ``UUIDField`` mit ``default=uuid.uuid4`` — eine FRISCHE, ungespeicherte
        # Instanz hat bereits eine ``pk``. ``self.instance.pk`` allein
        # unterscheidet Anlage und Bearbeitung also NICHT; dafuer ist
        # ``_state.adding`` zustaendig.
        is_editing = bool(self.instance and self.instance.pk and not self.instance._state.adding)

        doc_type_field = self.fields["document_type"]
        doc_type_field.required = False
        doc_type_field.empty_label = _("— Kein Typ —")  # type: ignore[attr-defined]
        doc_type_field.label_from_instance = _document_type_label  # type: ignore[attr-defined]
        if facility:
            qs = DocumentType.objects.for_facility(facility).filter(entity_type=DocumentType.EntityType.CASE)
            # Sensitivity-Filter laeuft VOR der Inaktiv-Ausnahme unten: ein
            # zugewiesener, aber fuer diese Rolle unsichtbarer Typ darf auch
            # dann nicht in die Auswahl geraten, wenn er inaktiv ist. Fuer
            # diesen Fall entfernt die View das Feld ganz
            # (``_lock_invisible_document_type``).
            if user is not None:
                from core.services.compliance import allowed_sensitivities_for_user

                qs = qs.filter(sensitivity__in=allowed_sensitivities_for_user(user))
            # Refs #1058 (P7-Fix-Runde 1, Entscheid E-10): Der AKTUELL
            # zugewiesene Typ bleibt waehlbar, auch wenn er zwischenzeitlich
            # deaktiviert wurde. Ohne diese Ausnahme faellt er aus dem Queryset,
            # das Select zeigt „— Kein Typ —" vorausgewaehlt, und ein ganz
            # normaler Submit loescht Typ UND alle Zusatzfeld-Werte, ohne dass
            # die Bearbeiterin etwas davon merkt. Muster #1160
            # (``_select_choices``): der deaktivierte Wert des BEARBEITETEN
            # Datensatzes bleibt erhalten — Neuanlagen bekommen ihn nicht.
            assigned_pk = self.instance.document_type_id if is_editing else None
            qs = qs.filter(Q(is_active=True) | Q(pk=assigned_pk)) if assigned_pk else qs.filter(is_active=True)
        else:
            qs = DocumentType.objects.none()
        doc_type_field.queryset = qs  # type: ignore[attr-defined]

        # Refs #1058 (P7-Fix-Runde 2, Entscheid E-12): Beim BEARBEITEN traegt das
        # HTMX-Nachladen die PK des Falls mit. Nur damit kann die Partial-View
        # den inaktiven Ist-Typ genauso durchlassen wie das Queryset oben —
        # sonst antwortet sie 404, HTMX swapt bei 4xx nicht, und im DOM bleiben
        # die Felder eines anderen Typs stehen (stiller Datenverlust beim
        # Speichern). Widgets sind pro Formularinstanz deep-copied
        # (``BaseForm.__init__``), die Mutation ist also nicht
        # instanzuebergreifend.
        if is_editing:
            partial_url = reverse("core:case_fields_partial")
            doc_type_field.widget.attrs["hx-get"] = f"{partial_url}?case={self.instance.pk}"

    def clean_client(self):
        client_id = self.cleaned_data.get("client")
        if not client_id:
            raise ValidationError(_("Bitte eine Person auswählen — Fälle müssen einer Person zugeordnet sein."))
        # Refs #819: scoped Query — eine Person aus einer fremden
        # Facility liefert direkt DoesNotExist statt der nachgelagerten
        # Facility-Pruefung. Defense-in-Depth gegen ID-Erraten.
        # Refs #1771: zusaetzlich das Widerrufs-Gate (Papierkorb/anonymisiert) —
        # ein Fall an einer redigierten Person waere frischer Klartext (Titel,
        # Beschreibung, ``data_json``, seit M24 auch Datei-Anhaenge).
        from core.services.client import resolve_writable_client

        return resolve_writable_client(
            self.facility,
            client_id,
            missing_error=_("Person existiert nicht."),
        )
