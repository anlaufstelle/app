"""Forms für die Formular-Konfiguration (M0, Refs #1056).

DocumentType- und FieldTemplate-Formulare für die Config-CRUD-UI unter
``/config/``. Die ``facility`` ist bewusst **kein** Formularfeld — sie kommt
aus dem Request-Scope (``request.current_facility``) und wird beim Speichern
über :mod:`core.services.config` gesetzt. Die Formulare kapseln nur die
UI-Validierung (Widget-CSS, freundliche Unique-Fehler, Immutabilitäten).
"""

from __future__ import annotations

import datetime
from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from core.forms.widgets import INPUT_CSS
from core.models import DocumentType, DocumentTypeField, FieldCondition, FieldGroup, FieldTemplate
from core.models.document_type import _RESERVED_SLUGS, slug_base
from core.services.conditions import (
    ALLOWED_SOURCE_FIELD_TYPES,
    OPERATORS_BY_FIELD_TYPE,
    VALUELESS_OPERATORS,
)


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


class DocumentTypeForm(forms.ModelForm):
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
    EVENT_ONLY_FIELDS = ("system_type", "min_contact_stage", "retention_days")

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
            "min_contact_stage",
            "retention_days",
            "description",
            "is_active",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CSS}),
            "entity_type": forms.Select(attrs={"class": INPUT_CSS}),
            "sensitivity": forms.Select(attrs={"class": INPUT_CSS}),
            "system_type": forms.Select(attrs={"class": INPUT_CSS}),
            "min_contact_stage": forms.Select(attrs={"class": INPUT_CSS}),
            "retention_days": forms.NumberInput(attrs={"class": INPUT_CSS, "min": 1}),
            "description": forms.Textarea(attrs={"class": INPUT_CSS, "rows": 3}),
        }

    def __init__(self, *args, facility=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.facility = facility
        # entity_type ist am Modell nicht ``blank=True`` (anders als
        # system_type) — als Formfeld bewusst optional, damit ein POST ohne
        # den Schlüssel (Bestand) nicht am „Pflichtfeld"-Fehler scheitert;
        # ``clean_entity_type`` liefert den Bestandsdefault EVENT nach.
        self.fields["entity_type"].required = False
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

    def _append_help_text(self, field_name: str, hint) -> None:
        """Haengt ``hint`` an den (Modell-)``help_text`` des Feldes an."""
        existing = self.fields[field_name].help_text
        self.fields[field_name].help_text = f"{existing} {hint}".strip() if existing else hint

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


class FieldTemplateForm(forms.ModelForm):
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
    """

    class Meta:
        model = FieldTemplate
        fields = [
            "name",
            "field_type",
            "is_required",
            "is_encrypted",
            "sensitivity",
            "options_json",
            "default_value",
            "statistics_category",
            "help_text",
            "is_active",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CSS}),
            "field_type": forms.Select(attrs={"class": INPUT_CSS}),
            "sensitivity": forms.Select(attrs={"class": INPUT_CSS}),
            "options_json": forms.Textarea(attrs={"class": INPUT_CSS, "rows": 3}),
            "default_value": forms.TextInput(attrs={"class": INPUT_CSS}),
            "statistics_category": forms.TextInput(attrs={"class": INPUT_CSS}),
            "help_text": forms.TextInput(attrs={"class": INPUT_CSS}),
        }

    def __init__(self, *args, facility=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.facility = facility

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
        # JSONField liefert bei leerem Textarea ``None``; das Model-Feld ist
        # NOT NULL mit ``default=list`` — leeres Feld also als leere Liste
        # normalisieren, damit kein IntegrityError am Save entsteht.
        value = self.cleaned_data.get("options_json")
        if value in (None, ""):
            return []
        return value


class FieldGroupForm(forms.ModelForm):
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
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CSS}),
        }

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


class DocumentTypeFieldAddForm(forms.Form):
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
        widget=forms.Select(attrs={"class": INPUT_CSS}),
    )
    field_group = forms.ModelChoiceField(
        queryset=FieldGroup.objects.none(),
        required=False,
        label=_("Feldgruppe"),
        empty_label=_("– Ohne Gruppe –"),
        widget=forms.Select(attrs={"class": INPUT_CSS}),
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


class FieldConditionForm(forms.Form):
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

    target_field = forms.ModelChoiceField(
        queryset=DocumentTypeField.objects.none(),
        label=_("Zielfeld"),
        widget=forms.Select(attrs={"class": INPUT_CSS, "data-testid": "condition-target"}),
    )
    depends_on_field = forms.ModelChoiceField(
        queryset=DocumentTypeField.objects.none(),
        label=_("Quellfeld"),
        widget=forms.Select(attrs={"class": INPUT_CSS, "data-testid": "condition-source"}),
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
        widget=forms.TextInput(attrs={"class": INPUT_CSS, "data-testid": "condition-value"}),
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
            cleaned["value_json"] = self._convert_value(field_type, source.field_template, raw)
        except ValidationError as exc:
            self.add_error("value", exc)
        return cleaned

    @staticmethod
    def _convert_value(field_type: str, template: FieldTemplate, raw: str) -> Any:
        """Konvertiert den generischen String-``value`` typabhängig nach JSON (E19)."""
        types = FieldTemplate.FieldType
        if field_type == types.NUMBER:
            try:
                return int(raw)
            except ValueError:
                try:
                    return float(raw)
                except ValueError:
                    raise ValidationError(_("Bitte eine gültige Zahl eingeben.")) from None
        if field_type == types.BOOLEAN:
            low = raw.lower()
            if low in ("true", "1", "ja"):
                return True
            if low in ("false", "0", "nein"):
                return False
            raise ValidationError(_("Bitte „wahr“ oder „falsch“ auswählen."))
        if field_type == types.DATE:
            try:
                datetime.date.fromisoformat(raw)
            except ValueError:
                raise ValidationError(_("Bitte ein gültiges Datum (JJJJ-MM-TT) eingeben.")) from None
            return raw
        if field_type == types.TIME:
            try:
                parsed = datetime.time.fromisoformat(raw)
            except ValueError:
                raise ValidationError(_("Bitte eine gültige Uhrzeit eingeben.")) from None
            # E11-Normalisierung: Editor speichert time als „HH:MM:SS" (JS hängt
            # an „HH:MM" ein „:00" an) — sonst schlagen equals-Vergleiche fehl.
            return parsed.isoformat(timespec="seconds")
        if field_type in (types.SELECT, types.MULTI_SELECT):
            known = {option.get("slug") for option in (template.options_json or [])}
            if raw not in known:
                raise ValidationError(_("Bitte eine gültige Option auswählen."))
            return raw
        # text/textarea (und Defensive): String übernehmen (Vergleich exakt, ADR-035).
        return raw
