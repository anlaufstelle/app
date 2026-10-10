"""Forms für die Formular-Konfiguration (M0, Refs #1056).

DocumentType- und FieldTemplate-Formulare für die Config-CRUD-UI unter
``/config/``. Die ``facility`` ist bewusst **kein** Formularfeld — sie kommt
aus dem Request-Scope (``request.current_facility``) und wird beim Speichern
über :mod:`core.services.config` gesetzt. Die Formulare kapseln nur die
UI-Validierung (Widget-CSS, freundliche Unique-Fehler, Immutabilitäten).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from django import forms
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _

from core.forms.mixins import TailwindFormMixin
from core.models import DocumentType, DocumentTypeField, FieldCondition, FieldGroup, FieldTemplate
from core.models.report_key import EXCLUDED
from core.models.slug import _RESERVED_SLUGS, _SLUG_RE, slug_base
from core.reporting.registry import (
    backends_for_report_key,
    diagnose_type_bindings,
    get_field_key_vocabulary,
    get_report_key_vocabulary,
)
from core.services.compliance.sensitivity import validate_explicit_high_field
from core.services.conditions import (
    ALLOWED_SOURCE_FIELD_TYPES,
    OPERATORS_BY_FIELD_TYPE,
    VALUELESS_OPERATORS,
    parse_condition_value,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    # Stub-only-Alias, zur Laufzeit nicht vorhanden — nur fuer Typannotationen.
    from django.utils.functional import _StrOrPromise


def validate_slugifiable_name(name: str) -> str:
    """Fail-fast, wenn ``name`` keinen Slug ergibt (Final-Review D, Refs #1058).

    Die Modelle erzeugen den Slug erst in ``save()`` und werfen dort eine
    ``ValidationError`` — nach ``form.is_valid()``, also ungefangen als HTTP 500.
    Diese Prüfung zieht denselben Test in die Formularschicht vor, damit die
    Config-UI einen normalen Feldfehler zeigt. Nur beim **Anlegen** relevant:
    danach ist der Slug unveränderlich, ein Namenswechsel berührt ihn nicht.
    """
    if name and not slug_base(name):
        raise ValidationError(
            _(
                "Aus diesem Namen lässt sich kein gültiger Slug bilden — bitte mindestens "
                "einen Buchstaben (a–z) oder eine Ziffer verwenden."
            )
        )
    # Reserved-Slug-Guard (Refs #1726): der aus dem Namen gebildete Slug darf
    # keinen festen Formularfeld-Namen treffen — dynamische Felder werden nackt
    # unter ihrem Slug im selben ``<form>`` gerendert, eine Kollision würde beim
    # Absenden das feste Feld überschreiben. Gilt über den gemeinsamen Helfer für
    # FieldTemplateForm.clean_name, FieldGroupForm.clean_name UND
    # DocumentTypeForm.clean_name (Konsistenz — nur FieldTemplate-Slugs werden
    # tatsächlich als Feldname gerendert, aber ein reservierter Gruppen-/Typ-Slug
    # ist ohnehin nie sinnvoll; dieselbe ``_RESERVED_SLUGS`` an allen Schichten).
    if name and slug_base(name) in _RESERVED_SLUGS:
        raise ValidationError(
            _(
                "Der Name „%(name)s“ ergibt den Slug „%(slug)s“, der mit einem festen "
                "Formularfeld kollidiert — bitte einen anderen Namen wählen."
            )
            % {"name": name, "slug": slug_base(name)}
        )
    return name


def label_blank_choice(field: forms.Field, blank_label: _StrOrPromise) -> None:
    """Beschriftet die Leer-Option eines Auswahlfelds (Refs #2381).

    Fuer Modellfelder mit ``blank=True`` und ``choices`` erzeugt Django ein
    ``TypedChoiceField`` mit einer Leer-Option, deren Text ohne eigene Angabe
    auf Djangos unuebersetzten Blank-Choice-Text ("- Select an option -")
    faellt. Alle uebrigen Optionen bleiben unveraendert.
    """
    choice_field = cast(forms.ChoiceField, field)
    choices = cast("Iterable[tuple[Any, Any]]", choice_field.choices)
    choice_field.choices = [(value, blank_label if value == "" else label) for value, label in choices]


class ReportKeyFormMixin:
    """Gemeinsame Formularmechanik des ``report_key`` (E3, Refs #1849).

    Beide Traeger (Dokumentationstyp und Feldvorlage) bekommen dieselbe Auswahl
    und dieselbe Sperrlogik — sie unterscheiden sich nur im **Vokabular**
    (Typ- bzw. Feld-Berichtsschluessel sind getrennte Werteraeume, Konzept
    #1847 §3.1).

    Drei Entscheidungen, die hier zusammenkommen:

    * **Der Werteraum kommt aus der Registry**, nicht aus dem Modell. Das
      Modellfeld traegt bewusst kein ``choices`` (sonst waere jeder Fremdwert im
      ``full_clean()`` hart); die Auswahl entsteht erst hier.
    * **Sperre nur bei gesetztem Wert** (Entscheid E-K) — nicht pauschal bei
      jeder Bearbeitung wie bei ``system_type``. Das Nachtragen ist der
      Reparaturpfad fuer die stille Nicht-Zuordnung und muss offen bleiben.
    * **Toleranz-Option** fuer einen Bestandswert, den kein Backend (mehr)
      kennt. Ohne sie waere die Zeile **unbearbeitbar**: ein ``disabled``-Feld
      wird weiterhin gegen die Optionsliste validiert
      (``ChoiceField.validate``), und ein unbekannter Wert schluege als
      „Ungueltige Auswahl" auf einem Feld auf, das die Nutzerin gar nicht
      aendern kann. Genau diese Falle war bisher nirgends abgesichert.
    """

    if TYPE_CHECKING:
        # Kommen von der ModelForm, neben der das Mixin steht — nur fuer mypy.
        fields: dict[str, forms.Field]
        instance: Any
        cleaned_data: dict[str, Any]

        def apply_tailwind_classes(self) -> None: ...

    #: Vorbelegung beim Anlegen: ``system_type`` → ``report_key`` (leer = keine).
    REPORT_KEY_DEFAULTS: dict[str, str] = {}

    def _append_help_text(self, field_name: str, hint) -> None:
        """Haengt ``hint`` an den (Modell-)``help_text`` des Feldes an."""
        existing = self.fields[field_name].help_text
        self.fields[field_name].help_text = f"{existing} {hint}".strip() if existing else hint

    def _setup_report_key(self, vokabular: dict[str, str]) -> None:
        """Ersetzt das generierte Textfeld durch die Registry-Auswahl."""
        bestand = self.fields["report_key"]
        auswahl = forms.ChoiceField(
            required=False,
            label=bestand.label,
            # Label und Hilfetext kommen aus dem Modellfeld — sie stehen sonst
            # ein zweites Mal im Uebersetzungskatalog und driften.
            help_text=bestand.help_text,
            choices=self._report_key_choices(vokabular),
        )
        gesetzt = bool(self.instance.report_key)
        bearbeitung = not self.instance._state.adding
        if bearbeitung and gesetzt:
            auswahl.disabled = True
        self.fields["report_key"] = auswahl
        if bearbeitung and gesetzt:
            self._append_help_text(
                "report_key",
                _(
                    "Falsch gesetzt? Diesen Eintrag deaktivieren und einen neuen mit dem richtigen Schlüssel "
                    "anlegen — bereits erfasste Einträge bleiben bei ihrer bisherigen Berichtszeile."
                ),
            )
        elif bearbeitung:
            # Nur im Nachtrag-Fall: beim Anlegen gibt es noch keine Berichte,
            # und bei gesperrtem Feld waere der Hinweis gegenstandslos.
            self._append_help_text(
                "report_key",
                _("Nachträglich gesetzt wirkt der Schlüssel auf künftig erzeugte Berichte über alle Zeiträume."),
            )
        # Der Feldtausch passiert NACH ``TailwindFormMixin.__init__`` — das neue
        # Widget haette sonst keine Eingabe-Klasse. ``setdefault`` macht den
        # zweiten Lauf gefahrlos.
        self.apply_tailwind_classes()

    def _report_key_choices(self, vokabular: dict[str, str]) -> list[tuple[str, Any]]:
        """Leer-Option, Sentinel, Registry-Vokabular — plus Toleranz-Option."""
        auswahl: list[tuple[str, Any]] = [
            ("", _("— Erscheint in keinem Bericht —")),
            (EXCLUDED, _("Gehört ausdrücklich in keinen Bericht")),
            *vokabular.items(),
        ]
        aktuell = self.instance.report_key
        if aktuell and aktuell not in {key for key, _label in auswahl}:
            auswahl.append(
                (
                    aktuell,
                    format_lazy(_("{key} — kein Backend kennt diesen Schlüssel"), key=aktuell),
                )
            )
        return auswahl

    def clean_report_key(self) -> str | None:
        """Leere Auswahl als ``NULL`` speichern, nicht als Leerstring.

        Beides gilt ueberall als „keine Aussage getroffen" (Modell-Guard,
        Backfill, Unmapped-Filter) — aber zwei Schreibweisen fuer denselben
        Zustand im Bestand sind eine Fehlerquelle ohne Gegenwert.
        """
        return self.cleaned_data.get("report_key") or None


class DocumentTypeForm(ReportKeyFormMixin, TailwindFormMixin, forms.ModelForm):
    """Anlegen/Bearbeiten eines Dokumentationstyps.

    * ``facility`` kommt aus dem Request-Scope (kein Formularfeld) und wird für
      neue Objekte auf die Instanz gesetzt, damit ``form.save()`` im Service den
      richtigen Mandanten persistiert.
    * ``system_type`` ist nur beim Anlegen editierbar; bei Bearbeitung wird das
      Feld ``disabled`` (Wert stammt aus der Instanz) — spiegelt die
      Model-Immutabilität (``DocumentType.save``) und die Admin-Parität
      (``DocumentTypeAdmin.get_readonly_fields``).
    * ``entity_type`` (M2, Refs #1058): dasselbe Muster wie ``system_type`` —
      nur beim Anlegen wählbar, bei Bearbeitung ``disabled`` (spiegelt die
      Model-Immutabilität in ``DocumentType.save()`` und die Admin-Parität
      ``DocumentTypeAdmin.get_readonly_fields``). Anders als ``system_type``
      ist das Modellfeld selbst NICHT ``blank=True`` — ein ``required``-
      Formfeld würde jeden POST ohne ``entity_type`` (Bestand: die meisten
      Formulare/Tests setzen das Feld nicht explizit) als Fehler ablehnen.
      Das Feld ist daher bewusst ``required=False``; ``clean_entity_type``
      füllt eine leere Auswahl auf den Bestandswert ``EntityType.EVENT`` auf
      (identisch zum bisherigen, impliziten Modell-Default) statt eine
      „Pflichtfeld"-Fehlermeldung zu zeigen.
    * Die zusammengesetzte Unique-Regel ``(facility, name)`` wird als
      freundlicher Formfehler gemeldet statt als ``IntegrityError`` (facility ist
      kein Formfeld, daher greift Djangos ModelForm-Unique-Check hier nicht).
    * ``EVENT_ONLY_FIELDS`` (Refs #1757, Option C aus #1731) ist die EINE
      Wahrheitsquelle dafür, welche Felder ausschließlich an
      ``entity_type=event``-Typen wirken: das Formular hängt daran die
      Hinweistexte, das Template gibt sie als ``data``-Attribut an die
      Alpine-Komponente weiter. Weder Template noch JavaScript führen einen
      zweiten Katalog.
    """

    #: Felder, die nur an ``entity_type=event``-Typen etwas bewirken.
    #: ``min_contact_stage`` greift ausschließlich in der Ereignis-Anlage
    #: (``services/events/crud.py``), ``retention_days`` löscht ausschließlich
    #: ``Event`` (``retention/enforcement.py``), und ``system_type`` ist laut
    #: Paketschema nur bei ``entity_type=event`` zulässig (``clean()``).
    #: Rein visuell — an der serverseitigen Validierung/Semantik ändert das
    #: Ausblenden nichts, die Werte werden unverändert mitgepostet.
    #: ``report_key`` (E3, Refs #1849) gehört ebenfalls hierher: alle
    #: Berichts-Backends zählen ``Event``-Zeilen, an einem Personen-/Fall-Typ ist
    #: die Berichtsfrage gegenstandslos. ``tally_enabled`` steuert eine
    #: Ereignis-Schnellerfassung und ist damit ebenso event-only.
    EVENT_ONLY_FIELDS = (
        "system_type",
        "report_key",
        "min_contact_stage",
        "retention_days",
        "tally_enabled",
    )

    #: Vorbelegung beim Anlegen (Entscheid E-D/E-I, Konzept #1847 §E3.5).
    #: Ohne sie verlören neu angelegte ``crisis``-Typen still die Parität zum
    #: Bestand (den sichert nur der Backfill), und neue ``ban``-Typen erzeugten
    #: den Dauerhinweis aus #1819 erneut. Beides bleibt **änderbar** — die
    #: Vorbelegung ist ein Vorschlag im Browser, kein serverseitiges Nachfüllen
    #: in ``clean()``: das überschriebe die ausdrückliche Wahl der Nutzerin.
    REPORT_KEY_DEFAULTS = {
        DocumentType.SystemType.CRISIS.value: "crisis",
        DocumentType.SystemType.BAN.value: EXCLUDED,
    }

    class Meta:
        model = DocumentType
        # Refs #1731: ``entity_type`` steht bewusst direkt hinter ``name``. Das
        # Template rendert die Felder einzeln in genau dieser Reihenfolge
        # (Refs #1757), diese Liste IST also die Anzeigereihenfolge — und der
        # Entitätstyp ist die folgenreichste, nach dem Anlegen unveränderbare
        # Entscheidung: er bestimmt, ob
        # ``system_type``/``min_contact_stage``/``retention_days`` überhaupt
        # wirken. Er darf nicht hinter den Feldern stehen, die er regiert.
        fields = [
            "name",
            "entity_type",
            "sensitivity",
            "system_type",
            "report_key",
            "min_contact_stage",
            "retention_days",
            "description",
            "icon",
            "color",
            "sort_order",
            "is_active",
            "tally_enabled",
        ]
        widgets = {
            "retention_days": forms.NumberInput(attrs={"min": 1}),
            "description": forms.Textarea(attrs={"rows": 3}),
            "sort_order": forms.NumberInput(),
        }

    def __init__(self, *args, facility=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.facility = facility
        # entity_type ist am Modell nicht ``blank=True`` (anders als
        # system_type) — als Formfeld bewusst optional, damit ein POST ohne
        # den Schlüssel (Bestand) nicht am „Pflichtfeld"-Fehler scheitert;
        # ``clean_entity_type`` liefert den Bestandsdefault EVENT nach.
        self.fields["retention_days"].label = _("Höchstfrist (verkürzt nur)")
        self.fields["retention_days"].help_text = _(
            "Diese Höchstfrist in Tagen verkürzt nur die Frist der Kontaktstufe; "
            "eine längere Höchstfrist verlängert sie nicht. "
            "Die Typ-Höchstfrist beginnt am Ereignisdatum. Leer lässt die Stufenfrist gelten."
        )
        self.fields["entity_type"].required = False
        # Kompatibel mit bestehenden Ohne-JS-/API-Posts, die das neue Feld aus
        # #1886 noch nicht senden: Anlage nutzt den Modell-Default, Bearbeitung
        # behaelt die vorhandene Position statt sie still auf 0 zu setzen.
        self.fields["sort_order"].required = False
        # Refs #2381: ``system_type``/``min_contact_stage`` sind Modellfelder
        # mit ``blank=True`` — Django erzeugt daraus ein ``TypedChoiceField``
        # mit einer Leer-Option, deren Text ohne eigene Angabe auf Djangos
        # unuebersetzten Blank-Choice-Text ("- Select an option -") faellt.
        label_blank_choice(self.fields["system_type"], _("— Kein Systemtyp —"))
        label_blank_choice(self.fields["min_contact_stage"], _("— Keine Angabe —"))
        # Neuobjekt: facility aus dem Scope an die Instanz binden, damit
        # form.save() (Service) den richtigen Mandanten schreibt.
        if facility is not None and self.instance.facility_id is None:
            self.instance.facility = facility
        # Bearbeitung: system_type/entity_type sind unveränderbar (Model
        # erzwingt es beim save()); disabled-Feld hält den Instanzwert und
        # ignoriert POST-Manipulation.
        # ACHTUNG: nicht über instance.pk prüfen — der UUID-Default macht den pk
        # schon VOR dem ersten Speichern truthy; _state.adding unterscheidet korrekt.
        if not self.instance._state.adding:
            self.fields["system_type"].disabled = True
            self.fields["entity_type"].disabled = True
        # ``report_key`` folgt bewusst NICHT dem Pauschalblock darüber: gesperrt
        # wird nur ein bereits gesetzter Wert (Entscheid E-K, Konzept #1847
        # §2.1). Das Vokabular ist das der **Typ**-Ebene.
        self._setup_report_key(get_report_key_vocabulary())
        self._append_help_text(
            "report_key",
            _(
                "Leer heißt: noch keine Aussage — Berichte, die alle Ereignisse gegenrechnen, weisen den Typ "
                "als Lücke aus. Soll er bewusst in keinem Bericht erscheinen, wählen Sie "
                "„Gehört ausdrücklich in keinen Bericht“."
            ),
        )
        self._append_help_text(
            "icon",
            _("Technischer Icon-Name, zum Beispiel users, heart oder file-text. Leer verwendet den Standard."),
        )
        self._append_help_text(
            "color",
            _(
                "Farbname für Kennzeichnungen, zum Beispiel green, rose oder blue. "
                "Bitte auf ausreichenden Kontrast zur Schrift achten."
            ),
        )
        self._append_help_text(
            "sort_order",
            _("Kleinere Zahlen erscheinen in Auswahllisten und Übersichten weiter oben."),
        )

        # Final-Review H (Refs #1058): beide Felder werden ausschliesslich im
        # Ereignis-Pfad ausgewertet (``enforce_document_type_retention`` zaehlt
        # ``Event``; ``min_contact_stage`` greift nur in der Ereignis-Anlage).
        # An Personen-/Fall-Typen sind sie wirkungslos. Der Hinweis bleibt auch
        # nach #1757 stehen: das Ausblenden ist reines JavaScript-Komfort, ohne
        # Alpine (und in jedem serverseitigen Re-Render) stehen die Felder da.
        entity_hint = _("Wirkt nur bei Dokumentationstypen für Ereignisse.")
        for field_name in self.EVENT_ONLY_FIELDS:
            # ``system_type`` traegt gleich unten einen eigenen, schaerferen
            # Hinweis (dort ist ein gesetzter Wert ein harter Formfehler, keine
            # blosse Wirkungslosigkeit).
            if field_name == "system_type":
                continue
            self._append_help_text(field_name, entity_hint)
        # Refs #1731: ``system_type`` ist der schaerfere Fall — das Select
        # bietet alle Werte an, ``clean()`` weist jeden gesetzten Wert bei
        # ``entity_type != event`` als Formfehler ab. Ohne diesen Hinweis
        # erfaehrt man die Regel erst beim Absenden. Wortlaut deckungsgleich
        # mit der Fehlermeldung in ``clean()``.
        self._append_help_text(
            "system_type",
            _("Nur für Dokumentationstypen mit Entitätstyp „Ereignis“ zulässig."),
        )
        self._append_help_text(
            "tally_enabled",
            _(
                "In der Strichliste erscheinen nur aktive Ereignistypen ohne Mindest-Kontaktstufe. "
                "Vorhandene Pflichtfelder müssen Zahlen-Pflichtfelder ohne Bedingung sein. "
                "Der Typ und alle Pflichtfelder müssen für die jeweilige Rolle sichtbar sein."
            ),
        )

    def clean_sort_order(self) -> int:
        value = self.cleaned_data.get("sort_order")
        return self.instance.sort_order if value is None else value

    # -- Template-Brücke für die Alpine-Komponente (Refs #1757) --------------
    #
    # Beide Werte kommen aus dem Formular, damit die Zuordnung
    # Entitätstyp→sichtbare Felder genau EINMAL gepflegt wird. Das Template
    # schreibt sie als ``data``-Attribute an den Feld-Container, die Komponente
    # (``static/js/alpine/config.js``) liest sie dort aus — sie kennt weder
    # Feldnamen noch Entitätstypen.

    @property
    def event_only_fields_attr(self) -> str:
        """Feldnamen für ``data-entity-scoped-fields`` (leerzeichengetrennt)."""
        return " ".join(self.EVENT_ONLY_FIELDS)

    @property
    def event_entity_type_value(self) -> str:
        """Entitätstyp-Wert, bei dem diese Felder sichtbar bleiben."""
        return str(DocumentType.EntityType.EVENT)

    @property
    def report_key_defaults_attr(self) -> str:
        """Vorbelegungen für ``data-report-key-defaults`` (``systemtyp:schlüssel``).

        Dasselbe Muster wie :attr:`event_only_fields_attr` (Refs #1757): die
        Zuordnung steht EINMAL im Formular, das Template reicht sie als
        ``data``-Attribut durch, die Alpine-Komponente kennt weder Systemtypen
        noch Berichtsschlüssel. Ohne JavaScript greift die Vorbelegung nicht —
        das Feld bleibt dann leer, was der sichere Zustand ist (keine
        Berichtszuordnung statt einer ungefragt gesetzten).
        """
        return " ".join(f"{system_type}:{report_key}" for system_type, report_key in self.REPORT_KEY_DEFAULTS.items())

    @property
    def report_key_bindings_json(self) -> str:
        """Show candidate report names before creation."""
        bindings = {}
        for key in get_report_key_vocabulary():
            bindings[key] = [str(backend.name) for backend in backends_for_report_key(key)]
        return json.dumps(bindings, ensure_ascii=False, separators=(",", ":"))

    @property
    def report_key_warnings_json(self) -> str:
        """Keep missing mappings separate from candidate report names."""
        warnings = {
            key: diagnose_type_bindings(DocumentType(facility=self.facility, report_key=key), self.facility)
            for key in get_report_key_vocabulary()
        }
        return json.dumps(warnings, ensure_ascii=False, separators=(",", ":"))

    @property
    def report_key_excluded_value(self) -> str:
        """Sentinel-Wert für die Live-Auskunft, ohne Duplikat im JavaScript."""
        return EXCLUDED

    def clean_name(self) -> str:
        name: str = self.cleaned_data["name"]
        if self.instance._state.adding:
            validate_slugifiable_name(name)
        return name

    def clean_entity_type(self) -> str:
        # Leere Auswahl (Feld ist ``required=False``, s. __init__) auf den
        # Bestandsdefault EVENT auffüllen — sonst würde ``full_clean()`` im
        # ModelForm-``_post_clean`` eine leere Zeichenkette gegen die
        # ``choices`` prüfen und (da das Modellfeld nicht ``blank=True`` ist)
        # eine ValidationError werfen, die als generischer Formfehler
        # aufschlüge statt als sinnvoller Default zu greifen.
        return self.cleaned_data.get("entity_type") or DocumentType.EntityType.EVENT

    def clean(self):
        # ``ModelForm.clean`` ist als ``dict | None`` typisiert (leere Form ⇒
        # None); ``or {}`` hält den strikten forms-mypy-Scope grün.
        cleaned = super().clean() or {}
        # Final-Review E (Refs #1058): ``system_type`` ist laut Paketschema nur
        # bei ``entity_type=event`` zulässig — und beide Felder sind nach dem
        # Anlegen unveränderlich. Ohne diese Prüfung entstünde über die
        # Config-UI ein dauerhaft nicht paketfähiger, nicht reparierbarer Typ.
        # Bewusst nur beim Anlegen: bei der Bearbeitung sind beide Felder
        # ``disabled`` (Werte kommen aus der Instanz), ein Bestandsobjekt mit
        # dieser Kombination bliebe sonst unbearbeitbar.
        if self.instance._state.adding:
            system_type = cleaned.get("system_type")
            entity_type = cleaned.get("entity_type")
            if system_type and entity_type and entity_type != DocumentType.EntityType.EVENT:
                self.add_error(
                    "system_type",
                    _("Ein Systemtyp ist nur für Dokumentationstypen mit Entitätstyp „Ereignis“ zulässig."),
                )
        # #1776 Punkt 5 (Bereinigen, ADR-039 Event-only-Doktrin): ``retention_days``
        # wirkt ausschliesslich an Ereignis-Typen (``enforce_document_type_retention``
        # zaehlt nur ``Event``). Ein an einem Personen-/Fall-Typ gesetzter Wert blieb
        # bisher dauerhaft gespeichert und war ueber die UI nicht mehr korrigierbar
        # (``entity_type`` ist nach dem Anlegen unveraenderbar) — toter, irrefuehrender
        # Datenbestand. NULLen statt Formfehler: das UI blendet das Feld fuer diesen
        # Entitaetstyp bereits aus (Refs #1757), ein Fehler waere fuer die Nutzerin
        # unsichtbar. Bestandswerte raeumt Migration 0132 auf.
        entity_type = cleaned.get("entity_type")
        if entity_type and entity_type != DocumentType.EntityType.EVENT:
            cleaned["retention_days"] = None
            # Die Strichliste legt ausschliesslich Events an. Das Feld wird im
            # UI fuer Personen-/Fall-Typen ausgeblendet; manipulierte Posts und
            # wirkungslose Legacy-Werte werden deshalb wie retention_days in
            # den kanonischen, deaktivierten Zustand ueberfuehrt.
            cleaned["tally_enabled"] = False

        if cleaned.get("sensitivity") == DocumentType.Sensitivity.HIGH and not self.instance._state.adding:
            for assignment in self.instance.fields.select_related("field_template"):
                field = assignment.field_template
                try:
                    validate_explicit_high_field(DocumentType.Sensitivity.HIGH, field.sensitivity, field.name)
                except ValidationError as error:
                    self.add_error("sensitivity", error)

        # Refs #1749 (ADR-040): Vorpruefung der Unique-Regel, seit dem Wegfall
        # von ``category`` auf ``(facility, name)``.
        name = cleaned.get("name")
        if name and self.facility is not None:
            qs = DocumentType.objects.for_facility(self.facility).filter(name=name)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise ValidationError(
                    _("Ein Dokumentationstyp mit diesem Namen existiert in dieser Einrichtung bereits.")
                )
        return cleaned


class FieldTemplateForm(ReportKeyFormMixin, TailwindFormMixin, forms.ModelForm):
    """Anlegen/Bearbeiten einer Feldvorlage.

    * ``facility`` kommt aus dem Request-Scope (kein Formularfeld) — der Service
      :func:`core.services.config.save_field_template` setzt sie beim Anlegen.
      Sie dient hier nur dem freundlichen Unique-Namens-Fehler.
    * ``slug`` ist **kein** Formularfeld: beim Anlegen erzeugt das Model ihn
      automatisch, danach ist er unveränderbar (``FieldTemplate.save``); die
      Update-Ansicht zeigt ihn read-only an.
    * Die inhaltlichen Regeln (HIGH-Sensibilität ⇒ verschlüsselt,
      ``default_value``-Registry-Validierung, Slug-Zeichensatz) liegen im Model
      (``FieldTemplate.clean``) und laufen über den ModelForm-``_post_clean``.
    * ``report_key`` (E3, Refs #1849) steht an der Stelle des entfernten
      ``statistics_category`` — dieselbe Position im Formular, aber mit
      Wirkung: der Schlüssel entscheidet, welche Feldvorlage die Minutensummen,
      den Spritzensaldo bzw. die Options-Verteilung speist. Das Vokabular ist
      das der **Feld**-Ebene (eigener Werteraum, Konzept #1847 §3.1).
    """

    class Meta:
        model = FieldTemplate
        fields = [
            "name",
            "field_type",
            "min_value",
            "max_value",
            "is_required",
            "is_encrypted",
            "sensitivity",
            "options_json",
            "default_value",
            "report_key",
            "help_text",
            "is_active",
        ]
        widgets = {
            "options_json": forms.Textarea(attrs={"rows": 3}),
            # ``default_value`` ist am Modell ein ``TextField`` — der
            # Django-Default waere eine Textarea. Der Default-Wert ist aber
            # einzeilig (Refs #907: Zahl, Datum, Option-Slug), deshalb bleibt
            # der Eintrag hier trotz fehlender Zusatz-Attribute stehen.
            "default_value": forms.TextInput(),
        }

    def __init__(self, *args, facility=None, used_option_slugs=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.facility = facility
        # The editor receives only identifiers from the shared usage service.
        self.used_option_slugs = set(used_option_slugs or ())
        self.used_option_slugs_attr = " ".join(sorted(self.used_option_slugs))
        self._original_options = list(self.instance.options_json or [])
        self.option_editor_errors: dict[str, str] = {}
        self._setup_report_key(get_field_key_vocabulary())
        # Refs #2381: ``sensitivity`` ist am Modell ``blank=True`` — Django
        # erzeugt daraus ein ``TypedChoiceField`` mit einer Leer-Option, deren
        # Text ohne eigene Angabe auf Djangos unuebersetzten Blank-Choice-Text
        # ("- Select an option -") faellt. Leer heisst hier "erbt vom
        # Dokumentationstyp" (Modell-Hilfetext) — der Platzhalter sagt das.
        label_blank_choice(self.fields["sensitivity"], _("— Vom Dokumentationstyp erben —"))

    @property
    def options_editor_rows(self):
        """Render submitted rows and retain blocked options with their server error."""
        _ = self.errors  # Populate entry errors before reading the bound JSON value.
        raw = self["options_json"].value()
        try:
            options = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            return []
        if not isinstance(options, list):
            return []
        options = [dict(option) for option in options if isinstance(option, dict)]
        present = {option.get("slug") for option in options if isinstance(option.get("slug"), str)}
        options.extend(
            dict(option)
            for option in self._original_options
            if option.get("slug") in self.option_editor_errors and option.get("slug") not in present
        )
        rows = []
        for option in options:
            slug = option.get("slug") if isinstance(option.get("slug"), str) else ""
            rows.append(
                {
                    "slug": slug,
                    "label": option.get("label") if isinstance(option.get("label"), str) else "",
                    "is_active": option.get("is_active") is not False,
                    "is_used": slug in self.used_option_slugs,
                    "error": self.option_editor_errors.get(slug, ""),
                }
            )
        return rows

    def clean(self):
        """E-J (Refs #1849), vierter Weg: der Schlüssel wird NACHTRÄGLICH gesetzt.

        Die drei Prüfungen aus C8 sitzen an den **Zuordnungs**-Pfaden. Hängen
        zwei Vorlagen aber schon **ohne** Schlüssel am selben Typ, entsteht die
        Doppelbindung erst hier — und ist danach irreparabel, weil die
        Änderbarkeits-Regel keinen der beiden Schlüssel mehr freigibt.

        In ``clean()`` statt ``clean_report_key()``: die Entscheidung braucht
        auch ``is_active`` (eine deaktivierte Vorlage bindet nicht), und
        Feldreihenfolge ist in ``clean_*``-Methoden keine verlässliche Zusage.
        """
        from core.services.config import conflicting_report_key_assignment

        cleaned = super().clean() or {}
        if cleaned.get("sensitivity") == "" and not self.instance._state.adding:
            for assignment in self.instance.document_type_fields.filter(
                document_type__facility=self.instance.facility,
                document_type__sensitivity=DocumentType.Sensitivity.HIGH,
            ).select_related("document_type"):
                try:
                    validate_explicit_high_field(
                        assignment.document_type.sensitivity, "", cleaned.get("name") or self.instance.name
                    )
                except ValidationError as error:
                    self.add_error("sensitivity", error)
                    break
        konflikt = conflicting_report_key_assignment(
            self.instance,
            cleaned.get("report_key"),
            is_active=cleaned.get("is_active", self.instance.is_active),
        )
        if konflikt is not None:
            _doc_type, bestand = konflikt
            self.add_error(
                "report_key",
                _(
                    "„%(neu)s“ trägt denselben Berichtsschlüssel wie die bereits zugeordnete Feldvorlage "
                    "„%(bestand)s“. Je Dokumentationstyp ist höchstens eine Feldvorlage je Berichtsschlüssel "
                    "zulässig — sonst zählt der Bericht keine von beiden."
                )
                % {"neu": cleaned.get("name") or self.instance.name, "bestand": bestand.name},
            )
        return cleaned

    def clean_name(self):
        name = self.cleaned_data["name"]
        if self.instance._state.adding:
            validate_slugifiable_name(name)
        if name and self.facility is not None:
            qs = FieldTemplate.objects.for_facility(self.facility).filter(name=name)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise ValidationError(_("Eine Feldvorlage mit diesem Namen existiert in dieser Einrichtung bereits."))
        return name

    # M24/AP4 (Refs #1738, ADR-039 §9): Der Typwechsel-Guard ``clean_field_type``
    # („Wechsel auf ``file`` blockieren, solange die Vorlage einem Personen-/
    # Fall-Typ zugeordnet ist", Final-Review F/Refs #1058) ist **ersatzlos
    # entfallen** — er war die vierte Schicht der E8-Sperre. Seit ADR-039 haengt
    # ein Anhang an Ereignis, Person oder Fall; ein FILE-Feld an einem
    # Personen-/Fall-Typ ist keine Sackgasse mehr, sondern der Regelfall.

    def clean_options_json(self):
        """Optionsstruktur wie im Paket-Schema validieren und normalisieren.

        Das ``JSONField`` prueft allein die JSON-Syntax. Alle Konsumenten
        erwarten aber eine eindeutige Liste aus ``slug``/``label`` und dem
        optionalen Boolean ``is_active``. Der visuelle Editor ist nur Komfort;
        diese serverseitige Pruefung bleibt auch fuer Ohne-JS-/Experten-Posts
        die Wahrheit.
        """
        value = self.cleaned_data.get("options_json")
        if value in (None, ""):
            value = []
        if not isinstance(value, list):
            raise ValidationError(_("Optionen müssen eine JSON-Liste sein."))
        if value and self.cleaned_data.get("field_type") not in {
            FieldTemplate.FieldType.SELECT,
            FieldTemplate.FieldType.MULTI_SELECT,
        }:
            raise ValidationError(_("Optionen sind nur für Auswahl- und Mehrfachauswahl-Felder zulässig."))

        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        allowed_keys = {"slug", "label", "is_active"}
        for index, option in enumerate(value, start=1):
            if not isinstance(option, dict):
                raise ValidationError(_("Option %(index)s muss ein JSON-Objekt sein.") % {"index": index})
            if set(option) - allowed_keys:
                raise ValidationError(_("Option %(index)s enthält unbekannte Schlüssel.") % {"index": index})

            slug = option.get("slug")
            label = option.get("label")
            active = option.get("is_active", True)
            if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
                raise ValidationError(
                    _("Option %(index)s braucht einen gültigen Slug aus a–z, 0–9 und Bindestrichen.") % {"index": index}
                )
            if slug in seen:
                raise ValidationError(_("Jeder Options-Slug darf nur einmal vorkommen."))
            if not isinstance(label, str) or not label.strip():
                raise ValidationError(_("Option %(index)s braucht eine Bezeichnung.") % {"index": index})
            if not isinstance(active, bool):
                raise ValidationError(
                    _("Der Aktiv-Status von Option %(index)s muss true oder false sein.") % {"index": index}
                )

            seen.add(slug)
            normalized.append({"slug": slug, "label": label.strip(), "is_active": active})
        if not self.instance._state.adding:
            from core.services.option_usage import validate_option_changes

            try:
                validate_option_changes(self.instance, normalized, default_value=self.data.get("default_value", ""))
            except ValidationError as error:
                for detail in error.error_dict["options_json"]:
                    if detail.code == "used_option_slugs" and detail.params:
                        for slug in detail.params["blocked_slugs"]:
                            self.option_editor_errors[slug] = detail.messages[0]
                raise ValidationError(error.error_dict["options_json"]) from error
        return normalized


class FieldGroupForm(TailwindFormMixin, forms.ModelForm):
    """Anlegen/Bearbeiten einer Feldgruppe (M0 P6, Refs #1056).

    * ``document_type`` kommt aus dem View-Scope (kein Formularfeld) — der
      Service :func:`core.services.config.save_field_group` setzt ihn beim
      Anlegen. Er dient hier dem freundlichen Unique-Namens-Fehler.
    * ``slug`` ist **kein** Formularfeld: beim Anlegen erzeugt das Model ihn
      automatisch aus dem Namen, danach ist er unveränderbar (``FieldGroup.save``).
    * Die zusammengesetzte Unique-Regel ``(document_type, name)``
      (Constraint ``unique_documenttype_fieldgroup_name``) wird als
      freundlicher Formfehler gemeldet statt als ``IntegrityError``
      (``document_type`` ist kein Formfeld, daher greift Djangos
      ModelForm-Unique-Check hier nicht).
    """

    class Meta:
        model = FieldGroup
        fields = ["name", "is_collapsible"]

    def __init__(self, *args, doc_type=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.doc_type = doc_type
        # Neuobjekt: document_type aus dem Scope an die Instanz binden, damit
        # form.save() (Service) den richtigen Dokumentationstyp schreibt.
        if doc_type is not None and self.instance.document_type_id is None:
            self.instance.document_type = doc_type

    def clean_name(self):
        name = self.cleaned_data["name"]
        if self.instance._state.adding:
            validate_slugifiable_name(name)
        if name and self.doc_type is not None:
            qs = FieldGroup.objects.filter(document_type=self.doc_type, name=name)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise ValidationError(
                    _("Eine Feldgruppe mit diesem Namen existiert für diesen Dokumentationstyp bereits.")
                )
        return name


class DocumentTypeFieldAddForm(TailwindFormMixin, forms.Form):
    """Ordnet eine Feldvorlage einem Dokumentationstyp zu (M0 P6, Refs #1056).

    Kein ``ModelForm`` — das Anlegen läuft über
    :func:`core.services.config.add_field_to_doctype` (Audit + ``sort_order``-
    Vergabe). Das Formular kapselt nur die facility-/doc-type-gescopte Auswahl:

    * ``field_template``: nur Vorlagen der eigenen Einrichtung, die diesem
      Dokumentationstyp **noch nicht** zugeordnet sind (Doppelzuordnung wird so
      bereits als „ungültige Auswahl"-Formfehler abgefangen, nie als 500).
    * ``field_group``: optional, nur Gruppen **dieses** Dokumentationstyps
      (``– Ohne Gruppe –`` = ungruppiert). Eine Gruppe aus einem anderen
      Dokumentationstyp wehrt der Service mit ``ValueError`` ab (View → 400).

    ``DocumentTypeField`` trägt selbst **kein** ``is_required``-Feld (die
    Pflicht-Eigenschaft liegt auf der ``FieldTemplate``) — daher gibt es hier
    bewusst keine ``is_required``-Checkbox.
    """

    field_template = forms.ModelChoiceField(
        queryset=FieldTemplate.objects.none(),
        label=_("Feldvorlage"),
        # Refs #2381: Django 6.1 faellt ohne eigenes ``empty_label`` auf den
        # unuebersetzten Blank-Choice-Text ("- Select an option -") zurueck.
        empty_label=_("— Bitte auswählen —"),
    )
    field_group = forms.ModelChoiceField(
        queryset=FieldGroup.objects.none(),
        required=False,
        label=_("Feldgruppe"),
        empty_label=_("– Ohne Gruppe –"),
    )

    def __init__(self, *args, doc_type=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.doc_type = doc_type
        if doc_type is not None:
            self.fields["field_template"].queryset = (  # type: ignore[attr-defined]
                FieldTemplate.objects.for_facility(doc_type.facility)
                .exclude(document_type_fields__document_type=doc_type)
                .order_by("name")
            )
            self.fields["field_group"].queryset = FieldGroup.objects.filter(  # type: ignore[attr-defined]
                document_type=doc_type
            ).order_by("sort_order", "name")

    def clean_field_template(self) -> FieldTemplate:
        """E-J (Refs #1849): Feld-Kardinalitaet je (Typ, Berichtsschlüssel).

        Die erste von drei Schichten (Formular, Service, Paket-Loader) — hier,
        weil eine abgewiesene Auswahl als Feldfehler neben dem Select landet
        statt als 500. Die Regel selbst steht im Service, damit alle Schichten
        dieselbe Antwort geben.
        """
        from core.services.config import conflicting_report_key_field

        field_template = self.cleaned_data["field_template"]
        if self.doc_type is None:
            return field_template
        # The service repeats this check under a lock before writing.
        current_level = (
            DocumentType.objects.for_facility(self.doc_type.facility_id).get(pk=self.doc_type.pk).sensitivity
        )
        validate_explicit_high_field(current_level, field_template.sensitivity, field_template.name)
        konflikt = conflicting_report_key_field(self.doc_type, field_template)
        if konflikt is not None:
            raise ValidationError(
                _(
                    "„%(neu)s“ trägt denselben Berichtsschlüssel wie die bereits zugeordnete Feldvorlage "
                    "„%(bestand)s“. Je Dokumentationstyp ist höchstens eine Feldvorlage je Berichtsschlüssel "
                    "zulässig — sonst zählt der Bericht keine von beiden."
                )
                % {"neu": field_template.name, "bestand": konflikt.name}
            )
        return field_template


class FieldConditionForm(TailwindFormMixin, forms.Form):
    """Regel-Editor-Formular für eine Feldbedingung (M1 P6, Refs #1057).

    Kein ``ModelForm`` — das Anlegen/Ändern läuft über
    :mod:`core.services.config` (Audit + ``full_clean``). Das Formular kapselt

    * die **doc-type-gescopte** Feldauswahl: ``target_field`` über alle Felder,
      ``depends_on_field`` gefiltert auf zulässige Quelltypen
      (``field_type in ALLOWED_SOURCE_FIELD_TYPES``) und **nicht** verschlüsselt
      — verbotene Quellen sind so gar nicht wählbar (E19, #774); das
      Modell-``clean()`` bleibt als zweite Verteidigung;
    * die Operator×Typ-Matrix (E9) und die **typabhängige Konvertierung** des
      generischen String-Felds ``value`` nach JSON (``cleaned_data["value_json"]``):
      number→int/float, boolean→bool, date/time→ISO-validiert (time normalisiert
      auf ``"HH:MM:SS"``), select/multi_select→Slug aus ``options_json``;
      ``VALUELESS_OPERATORS`` (``is_empty``/``is_not_empty``) ⇒ ``value_json=None``.
    """

    # Refs #1834: ``operator`` ist ein reines Validierungsfeld — gerendert wird
    # der Operator ueber das HTMX-Partial ``condition_operator_input.html``
    # (#1715), das seine Klassen selbst mitbringt. Ein gestyltes Widget hier
    # waere toter Code, der beim naechsten Klassen-Wechsel stumm driftet.
    tailwind_opt_out = ("operator",)

    target_field = forms.ModelChoiceField(
        queryset=DocumentTypeField.objects.none(),
        label=_("Zielfeld"),
        # Refs #2381: Django 6.1 faellt ohne eigenes ``empty_label`` auf den
        # unuebersetzten Blank-Choice-Text ("- Select an option -") zurueck.
        empty_label=_("— Bitte auswählen —"),
        widget=forms.Select(attrs={"data-testid": "condition-target"}),
    )
    depends_on_field = forms.ModelChoiceField(
        queryset=DocumentTypeField.objects.none(),
        label=_("Quellfeld"),
        empty_label=_("— Bitte auswählen —"),
        widget=forms.Select(attrs={"data-testid": "condition-source"}),
    )
    # Reines Validierungsfeld: gerendert wird der Operator über das
    # HTMX-Partial ``condition_operator_input.html`` (#1715), nicht das Widget.
    operator = forms.ChoiceField(
        choices=FieldCondition.Operator.choices,
        label=_("Operator"),
    )
    value = forms.CharField(
        required=False,
        label=_("Vergleichswert"),
        widget=forms.TextInput(attrs={"data-testid": "condition-value"}),
    )
    # Hidden (Plan § 5): die OR-Gruppe wird nicht getippt, sondern über die
    # Board-Affordanzen „UND-Bedingung hinzufügen" (Index der Gruppe) bzw.
    # „ODER-Gruppe hinzufügen" (max+1, server-vorbelegt) gesetzt.
    group_index = forms.IntegerField(
        min_value=0,
        required=False,
        widget=forms.HiddenInput(attrs={"data-testid": "condition-group-index"}),
    )

    def __init__(self, *args: Any, document_type: DocumentType | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.document_type = document_type
        if document_type is not None:
            from core.services.config import block_ordered_dtf_queryset

            # #1713-Folge: Dropdowns in globaler Block-Reihenfolge (wie das
            # Formular rendert) — flaches sort_order hätte Positions-Ties.
            self.fields["target_field"].queryset = block_ordered_dtf_queryset(  # type: ignore[attr-defined]
                document_type
            )
            self.fields["depends_on_field"].queryset = block_ordered_dtf_queryset(  # type: ignore[attr-defined]
                document_type
            ).filter(
                field_template__field_type__in=ALLOWED_SOURCE_FIELD_TYPES,
                field_template__is_encrypted=False,
            )
            # #1715: Die Verkabelung ist aufgespalten (NICHT ergänzt — sonst
            # doppelter Value-Fetch/Race beim Quellwechsel). Die QUELLE holt jetzt
            # AUSSCHLIESSLICH den typabhängigen Operator-Slot; die Kette
            # Quelle→Operator→Value läuft danach über das ``load`` des frisch
            # geswappten Operator-Selects (condition_operator_input.html). Der
            # Operator-Slot selbst wird als Partial gerendert (nicht das
            # Form-Widget) und trägt die Value-Endpoint-Attribute dort.
            # ``hx-swap="outerHTML"`` explizit (statt Erbe vom <form>): der
            # Operator-Slot-Wrapper ersetzt sich als Ganzes durch den neuen Wrapper —
            # idempotent über beliebig viele Quellwechsel.
            operator_url = reverse("core:config_condition_operator_input", kwargs={"pk": document_type.pk})
            self.fields["depends_on_field"].widget.attrs.update(
                {
                    "hx-get": operator_url,
                    "hx-trigger": "change",
                    "hx-target": "next .condition-operator-slot",
                    "hx-swap": "outerHTML",
                    "hx-include": "closest form",
                }
            )

    def clean(self) -> dict[str, Any]:
        # ``forms.Form.clean`` ist ``dict | None`` — ``or {}`` hält den strikten
        # forms-mypy-Scope grün (Muster DocumentTypeForm.clean).
        cleaned: dict[str, Any] = super().clean() or {}
        if cleaned.get("group_index") is None:
            cleaned["group_index"] = 0

        source: DocumentTypeField | None = cleaned.get("depends_on_field")
        operator: str = cleaned.get("operator") or ""
        # Feld-Level-Fehler (Pflichtfeld leer) sind bereits gesetzt — dann keine
        # Wertkonvertierung; ``value_json`` bleibt ungesetzt.
        if source is None or not operator:
            return cleaned

        field_type = source.field_template.field_type
        allowed = OPERATORS_BY_FIELD_TYPE.get(field_type, frozenset())
        if operator not in allowed:
            self.add_error("operator", _("Dieser Operator ist für den Feldtyp des Quellfelds nicht zulässig."))
            return cleaned

        if operator in VALUELESS_OPERATORS:
            # is_empty/is_not_empty tragen nie einen Vergleichswert.
            cleaned["value_json"] = None
            return cleaned

        raw = (cleaned.get("value") or "").strip()
        if raw == "":
            self.add_error("value", _("Für diesen Operator ist ein Vergleichswert erforderlich."))
            return cleaned

        try:
            cleaned["value_json"] = parse_condition_value(field_type, source.field_template, raw)
        except ValidationError as exc:
            self.add_error("value", exc)
        return cleaned
