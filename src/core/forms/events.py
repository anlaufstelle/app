"""Forms for event recording.

M2 (Refs #1058): Die dynamische ``data_json``-Form ist nach
:mod:`core.forms.dynamic` gezogen — sie war nie event-spezifisch und wird ab M2
auch von Client/Case genutzt. :class:`DynamicEventDataForm` ist nur noch die
event-seitige Auspraegung (leerer Body, FILE-Felder bleiben aktiv), damit alle
Bestands-Importpfade und das Verhalten des Event-Pfads unveraendert bleiben.
Die Modul-Helfer der Form werden hier ebenfalls re-exportiert.
"""

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.forms.dynamic import (  # noqa: F401 — Re-Export, Importpfad-Stabilitaet (Refs #1058)
    _PAYLOAD_UNSET,
    DynamicDataForm,
    MultipleFileField,
    MultipleFileInput,
    _build_dynamic_field,
    _coerce_condition_number,
    _file_upload_errors,
    _resolve_upload_constraints,
    _select_choices,
)
from core.forms.widgets import INPUT_CSS
from core.models import Case, Client, DocumentType


def _case_label(case: Case) -> str:
    """Dropdown label: title plus client pseudonym so users can distinguish cases."""
    pseudonym = case.client.pseudonym if case.client_id else None
    return f"{case.title} – {pseudonym}" if pseudonym else case.title


class EventMetaForm(forms.Form):
    """Event metadata (document type, client, timestamp)."""

    document_type = forms.ModelChoiceField(
        queryset=DocumentType.objects.none(),
        label=_("Dokumentationstyp"),  # pragma: no mutate
        widget=forms.Select(
            attrs={
                "class": INPUT_CSS,
                "hx-get": "",  # wird im View gesetzt
                "hx-target": "#dynamic-fields",
                "hx-trigger": "change",
            }
        ),
    )
    client = forms.UUIDField(
        required=False,
        label=_("Person"),  # pragma: no mutate
        widget=forms.HiddenInput(),
    )
    occurred_at = forms.DateTimeField(
        label=_("Zeitpunkt"),  # pragma: no mutate
        widget=forms.DateTimeInput(
            attrs={
                "type": "datetime-local",
                "class": INPUT_CSS,
                "tabindex": "4",
            },
        ),
    )
    case = forms.ModelChoiceField(
        queryset=Case.objects.none(),
        required=False,
        label=_("Fall"),  # pragma: no mutate
        empty_label=_("– Keinem Fall zuordnen –"),
        widget=forms.Select(attrs={"class": INPUT_CSS}),
    )

    def __init__(self, *args, facility=None, user=None, **kwargs):
        initial = kwargs.get("initial", {})
        if "occurred_at" not in initial:
            initial["occurred_at"] = timezone.localtime(timezone.now()).strftime("%Y-%m-%dT%H:%M")
            kwargs["initial"] = initial
        super().__init__(*args, **kwargs)
        self._facility = facility
        self._user = user
        if facility:
            qs = DocumentType.objects.for_facility(facility).filter(is_active=True)
            if user is not None:
                from core.services.compliance import allowed_sensitivities_for_user

                qs = qs.filter(sensitivity__in=allowed_sensitivities_for_user(user))
            self.fields["document_type"].queryset = qs  # type: ignore[attr-defined]
            case_qs = Case.objects.for_facility(facility).filter(status=Case.Status.OPEN).select_related("client")
            self.fields["case"].queryset = case_qs  # type: ignore[attr-defined]
            self.fields["case"].label_from_instance = _case_label  # type: ignore[attr-defined]

    def clean_client(self):
        """Refs #1423 (N11): Client-UUID facility-scoped gegen die DB aufloesen.

        Muster analog ``CaseForm.clean_client`` (``core/forms/cases.py``):
        kein Wert ist legitim (anonyme Events bleiben erlaubt, ``client``
        ist ``required=False``) -> ``None``. Ein Wert, der sich nicht auf
        eine Person der eigenen Facility aufloesen laesst (fremde Facility
        ODER unbekannte/geloeschte UUID), war vorher ein stiller
        Anonym-Fallback im View (Datenintegritaetsverlust) — jetzt ein
        harter Formularfehler, der ueber den bestehenden 422-Replay-
        Contract sauber als ``invalid`` klassifiziert wird.
        """
        client_id = self.cleaned_data.get("client")
        if not client_id:
            return None
        scoped = Client.objects.for_facility(self._facility) if self._facility else Client.objects.all()
        try:
            return scoped.get(pk=client_id)
        except Client.DoesNotExist as exc:
            raise ValidationError(_("Person existiert nicht.")) from exc  # pragma: no mutate

    def clean(self):
        cleaned = super().clean()
        return cleaned


class DynamicEventDataForm(DynamicDataForm):
    """Event-Auspraegung der dynamischen ``data_json``-Form (M2, Refs #1058).

    Bewusst ohne eigenen Body: der komplette Aufbau liegt in
    :class:`~core.forms.dynamic.DynamicDataForm`. FILE-Felder bleiben aktiv
    (``exclude_file_fields = False``), denn nur der Event-Pfad hat die
    Attachment-/File-Vault-Pipeline (Entscheid E8). Die eigene Klasse bleibt
    bestehen, weil Importpfad, Klassenname und ``type(form).__name__``
    (Logs/Tests) stabil bleiben muessen.
    """
