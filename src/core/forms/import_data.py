"""Formulare des Import-Wizards (M4, Refs #1060).

Nur Schritt 1 (Hochladen) ist ein klassisches Formular. Die Spaltenzuordnung in
Schritt 2 wird dynamisch aus der Kopfzeile der Datei gebaut und direkt in der
Engine (:func:`core.services.import_engine.apply_mapping`) validiert — ein
Django-Form mit zur Laufzeit erzeugten Feldern haette dieselbe Logik ein
zweites Mal getragen.
"""

from django import forms
from django.utils.translation import gettext_lazy as _

from core.forms.mixins import TailwindFormMixin
from core.models import DocumentType, ImportJob
from core.services.import_engine import SUPPORTED_ENCODINGS

#: Delimiter-Auswahl mit **symbolischen** Werten. Der Tabulator darf nicht als
#: rohes Zeichen im ``value``-Attribut stehen (HTML-Whitespace-Normalisierung);
#: :data:`DELIMITER_VALUES` loest den Schluessel wieder auf.
DELIMITER_CHOICES = (
    ("", _("Automatisch erkennen")),
    ("semicolon", _("Semikolon (;)")),
    ("comma", _("Komma (,)")),
    ("tab", _("Tabulator")),
)
DELIMITER_VALUES = {"semicolon": ";", "comma": ",", "tab": "\t"}

ENCODING_LABELS = {"utf-8-sig": _("UTF-8"), "cp1252": _("Windows-1252 (ältere Excel-Exporte)")}
ENCODING_CHOICES = (("", _("Automatisch erkennen")),) + tuple(
    (enc, ENCODING_LABELS[enc]) for enc in SUPPORTED_ENCODINGS
)


class ImportUploadForm(TailwindFormMixin, forms.Form):
    """Schritt 1: Zieltyp, Dokumentationstyp und CSV-Datei.

    Die Facility kommt als Keyword-Argument aus der View (nie aus dem POST) —
    das Typ-Dropdown ist damit strukturell auf die eigene Einrichtung gescopt.
    """

    # Refs #1834: ``target_type`` ist eine Radio-Gruppe. ``INPUT_CSS`` ist die
    # Klasse eines Text-/Select-Feldes (volle Breite, Rahmen, Innenabstand) und
    # landete an einer ``RadioSelect`` auf dem umschliessenden ``<ul>`` — die
    # Auswahl saehe aus wie ein leeres Eingabefeld mit Radios darin.
    tailwind_opt_out = ("target_type",)

    target_type = forms.ChoiceField(
        choices=ImportJob.TargetType.choices,
        label=_("Was soll importiert werden?"),
        widget=forms.RadioSelect,
    )
    document_type = forms.ModelChoiceField(
        queryset=DocumentType.objects.none(),
        required=False,
        label=_("Dokumentationstyp"),
        help_text=_("Liefert die Zusatzfelder als Zuordnungsziele. Für Ereignisse Pflicht."),
    )
    source_file = forms.FileField(
        label=_("CSV-Datei"),
        help_text=_("Erste Zeile = Spaltenüberschriften. Maximal 5 MB und 1000 Datenzeilen."),
        widget=forms.ClearableFileInput(attrs={"accept": ".csv,text/csv"}),
    )
    delimiter = forms.ChoiceField(
        choices=DELIMITER_CHOICES,
        required=False,
        label=_("Trennzeichen"),
    )
    encoding = forms.ChoiceField(
        choices=ENCODING_CHOICES,
        required=False,
        label=_("Zeichensatz"),
    )

    def __init__(self, *args, facility=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.facility = facility
        if facility is not None:
            self.fields["document_type"].queryset = (  # type: ignore[attr-defined]
                DocumentType.objects.for_facility(facility)
                .filter(
                    entity_type__in=[DocumentType.EntityType.CLIENT, DocumentType.EntityType.EVENT],
                    is_active=True,
                )
                .order_by("entity_type", "name")
            )

    def clean(self):
        cleaned = super().clean() or {}
        target_type = cleaned.get("target_type")
        document_type = cleaned.get("document_type")
        if target_type == ImportJob.TargetType.EVENT and document_type is None:
            self.add_error("document_type", _("Für den Ereignis-Import ist ein Dokumentationstyp erforderlich."))
        elif document_type is not None and target_type and document_type.entity_type != target_type:
            self.add_error("document_type", _("Der Dokumentationstyp passt nicht zum gewählten Zieltyp."))
        return cleaned

    @property
    def resolved_delimiter(self) -> str:
        """Das tatsaechliche Trennzeichen (leer = Auto-Erkennung)."""
        return DELIMITER_VALUES.get(self.cleaned_data.get("delimiter") or "", "")
