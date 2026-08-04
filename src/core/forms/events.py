"""Forms for event recording.

M2 (Refs #1058): Die dynamische ``data_json``-Form ist nach
:mod:`core.forms.dynamic` gezogen — sie war nie event-spezifisch und wird ab M2
auch von Client/Case genutzt. :class:`DynamicEventDataForm` ist nur noch die
event-seitige Auspraegung (leerer Body, FILE-Felder bleiben aktiv), damit alle
Bestands-Importpfade und das Verhalten des Event-Pfads unveraendert bleiben.
Die Modul-Helfer der Form werden hier ebenfalls re-exportiert.
"""

from django import forms
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
from core.models import Case, DocumentType


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
            # M2 (Refs #1058): nur event-Typen anbieten — client-/case-Typen
            # gehoeren nicht in den Event-Pfad (Entscheid E21).
            qs = DocumentType.objects.for_facility(facility).filter(
                is_active=True, entity_type=DocumentType.EntityType.EVENT
            )
            if user is not None:
                from core.services.compliance import allowed_sensitivities_for_user

                qs = qs.filter(sensitivity__in=allowed_sensitivities_for_user(user))
            self.fields["document_type"].queryset = qs  # type: ignore[attr-defined]
            # Refs #1777: Das Queryset ist zugleich die Validierungs-Whitelist des
            # Feldes. ``cases_with_live_client`` haelt Faelle widerrufener Personen
            # (Papierkorb/anonymisiert) heraus — sonst haengt ein Ereignis ueber
            # den Fall-Umweg doch wieder an einer Person, die den direkten
            # Client-Weg seit #1771 nicht mehr annimmt.
            from core.services.case import cases_with_live_client

            case_qs = cases_with_live_client().for_facility(facility).filter(status=Case.Status.OPEN)
            case_qs = case_qs.select_related("client")
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

        Refs #1771: Die Aufloesung laeuft seither ueber
        :func:`~core.services.client.resolve_writable_client` — der Scope allein
        liess widerrufene Personen (Papierkorb ``is_deleted``, anonymisiert
        ``is_active=False``) durch, und ein Offline-Replay schrieb frischen
        Klartext an den redigierten Datensatz.
        """
        client_id = self.cleaned_data.get("client")
        if not client_id:
            return None
        # Lokaler Import: ``core.services.client`` zieht Modelle und weitere
        # Services nach, ein Modul-Top-Level-Import riskierte einen Zirkel
        # ueber ``core.forms``.
        from core.services.client import resolve_writable_client

        return resolve_writable_client(
            self._facility,
            client_id,
            missing_error=_("Person existiert nicht."),  # pragma: no mutate
        )

    def clean(self):
        cleaned = super().clean()
        return cleaned


class DynamicEventDataForm(DynamicDataForm):
    """Event-Auspraegung der dynamischen ``data_json``-Form (M2, Refs #1058).

    Bewusst ohne eigenen Body: der komplette Aufbau liegt in
    :class:`~core.forms.dynamic.DynamicDataForm`. M24/AP4 (Refs #1738,
    ADR-039 §9): Das Flag ``exclude_file_fields`` gibt es nicht mehr — die
    Attachment-/File-Vault-Pipeline haengt seit ADR-039 am Owner (Ereignis,
    Person oder Fall), nicht mehr am Ereignis allein, und der Event-Pfad ist
    damit kein Sonderfall mehr. Die eigene Klasse bleibt bestehen, weil
    Importpfad, Klassenname und ``type(form).__name__`` (Logs/Tests) stabil
    bleiben muessen.
    """
