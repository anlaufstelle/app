"""Forms for event recording.

M2 (Refs #1058): Die dynamische ``data_json``-Form ist nach
:mod:`core.forms.dynamic` gezogen — sie war nie event-spezifisch und wird ab M2
auch von Client/Case genutzt. :class:`DynamicEventDataForm` ist nur noch die
event-seitige Auspraegung (leerer Body, FILE-Felder bleiben aktiv), damit alle
Bestands-Importpfade und das Verhalten des Event-Pfads unveraendert bleiben.
Ein Teil der Modul-Helfer wird hier ebenfalls re-exportiert — ausschliesslich
diejenigen mit einem echten Verbraucher ausserhalb dieses Moduls
(``core.services.system.field_types`` importiert ``MultipleFileField``/
``MultipleFileInput`` weiterhin von hier, ebenso einzelne
``test_mutation_followup_event_form.py``-Tests die uebrigen internen Helfer —
Refs #1827: dieses Test-File bleibt gemaess mutmut-Vertrag unangetastet, daher
bleiben ``_build_dynamic_field``/``_file_upload_errors``/
``_resolve_upload_constraints``/``_select_choices`` re-exportiert). Der reine
Test-only-Re-Export ``_coerce_condition_number`` (einziger Verbraucher:
``test_event_form_conditions.py``) importiert seitdem direkt von
:mod:`core.forms.dynamic`; ``_PAYLOAD_UNSET`` hatte ueberhaupt keinen
Verbraucher ausserhalb dieses Moduls und ist ersatzlos entfallen.
"""

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.forms.dynamic import (  # noqa: F401 — Re-Export, Importpfad-Stabilitaet (Refs #1058)
    DynamicDataForm,
    MultipleFileField,
    MultipleFileInput,
    _build_dynamic_field,
    _file_upload_errors,
    _resolve_upload_constraints,
    _select_choices,
)
from core.forms.fields import AmbiguousWallClock, DstAwareDateTimeField
from core.forms.mixins import PinnedClientMixin, TailwindFormMixin
from core.models import Case, DocumentType
from core.utils.date_input import AppDateTimeInput


def _case_label(case: Case) -> str:
    """Dropdown label: title plus client pseudonym so users can distinguish cases."""
    pseudonym = case.client.pseudonym if case.client_id else None
    return f"{case.title} – {pseudonym}" if pseudonym else case.title


# Refs #2127: Die beiden Lesarten der doppelten Stunde am Rueckfalltag. Die
# Werte sind die ``fold``-Werte aus PEP 495 als String (Formulardaten sind
# Strings); der Leerwert ist der Normalfall — er steht fuer "noch nicht
# gefragt" UND fuer "gibt es hier nicht zu entscheiden".
OCCURRENCE_FOLD_SUMMER = "0"
OCCURRENCE_FOLD_WINTER = "1"
OCCURRENCE_FOLD_CHOICES = (
    # Bestands-msgid (``offline_create.html``/``offline_detail.html``) — bewusst wiederverwendet.
    ("", _("– bitte wählen –")),  # pragma: no mutate
    (OCCURRENCE_FOLD_SUMMER, _("Sommerzeit — erste Stunde (MESZ)")),  # pragma: no mutate
    (OCCURRENCE_FOLD_WINTER, _("Winterzeit — zweite Stunde (MEZ)")),  # pragma: no mutate
)


class EventMetaForm(TailwindFormMixin, PinnedClientMixin, forms.Form):
    """Event metadata (document type, client, timestamp)."""

    document_type = forms.ModelChoiceField(
        queryset=DocumentType.objects.none(),
        label=_("Dokumentationstyp"),  # pragma: no mutate
        # Refs #2381: Django 6.1 faellt ohne eigenes ``empty_label`` auf den
        # unuebersetzten Blank-Choice-Text ("- Select an option -") zurueck.
        empty_label=_("— Bitte auswählen —"),
        widget=forms.Select(
            attrs={
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
    occurred_at = DstAwareDateTimeField(
        label=_("Zeitpunkt"),  # pragma: no mutate
        # Refs #2127: Die doppelte Stunde des Rueckfalltags kommt als
        # ``AmbiguousWallClock`` hier an statt als Feldfehler — aufgeloest wird
        # sie unten in ``clean()`` mit der Antwort auf ``occurred_at_fold``.
        resolve_ambiguous=True,
        widget=AppDateTimeInput(
            attrs={
                "type": "datetime-local",
            },
        ),
    )
    occurred_at_fold = forms.ChoiceField(
        required=False,
        label=_("Welche der beiden Stunden?"),  # pragma: no mutate
        choices=OCCURRENCE_FOLD_CHOICES,
        help_text=_(
            "In der Nacht der Umstellung auf die Winterzeit läuft die Stunde von 02:00 bis 03:00 zweimal. "
            "Die erste Stunde ist noch Sommerzeit, die zweite bereits Winterzeit."
        ),
        widget=forms.Select(),
    )
    case = forms.ModelChoiceField(
        queryset=Case.objects.none(),
        required=False,
        label=_("Fall"),  # pragma: no mutate
        empty_label=_("– Keinem Fall zuordnen –"),
    )

    def __init__(self, *args, facility=None, user=None, **kwargs):
        initial = kwargs.get("initial", {})
        if "occurred_at" not in initial:
            initial["occurred_at"] = timezone.localtime(timezone.now()).strftime("%Y-%m-%dT%H:%M")
            kwargs["initial"] = initial
        super().__init__(*args, **kwargs)
        # Refs #2127: Sichtbarkeits-Zustand der Okkurrenz-Auswahl, gesetzt in
        # ``clean()``. Ein ungebundenes Formular kennt keinen Grenzfall.
        self._show_occurred_at_fold = False
        self._facility = facility
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
            # Refs #2533: the picker whitelist is also the assignment boundary.
            from core.services.events.case_assignment import assignable_cases

            case_qs = assignable_cases(facility, user)
            case_qs = case_qs.select_related("client")
            self.fields["case"].queryset = case_qs  # type: ignore[attr-defined]
            self.fields["case"].label_from_instance = _case_label  # type: ignore[attr-defined]

    @property
    def show_occurred_at_fold(self) -> bool:
        """Refs #2127: Rendert das Template die Okkurrenz-Auswahl?

        Der Zustand kommt aus :meth:`clean` — ausdruecklich **nicht** aus
        ``BoundField.value()``. Letzteres liefert bei einem gebundenen Formular
        den ROH uebermittelten POST-Wert und nicht ``cleaned_data``: eine
        mitgeschickte Auswahl haette das Feld deshalb auch ausserhalb des
        Doppelfensters wieder sichtbar gemacht, sobald ein anderes Feld
        scheitert und das Formular neu gerendert wird — genau der Fall, den
        ``clean()`` laengst auf ``""`` normalisiert hat.

        ``self.errors`` ist Djangos uebliche Lazy-Kante: der Zugriff erzwingt
        ``full_clean()``, falls ein Template das gebundene Formular rendert,
        ohne dass der View vorher ``is_valid()`` gerufen hat.
        """
        if not self.is_bound:
            return False
        _ = self.errors
        return self._show_occurred_at_fold

    def get_client_facility(self):
        """Hook aus :class:`~core.forms.mixins.PinnedClientMixin`.

        Das Ereignis-Formular haelt die Facility unter ``_facility`` (kein
        ModelForm, die Attribute sind bewusst privat) — der Hook ueberbrueckt
        genau diese Namens-Asymmetrie, statt sie mit einem Rename quer durch
        Views und Tests einzuebnen.
        """
        return self._facility

    def clean_client(self):
        """Refs #1423 (N11): Client-UUID facility-scoped gegen die DB aufloesen.

        Kein Pinning: ``EventMetaForm`` kennt kein ``pinned_client``, das
        Zuordnungs-Gate sitzt view-seitig (``views/events.py``). Geteilt ist
        nur der Resolver aus :class:`~core.forms.mixins.PinnedClientMixin`.

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
        return self.resolve_client(
            client_id,
            missing_error=_("Person existiert nicht."),  # pragma: no mutate
        )

    def clean(self):
        """Refs #2127: die doppelte Stunde des Rueckfalltags aufloesen — oder nachfragen.

        ``occurred_at`` traegt hier einen :class:`~core.forms.fields.AmbiguousWallClock`,
        wenn die eingegebene Wanduhrzeit am Rueckfalltag zweimal existiert.
        Genau dann — und nur dann — ist ``occurred_at_fold`` eine Pflichtangabe:

        * mit Auswahl wird daraus ein aware ``datetime`` mit dem gewaehlten
          ``fold``; die beiden Lesarten liegen in UTC eine Stunde auseinander;
        * ohne Auswahl bekommt **das Auswahlfeld** den Fehler (nicht die
          Uhrzeit — die ist nicht falsch, sondern nur noch nicht entschieden),
          und der unaufgeloeste Zwischenwert wird aus ``cleaned_data``
          entfernt, damit kein Verbraucher ihn je zu sehen bekommt. Das
          Template rendert das Feld daraufhin sichtbar (serverseitiger
          Sichtbarkeitswechsel, kein JavaScript).

        Ausserhalb des Doppelfensters ist die Auswahl bedeutungslos und wird
        auf den Leerwert normalisiert — ein mitgeschickter Wert darf einen
        eindeutigen Zeitpunkt nicht verschieben.
        """
        # ``Form.clean`` ist laut Stub ``dict | None`` (eine Unterklasse darf
        # nichts zurueckgeben — Django faellt dann auf ``self.cleaned_data``
        # zurueck). Der Fallback macht genau das explizit, statt sich auf die
        # heutige Basisimplementierung zu verlassen.
        cleaned = super().clean() or self.cleaned_data
        occurred_at = cleaned.get("occurred_at")
        if not isinstance(occurred_at, AmbiguousWallClock):
            cleaned["occurred_at_fold"] = ""
            return cleaned
        # Ab hier ist der Grenzfall sicher: das Template darf (und muss) das
        # Auswahlfeld zeigen — auch dann, wenn die Auswahl bereits getroffen
        # wurde und nur ein ANDERES Feld den Redisplay ausloest.
        self._show_occurred_at_fold = True
        fold = cleaned.get("occurred_at_fold")
        if fold in (OCCURRENCE_FOLD_SUMMER, OCCURRENCE_FOLD_WINTER):
            cleaned["occurred_at"] = occurred_at.resolve(int(fold))
            return cleaned
        # Ein bereits vom ChoiceField abgelehnter Wert (manipuliertes Select)
        # traegt seinen eigenen Fehler — keine zweite Meldung am selben Feld.
        if not self.has_error("occurred_at_fold"):
            self.add_error(
                "occurred_at_fold",
                forms.ValidationError(
                    _("Diese Uhrzeit gibt es am Rückfalltag zweimal — bitte Sommer- oder Winterzeit wählen."),
                    code="fold_required",
                ),
            )
        cleaned.pop("occurred_at", None)
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
