"""Entity-agnostische Basis für dynamische ``data_json``-Formulare (M2, Refs #1058).

Bis M2 lebte diese Logik als ``DynamicEventDataForm`` in
:mod:`core.forms.events`. Sie war nie event-spezifisch: aufgebaut wird
ausschliesslich aus einem ``DocumentType``, seinen ``FieldTemplate``-Zuordnungen
und den ``FieldCondition``s — alles Dinge, die Client und Case in den folgenden
M2-Phasen ebenfalls bekommen. Der Body zieht daher unveraendert hierher um;
``DynamicEventDataForm`` erbt nur noch (leerer Body), sodass sich der Event-Pfad
in keinem beobachtbaren Verhalten aendert.

M24/AP4 (Refs #1738, ADR-039 §9): Mit der owner-generischen Attachment-Pipeline
ist auch die letzte Auspraegungs-Differenz (das Flag ``exclude_file_fields``)
entfallen — Event-, Personen- und Fall-Form sind jetzt vollstaendig identisch.
"""

from __future__ import annotations

import logging
from typing import Any

from django import forms
from django.conf import settings as django_settings
from django.forms.boundfield import BoundField
from django.utils.translation import gettext_lazy as _

from core.constants import DEFAULT_ALLOWED_FILE_TYPES, DEFAULT_MAX_FILE_SIZE_MB
from core.forms.widgets import INPUT_CSS
from core.models import DocumentType, FieldCondition, FieldGroup, FieldTemplate
from core.models.settings import Settings
from core.services.conditions import (
    FILE,
    NUMBER,
    build_conditions_payload,
    evaluate_conditions,
    is_empty,
    normalize_value,
)
from core.services.config import block_ordered_dtf_queryset

logger = logging.getLogger(__name__)


def document_type_label(document_type: DocumentType) -> str:
    """Dropdown-Label eines Dokumentationstyps; deaktivierte werden markiert.

    Muster :func:`_select_choices` (Refs #1160): ein deaktivierter Wert des
    BEARBEITETEN Datensatzes bleibt waehlbar, wird aber sichtbar als solcher
    gekennzeichnet — sonst sieht die Leitung nicht, dass dieser Typ fuer
    Neuanlagen nicht mehr angeboten wird.

    M2 P8 (Refs #1058): aus ``forms/clients.py`` hierher gezogen — die
    Beschriftung ist nicht personen-spezifisch und wird von jedem Formular mit
    editierbarem ``document_type`` gebraucht (Person P7, Fall P8).
    ``forms/clients.py`` behaelt den Alt-Namen ``_document_type_label`` als
    Import-Alias (Muster P3/P7: Umzug + Alias-Re-Export).
    """
    if document_type.is_active:
        return document_type.name
    # Refs #1933: Aufruf vor dem f-String — in einem f-String-Ausdruck sieht
    # xgettext 0.21 (CI-Toolchain) ihn nicht.
    inactive_label = _("deaktiviert")
    return f"{document_type.name} ({inactive_label})"


class MultipleFileInput(forms.ClearableFileInput):
    """ClearableFileInput-Widget mit ``multiple``-Attribut — Django erlaubt
    Multi-File-Upload seit 5.0, aber der Standard-Widget blockiert das aktiv.
    Refs #622.
    """

    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """FileField, das eine Liste von UploadedFile zurückgibt (auch für N=0/1).

    Wird für FILE-Feldtypen in :class:`DynamicDataForm` verwendet, damit
    mehrere Dateien pro Feld hochgeladen werden können (Refs #622).
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            files = [single_clean(d, initial) for d in data if d]
        elif data:
            files = [single_clean(data, initial)]
        else:
            files = []
        # Refs #1268 (T3): Datei-Anzahl pro Feld/POST cappen — sonst kann ein
        # einzelner Request beliebig viele Dateien einschleusen (Memory-DoS).
        max_files = getattr(django_settings, "FILE_VAULT_MAX_UPLOAD_FILES", 20)
        if len(files) > max_files:
            raise forms.ValidationError(
                _("Zu viele Dateien (%(n)d). Maximal %(max)d pro Feld.") % {"n": len(files), "max": max_files}
            )
        return files


def _select_choices(ft, initial_data, select_type):
    """Refs #1160: Choices fuer SELECT/MULTI_SELECT inkl. Beibehaltung
    inaktiver Werte des bearbeiteten Datensatzes.

    ``select_type`` ist die ``SELECT``-Konstante aus ``core.services.system`` —
    bei SELECT wird die Leer-Option vorangestellt. Wirkungs-identisch zum
    frueheren Inline-Block in :meth:`DynamicDataForm.__init__`.
    """
    choices = [("", "---------")] + list(ft.choices) if ft.field_type == select_type else list(ft.choices)

    # Bei Bearbeitung: inaktive Werte des Datensatzes beibehalten
    if initial_data and ft.slug in initial_data:
        current_values = initial_data[ft.slug]
        if not isinstance(current_values, list):
            current_values = [current_values] if current_values else []
        for o in ft.options_json or []:
            if not o.get("is_active", True) and o["slug"] in current_values:
                choices.append((o["slug"], f"{o['label']} (deaktiviert)"))

    return choices


def build_dynamic_form_field(ft, initial_data):
    """Refs #1160: Ein einzelnes Formularfeld aus einem ``FieldTemplate`` bauen.

    Aus :meth:`DynamicDataForm.__init__` herausgezogen — kapselt die
    Field-Klassen-/Widget-/CSS-/Choices-/Initial-Auswahl pro Feldtyp.
    Wirkungs-identisch; FILE -> ``MultipleFileField`` (Refs #622), die
    SELECT/MULTI_SELECT-Choices stammen aus :func:`_select_choices`.
    """
    from core.services.system import (
        FILE,
        MULTI_SELECT,
        NUMBER,
        SELECT,
        get_form_field_cls_for_file,
        get_spec,
    )

    spec = get_spec(ft.field_type)

    # FILE bekommt den MultipleFileField — der unterstuetzt
    # Multi-Upload (Refs #622). Der Registry-Eintrag fuer FILE
    # nennt nur ``forms.FileField`` als generischen Marker.
    if ft.field_type == FILE:
        field_cls: type[forms.Field] = get_form_field_cls_for_file()
    else:
        field_cls = spec.form_field_cls

    widget = spec.widget_factory()

    css = INPUT_CSS
    if not isinstance(widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
        widget.attrs.setdefault("class", css)
    else:
        widget.attrs.setdefault("class", "rounded border-subtle text-accent")

    kwargs_copy: dict[str, Any] = {
        "widget": widget,
        "required": ft.is_required,
        "label": ft.name,
    }
    if ft.field_type == NUMBER:
        kwargs_copy["min_value"] = ft.min_value
        kwargs_copy["max_value"] = ft.max_value
    if ft.help_text:
        kwargs_copy["help_text"] = ft.help_text

    if ft.field_type in (SELECT, MULTI_SELECT):
        kwargs_copy["choices"] = _select_choices(ft, initial_data, SELECT)

    field = field_cls(**kwargs_copy)

    if initial_data and ft.slug in initial_data:
        field.initial = initial_data[ft.slug]
    elif spec.allows_default:
        default_initial = ft.get_default_initial()
        if default_initial is not None:
            field.initial = default_initial

    return field


# Rückwärtskompatibler Alias für bestehende Event-Formular-Konsumenten. Neue
# Formulare importieren ausschließlich den öffentlichen Namen.
_build_dynamic_field = build_dynamic_form_field


def _resolve_upload_constraints(facility):
    """Refs #1160/#771: ``(allowed, max_mb, max_bytes)`` fuer Upload-Validierung.

    Fail-closed: fehlende Settings oder leere Whitelist greifen auf
    ``DEFAULT_ALLOWED_FILE_TYPES`` / ``DEFAULT_MAX_FILE_SIZE_MB`` zurueck,
    statt jeden Upload stillschweigend durchzulassen.
    """
    try:
        facility_settings = Settings.objects.get(facility=facility)
    except Settings.DoesNotExist:
        facility_settings = None
    raw = (facility_settings.allowed_file_types or "") if facility_settings else ""
    allowed = {ext.strip().lower().lstrip(".") for ext in raw.split(",") if ext.strip()}
    if not allowed:
        allowed = set(DEFAULT_ALLOWED_FILE_TYPES)
    max_mb = facility_settings.max_file_size_mb if facility_settings else DEFAULT_MAX_FILE_SIZE_MB
    max_bytes = max_mb * 1024 * 1024
    return allowed, max_mb, max_bytes


def _file_upload_errors(uploaded, allowed, max_mb, max_bytes):
    """Refs #1160: Validierungsfehler fuer eine einzelne hochgeladene Datei.

    Liefert eine Liste lazy-uebersetzter Fehlermeldungen (Dateityp nicht
    erlaubt / Datei zu gross). Wirkungs-identisch zum frueheren Inline-Block.
    """
    errors = []
    ext = uploaded.name.rsplit(".", 1)[-1].lower() if "." in uploaded.name else ""
    if allowed and ext not in allowed:
        errors.append(
            _("Dateityp .%(ext)s nicht erlaubt. Erlaubt: %(allowed)s")  # pragma: no mutate
            % {"ext": ext, "allowed": ", ".join(sorted(allowed))}
        )
    if uploaded.size > max_bytes:
        errors.append(
            _("Datei zu groß (%(size)d MB). Maximum: %(max)d MB")  # pragma: no mutate
            % {
                "size": uploaded.size // (1024 * 1024),
                "max": max_mb,
            }
        )
    return errors


# Sentinel für das lazy-gecachte Conditions-Payload (``None`` ist ein gültiges
# Ergebnis „keine Bedingungen", muss vom „noch nicht berechnet" unterscheidbar sein).
_PAYLOAD_UNSET: Any = object()


def _coerce_condition_number(value: Any) -> Any:
    """Number-Ist-Werte für die Bedingungsauswertung auf ``float`` ziehen (M1/P4).

    Djangos Number-Formfelder liefern je nach Registry-Wahl ``int`` oder ``Decimal``
    in ``cleaned_data``. Der Bedingungs-``value`` kommt als JSON-Zahl (``int``/``float``).
    Ein ``Decimal`` ist NICHT gleich dem gleichlautenden ``float`` (``Decimal("1.1")
    != 1.1``) — ohne Koerzierung schlägt eine ``equals``-Bedingung genau dann
    fehl. ``float`` spiegelt zugleich die JS-Seite (``parseFloat``, P5) und hält
    FE/BE deckungsgleich. ``None`` (leerer Wert) bleibt ``None``.
    """
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


class DynamicDataForm(forms.Form):
    """Dynamic form based on DocumentType fields — entity-agnostisch (M2, Refs #1058).

    Refs #907: Form-Field-, Widget-, Default-Auswahl pro Feldtyp
    laeuft ueber :data:`core.services.system.field_types.FIELD_TYPE_REGISTRY` —
    die alte ``FIELD_TYPE_MAP`` ist ersetzt. SELECT/MULTI_SELECT-Choices
    und die FILE-Sonderlogik bleiben in dieser Form, da sie auf Form-State
    (``initial_data``, deaktivierte Options) zugreifen.

    Traegerentitaet ist allein der ``document_type``; ``facility`` dient nur der
    Upload-Validierung.

    M24/AP4 (Refs #1738, ADR-039 §9): Das Flag ``exclude_file_fields`` ist
    **ersatzlos entfallen** — mit der owner-generischen Attachment-Pipeline gibt
    es keinen Grund mehr, FILE-Felder je nach Entitaet zu ueberspringen. Event-,
    Personen- und Fall-Formulare unterscheiden sich damit in gar nichts mehr;
    die drei Unterklassen bleiben als leere Huellen fuer stabile Importpfade und
    Lognamen (``type(form).__name__``).
    """

    def __init__(self, *args, document_type=None, initial_data=None, facility=None, existing_file_slugs=None, **kwargs):
        self.facility = facility
        self.document_type = document_type
        # M0/P4 (Refs #1056): slug -> FieldGroup | None. Aus dem select_related
        # unten (kein Extra-Query). ``grouped_fields`` liest daraus die Gruppe je
        # Feld — auch nachdem ``remove_restricted_fields`` (#774) Felder entfernt.
        self._field_group_of: dict[str, FieldGroup | None] = {}
        # M1/P4 (Refs #1057): slug -> FieldTemplate. Fällt kostenlos aus demselben
        # select_related-Query ab und wird von clean() (Feldtyp/Pflicht) gebraucht;
        # ``ft`` wurde bislang im Loop verworfen.
        self._field_templates: dict[str, FieldTemplate] = {}
        # M2/#1713 (globale Block-Sequenz): slug -> Blockposition. Für ein freies
        # (ungruppiertes) Feld ist das seine globale Position, für ein gruppiertes
        # Feld sein Innen-Index. ``grouped_fields`` interleavt Gruppen- und freie
        # Feldblöcke ausschließlich anhand dieser Position (NICHT anhand der
        # ``self.fields``-Iterationsreihenfolge). ``_dtf_pk`` liefert den
        # deterministischen Tie-Break bei Positionsgleichheit.
        self._block_position: dict[str, int] = {}
        self._dtf_pk: dict[str, str] = {}
        # E17: FILE-Slugs mit bestehendem Anhang-Marker (aus den Update-Views), damit
        # ein vorhandener Anhang beim Bearbeiten als „gefüllt" zählt.
        self._existing_file_slugs: set[str] = set(existing_file_slugs or ())
        # M1/P4 (#1716): FieldConditions dieses Typs werden EINMAL vollständig
        # geladen; sowohl die Ziel-Slugs (Pflicht-Relaxierung) als auch das
        # E12-Payload (``conditions_payload``) leiten sich daraus ab — statt zweier
        # separater Condition-Queries (values_list-Slugs + Payload-Query).
        self._field_conditions: list[FieldCondition] = []
        # Ziel-Slugs aller Bedingungen dieses DocumentType (Pflicht wird für sie nur
        # bedingt/sichtbarkeitsabhängig durchgesetzt). Vor remove_restricted_fields
        # bestimmt — clean() schneidet zur Aufrufzeit mit self.fields.
        self._condition_target_slugs: set[str] = set()
        super().__init__(*args, **kwargs)
        if not document_type:
            return

        # #1713/#1719-Folge: Felder in globaler BLOCK-Reihenfolge einfügen —
        # ``self.fields``-Iterationsreihenfolge == Anzeige-Reihenfolge (darauf
        # verlassen sich z. B. der Serienerfassungs-Autofocus und alle
        # "erstes Feld"-Konsumenten). Ein flaches ``order_by("sort_order")``
        # hätte Ties zwischen Innen-Indizes und Globalpositionen.
        dtf_qs = block_ordered_dtf_queryset(document_type)

        for dtf in dtf_qs:
            ft = dtf.field_template
            self.fields[ft.slug] = build_dynamic_form_field(ft, initial_data)
            self._field_group_of[ft.slug] = dtf.field_group
            self._field_templates[ft.slug] = ft
            self._block_position[ft.slug] = dtf.sort_order
            self._dtf_pk[ft.slug] = str(dtf.pk)

        # E16 (#1716): Bedingungen EINMAL laden (select_related für die Payload-
        # Erzeugung), Ziel-Slugs in Python ableiten. Ziel-Felder Django-seitig
        # optional machen (required=False) und den Pflicht-Marker separat setzen —
        # die Pflicht erzwingt clean() nur für tatsächlich sichtbare Ziele. Der
        # Marker trägt die ursprüngliche Pflicht (Pflicht-Stern im Template).
        self._field_conditions = list(
            FieldCondition.objects.filter(target_field__document_type=document_type).select_related(
                "target_field__field_template", "depends_on_field__field_template"
            )
        )
        self._condition_target_slugs = {c.target_field.field_template.slug for c in self._field_conditions}
        for slug in self._condition_target_slugs & set(self.fields):
            ft = self._field_templates[slug]
            self.fields[slug].required = False
            self.fields[slug].is_conditionally_required = ft.is_required  # type: ignore[attr-defined]

    def grouped_fields(self) -> list[tuple[FieldGroup | None, list[BoundField]]]:
        """Gebundene Felder als **interleavte Block-Sequenz** (M0/P4 + #1713).

        Liefert ``[(gruppe_oder_None, [BoundField, ...]), ...]`` — Gruppenblöcke
        und ``None``-Runs freier Felder in EINER globalen Reihenfolge:

        - **Blockposition** = ``FieldGroup.sort_order`` (Gruppenblock) bzw. der
          ``DocumentTypeField.sort_order`` des FREIEN Felds (Feldblock); die
          Blockliste wird nach dieser Position sortiert (Tie-Break: Gruppe vor
          Feld, dann Name), NICHT nach der ``self.fields``-Iterationsreihenfolge
          (das flache ``order_by("sort_order")`` in ``__init__`` mischt Innen-
          Indizes gruppierter Felder mit globalen Positionen);
        - aufeinanderfolgende freie Blöcke werden zu ``(None, [fields…])``-Runs
          zusammengefasst (deshalb kann ``None`` mehrfach vorkommen, jeweils
          zwischen Gruppen);
        - **leere Gruppen entfallen** — iteriert wird ausschliesslich ueber die
          im Formular tatsaechlich vorhandenen Felder, sodass eine Gruppe, deren
          Felder z.B. von ``remove_restricted_fields`` (#774) entfernt wurden,
          automatisch verschwindet (das kann zwei zuvor getrennte freie Runs
          verschmelzen);
        - Feld-Reihenfolge innerhalb einer Gruppe folgt dem Innen-``sort_order``.

        Sortiert wird in Python — bewusst **kein** SQL-``NULLS LAST`` (DB-fragil).
        """
        group_blocks: dict[Any, tuple[FieldGroup, list]] = {}
        free_blocks: list[tuple[int, str, str, BoundField]] = []
        for name in self.fields:
            group = self._field_group_of.get(name)
            position = self._block_position.get(name, 0)
            bound = self[name]
            if group is None:
                ft = self._field_templates.get(name)
                sort_name = ft.name if ft is not None else name
                free_blocks.append((position, sort_name, self._dtf_pk.get(name, ""), bound))
            else:
                _group, members = group_blocks.setdefault(group.pk, (group, []))
                members.append((self._block_position.get(name, 0), self._dtf_pk.get(name, ""), bound))

        # (sort_key, group_or_None, [BoundField, ...]) — sort_key wie im Board.
        blocks: list[tuple[tuple, FieldGroup | None, list[BoundField]]] = []
        for group, members in group_blocks.values():
            members.sort(key=lambda member: (member[0], member[1]))
            blocks.append(((group.sort_order, 0, group.name, ""), group, [member[2] for member in members]))
        for position, sort_name, pk, bound in free_blocks:
            blocks.append(((position, 1, sort_name, pk), None, [bound]))
        blocks.sort(key=lambda entry: entry[0])

        result: list[tuple[FieldGroup | None, list[BoundField]]] = []
        for _key, group, bounds in blocks:
            if group is None and result and result[-1][0] is None:
                result[-1][1].extend(bounds)
            else:
                result.append((group, list(bounds)))
        return result

    def grouped_blocks(self) -> list[dict[str, Any]]:
        """``grouped_fields()`` angereichert um die Bedingungs-Slugliste je Block (#1867).

        Liefert ``[{"group": FieldGroup | None, "fields": [BoundField, ...],
        "condition_slugs": "slug-a,slug-b"}, ...]`` — ein Eintrag je Block aus
        :meth:`grouped_fields` (die bleibt unverändert, inkl. ihrer Sortierung/
        Skip-leerer-Gruppen-Logik und ihrer eigenen Tests).

        ``condition_slugs`` ist eine kommagetrennte Liste NUR dann, wenn JEDES
        Feld des Blocks in :attr:`condition_target_slugs` liegt (Payload-basiert,
        also nach ``remove_restricted_fields``/#774) — sonst ein leerer String.
        Grund: eine Karte kann clientseitig nur komplett verschwinden, wenn
        ALLE ihre Felder Bedingungs-Ziele sind; enthält sie ein unkonditioniertes
        Feld, bleibt sie unabhängig vom Store immer sichtbar und braucht keinen
        Sichtbarkeits-Wrapper (Template entscheidet anhand dieses Strings, ob die
        Karte in die Alpine-Komponente ``conditionalGroup`` gewrappt wird).

        Gilt gleichermaßen für benannte Gruppen UND den ``group=None``-Block der
        ungruppierten Felder — beide teilen dasselbe Karten-Markup.
        """
        targets = self.condition_target_slugs
        blocks: list[dict[str, Any]] = []
        for group, fields in self.grouped_fields():
            slugs = [bf.name for bf in fields]
            all_targets = bool(slugs) and all(slug in targets for slug in slugs)
            blocks.append(
                {
                    "group": group,
                    "fields": fields,
                    "condition_slugs": ",".join(slugs) if all_targets else "",
                }
            )
        return blocks

    def conditions_payload(self) -> dict[str, Any] | None:
        """E12-Payload der Feld-Bedingungen für das AKTUELLE Feldset — oder ``None``.

        **Vertrag (Refs #1057, E8/#774):** Die Feld-Slugs werden **zur Aufrufzeit**
        aus ``self.fields`` gelesen, also NACH ``remove_restricted_fields`` (die
        Views rufen das unmittelbar nach der Konstruktion). Dadurch fließen die
        Vergleichswerte sensibilitäts-beschränkter Quellfelder nie ins Frontend-
        JSON: :func:`~core.services.conditions.build_conditions_payload` verwirft
        jede Bedingung, deren ``source`` **oder** ``target`` nicht (mehr) im
        Feldset liegt. ``None``, wenn danach keine Bedingung übrig bleibt.

        Lazy-gecacht — der erste Aufruf muss also NACH dem Restrict-Schritt
        erfolgen (Template-Rendering und ``clean()`` tun das).
        """
        cached = getattr(self, "_conditions_payload_cache", _PAYLOAD_UNSET)
        if cached is _PAYLOAD_UNSET:
            if self.document_type is None:
                cached = None
            else:
                # #1716: die in ``__init__`` einmal geladenen Bedingungen
                # injizieren — kein zweiter Condition-Query.
                cached = build_conditions_payload(
                    self.document_type,
                    list(self.fields.keys()),
                    conditions=self._field_conditions,
                )
            self._conditions_payload_cache = cached
        return cached

    @property
    def condition_target_slugs(self) -> set[str]:
        """Slugs der Felder, die im AKTUELLEN Payload ein Bedingungs-**Ziel** sind.

        Basis ist :meth:`conditions_payload` — also NACH ``remove_restricted_fields``
        und der #774-Drop-Regel. Nur diese Felder können clientseitig je verborgen
        werden und tragen daher im Template ``x-cloak`` (Flacker-Schutz gegen das
        Aufblitzen versteckter Ziele vor dem Alpine-Init). Unkonditionierte Felder
        und Quellfelder cloaken NIE — sie bleiben bei einem Alpine-Boot-Fehler
        sichtbar/bedienbar (App-Resilienz). Ein per #774 verworfenes Ziel taucht
        nicht im Payload auf, ist also unbedingt sichtbar und wird ebenfalls nicht
        gecloakt.
        """
        payload = self.conditions_payload()
        if not payload:
            return set()
        return {condition["target"] for condition in payload["conditions"]}

    def _condition_values(self, cleaned: dict[str, Any]) -> dict[str, Any]:
        """Normalisierte Ist-Werte je aktuellem Feld-Slug (Evaluator-Eingabe E11/E14).

        Fehlt ein Feld in ``cleaned`` (z.B. eigener Validierungsfehler des
        Quellfelds), gilt sein Wert als ``None`` → leer (Risiko 6). Number-Werte
        werden auf ``float`` gezogen (Decimal-Falle, :func:`_coerce_condition_number`).
        """
        values: dict[str, Any] = {}
        for slug in self.fields:
            ft = self._field_templates.get(slug)
            if ft is None:
                continue
            normalized = normalize_value(ft.field_type, cleaned.get(slug))
            if ft.field_type == NUMBER:
                normalized = _coerce_condition_number(normalized)
            values[slug] = normalized
        return values

    def _enforce_conditional_required(self, cleaned: dict[str, Any]) -> None:
        """Pflicht nur für **sichtbare** konditionale Ziel-Felder erzwingen (E16/E17).

        Versteckte Ziele bleiben leer zulässig (Issue-Kernfall); Kaskaden-Verstecken
        (E7) folgt aus dem gemeinsamen Evaluator. Ein FILE-Ziel gilt als gefüllt,
        wenn eine neue Datei hochgeladen wurde ODER bereits ein Anhang existiert
        (``existing_file_slugs``, E17).
        """
        targets = self._condition_target_slugs & set(self.fields)
        if not targets:
            return

        values = self._condition_values(cleaned)
        payload = self.conditions_payload()
        if payload is not None:
            # E12 liefert ``{slug: {"type": t}}``; der Evaluator erwartet die
            # Kurzform ``{slug: t}`` (ADR-035 § 8).
            fields_short = {slug: spec["type"] for slug, spec in payload["fields"].items()}
            visible = evaluate_conditions(fields_short, payload["conditions"], values)
        else:
            # Keine (verbleibende) Bedingung ⇒ alle Felder unbedingt sichtbar (E5):
            # ein Pflicht-Ziel behält damit seine Pflicht (kein stiller Opt-out,
            # etwa wenn die Quelle per #774-Drop wegfiel).
            visible = set(self.fields)

        for slug in targets:
            ft = self._field_templates[slug]
            if not ft.is_required or slug not in visible or slug in self.errors:
                continue
            if ft.field_type == FILE:
                filled = bool(cleaned.get(slug)) or slug in self._existing_file_slugs
            else:
                filled = not is_empty(values.get(slug))
            if not filled:
                self.add_error(slug, self.fields[slug].error_messages["required"])

    def clean(self):
        cleaned = super().clean() or {}
        # P7 C-2 (Refs #1730): die Conditional-Required-Pruefung braucht KEIN
        # facility (nur Feld-/Bedingungs-Metadaten) und darf daher nie an einem
        # fehlenden facility scheitern — sonst wird eine Pflichtfeld-Verletzung
        # (sichtbares Ziel bleibt leer) still durchgewunken. Frueher stand hier
        # ein frueher ``if not self.facility: return cleaned``-Ausstieg VOR
        # diesem Aufruf.
        file_fields = {
            field_name: field_obj
            for field_name, field_obj in self.fields.items()
            if isinstance(field_obj, forms.FileField)
        }
        has_bound_upload = any(cleaned.get(field_name) for field_name in file_fields)
        if has_bound_upload:
            # Nur die Upload-Validierung braucht facility
            # (``_resolve_upload_constraints`` liest die Facility-``Settings``).
            # Liegt trotzdem ein gebundener Upload vor, ist ein fehlendes
            # facility ein Programmierfehler des Aufrufers (alle View-Aufrufer
            # uebergeben facility) — fail-loud statt den Upload klaglos
            # durchzuwinken.
            if not self.facility:
                raise ValueError(
                    "DynamicDataForm.clean(): Datei-Upload gebunden, aber kein facility uebergeben — "
                    "Upload-Validierung (_resolve_upload_constraints) kann ohne facility nicht laufen. "
                    "Programmierfehler des Aufrufers: facility muss beim Konstruieren der Form gesetzt sein."
                )
            allowed, max_mb, max_bytes = _resolve_upload_constraints(self.facility)
            for field_name in file_fields:
                value = cleaned.get(field_name)
                if not value:
                    continue
                # MultipleFileField → Liste; klassisches FileField → Einzelobjekt.
                uploads = value if isinstance(value, list) else [value]
                for uploaded in uploads:
                    if not uploaded:
                        continue
                    for error in _file_upload_errors(uploaded, allowed, max_mb, max_bytes):
                        self.add_error(field_name, error)
        # M1/P4 (Refs #1057): Hidden-Required im Anschluss an die File-Checks —
        # laeuft jetzt IMMER, unabhaengig von facility.
        self._enforce_conditional_required(cleaned)
        return cleaned
