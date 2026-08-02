"""Forms for event recording."""

from typing import Any

from django import forms
from django.conf import settings as django_settings
from django.core.exceptions import ValidationError
from django.forms.boundfield import BoundField
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.constants import DEFAULT_ALLOWED_FILE_TYPES, DEFAULT_MAX_FILE_SIZE_MB
from core.forms.widgets import INPUT_CSS
from core.models import Case, Client, DocumentType, DocumentTypeField, FieldCondition, FieldGroup, FieldTemplate
from core.models.settings import Settings
from core.services.conditions import (
    FILE,
    NUMBER,
    build_conditions_payload,
    evaluate_conditions,
    is_empty,
    normalize_value,
)


class MultipleFileInput(forms.ClearableFileInput):
    """ClearableFileInput-Widget mit ``multiple``-Attribut — Django erlaubt
    Multi-File-Upload seit 5.0, aber der Standard-Widget blockiert das aktiv.
    Refs #622.
    """

    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """FileField, das eine Liste von UploadedFile zurückgibt (auch für N=0/1).

    Wird für FILE-Feldtypen in :class:`DynamicEventDataForm` verwendet, damit
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


def _select_choices(ft, initial_data, select_type):
    """Refs #1160: Choices fuer SELECT/MULTI_SELECT inkl. Beibehaltung
    inaktiver Werte des bearbeiteten Events.

    ``select_type`` ist die ``SELECT``-Konstante aus ``core.services.system`` —
    bei SELECT wird die Leer-Option vorangestellt. Wirkungs-identisch zum
    frueheren Inline-Block in :meth:`DynamicEventDataForm.__init__`.
    """
    choices = [("", "---------")] + list(ft.choices) if ft.field_type == select_type else list(ft.choices)

    # Bei Bearbeitung: inaktive Werte des Events beibehalten
    if initial_data and ft.slug in initial_data:
        current_values = initial_data[ft.slug]
        if not isinstance(current_values, list):
            current_values = [current_values] if current_values else []
        for o in ft.options_json or []:
            if not o.get("is_active", True) and o["slug"] in current_values:
                choices.append((o["slug"], f"{o['label']} (deaktiviert)"))

    return choices


def _build_dynamic_field(ft, initial_data):
    """Refs #1160: Ein einzelnes Formularfeld aus einem ``FieldTemplate`` bauen.

    Aus :meth:`DynamicEventDataForm.__init__` herausgezogen — kapselt die
    Field-Klassen-/Widget-/CSS-/Choices-/Initial-Auswahl pro Feldtyp.
    Wirkungs-identisch; FILE -> ``MultipleFileField`` (Refs #622), die
    SELECT/MULTI_SELECT-Choices stammen aus :func:`_select_choices`.
    """
    from core.services.system import (
        FILE,
        MULTI_SELECT,
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


class DynamicEventDataForm(forms.Form):
    """Dynamic form based on DocumentType fields.

    Refs #907: Form-Field-, Widget-, Default-Auswahl pro Feldtyp
    laeuft ueber :data:`core.services.system.field_types.FIELD_TYPE_REGISTRY` —
    die alte ``FIELD_TYPE_MAP`` ist ersetzt. SELECT/MULTI_SELECT-Choices
    und die FILE-Sonderlogik bleiben in dieser Form, da sie auf Form-State
    (``initial_data``, deaktivierte Options) zugreifen.
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
        # E17: FILE-Slugs mit bestehendem Anhang-Marker (aus den Update-Views), damit
        # ein vorhandener Anhang beim Bearbeiten als „gefüllt" zählt.
        self._existing_file_slugs: set[str] = set(existing_file_slugs or ())
        # Ziel-Slugs aller Bedingungen dieses DocumentType (Pflicht wird für sie nur
        # bedingt/sichtbarkeitsabhängig durchgesetzt). Vor remove_restricted_fields
        # bestimmt — clean() schneidet zur Aufrufzeit mit self.fields.
        self._condition_target_slugs: set[str] = set()
        super().__init__(*args, **kwargs)
        if not document_type:
            return

        dtf_qs = (
            DocumentTypeField.objects.filter(
                document_type=document_type,
            )
            .select_related("field_template", "field_group")
            .order_by("sort_order")
        )

        for dtf in dtf_qs:
            ft = dtf.field_template
            self.fields[ft.slug] = _build_dynamic_field(ft, initial_data)
            self._field_group_of[ft.slug] = dtf.field_group
            self._field_templates[ft.slug] = ft

        # E16: Ziel-Felder einer Bedingung Django-seitig optional machen
        # (required=False) und den Pflicht-Marker separat setzen — die Pflicht
        # erzwingt clean() nur für tatsächlich sichtbare Ziele. Der Marker trägt
        # die ursprüngliche Pflicht (Pflicht-Stern im Template).
        self._condition_target_slugs = set(
            FieldCondition.objects.filter(target_field__document_type=document_type).values_list(
                "target_field__field_template__slug", flat=True
            )
        )
        for slug in self._condition_target_slugs & set(self.fields):
            ft = self._field_templates[slug]
            self.fields[slug].required = False
            self.fields[slug].is_conditionally_required = ft.is_required  # type: ignore[attr-defined]

    def grouped_fields(self) -> list[tuple[FieldGroup | None, list[BoundField]]]:
        """Gebundene Felder nach :class:`FieldGroup` gruppiert (M0/P4, Refs #1056).

        Liefert ``[(gruppe_oder_None, [BoundField, ...]), ...]``:

        - benannte Gruppen zuerst, sortiert nach ``(sort_order, name)``;
        - die ``None``-Sektion („Ohne Gruppe") steht **garantiert zuletzt**;
        - **leere Gruppen entfallen** — iteriert wird ausschliesslich ueber die
          im Formular tatsaechlich vorhandenen Felder, sodass eine Gruppe, deren
          Felder z.B. von ``remove_restricted_fields`` (#774) entfernt wurden,
          automatisch verschwindet;
        - Feld-Reihenfolge innerhalb einer Gruppe folgt der ``DocumentTypeField.
          sort_order`` (Einfuegereihenfolge aus ``__init__``).

        Gruppiert wird in Python — bewusst **kein** SQL-``NULLS LAST`` (DB-fragil).
        """
        buckets: dict[Any, tuple[FieldGroup | None, list[BoundField]]] = {}
        for name in self.fields:
            group = self._field_group_of.get(name)
            key = group.pk if group is not None else None
            if key not in buckets:
                buckets[key] = (group, [])
            buckets[key][1].append(self[name])

        named = [(group, bound) for group, bound in buckets.values() if group is not None]
        named.sort(key=lambda entry: (entry[0].sort_order, entry[0].name))
        result: list[tuple[FieldGroup | None, list[BoundField]]] = list(named)
        if None in buckets:
            result.append(buckets[None])
        return result

    def conditions_payload(self) -> dict[str, Any] | None:
        """E12-Payload der Feld-Bedingungen f\u00fcr das AKTUELLE Feldset \u2014 oder ``None``.

        **Vertrag (Refs #1057, E8/#774):** Die Feld-Slugs werden **zur Aufrufzeit**
        aus ``self.fields`` gelesen, also NACH ``remove_restricted_fields`` (die
        Views rufen das unmittelbar nach der Konstruktion). Dadurch flie\u00dfen die
        Vergleichswerte sensibilit\u00e4ts-beschr\u00e4nkter Quellfelder nie ins Frontend-
        JSON: :func:`~core.services.conditions.build_conditions_payload` verwirft
        jede Bedingung, deren ``source`` **oder** ``target`` nicht (mehr) im
        Feldset liegt. ``None``, wenn danach keine Bedingung \u00fcbrig bleibt.

        Lazy-gecacht \u2014 der erste Aufruf muss also NACH dem Restrict-Schritt
        erfolgen (Template-Rendering und ``clean()`` tun das).
        """
        cached = getattr(self, "_conditions_payload_cache", _PAYLOAD_UNSET)
        if cached is _PAYLOAD_UNSET:
            if self.document_type is None:
                cached = None
            else:
                cached = build_conditions_payload(self.document_type, list(self.fields.keys()))
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
        Quellfelds), gilt sein Wert als ``None`` \u2192 leer (Risiko 6). Number-Werte
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
        """Pflicht nur f\u00fcr **sichtbare** konditionale Ziel-Felder erzwingen (E16/E17).

        Versteckte Ziele bleiben leer zul\u00e4ssig (Issue-Kernfall); Kaskaden-Verstecken
        (E7) folgt aus dem gemeinsamen Evaluator. Ein FILE-Ziel gilt als gef\u00fcllt,
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
            # Kurzform ``{slug: t}`` (ADR-035 \u00a7 8).
            fields_short = {slug: spec["type"] for slug, spec in payload["fields"].items()}
            visible = evaluate_conditions(fields_short, payload["conditions"], values)
        else:
            # Keine (verbleibende) Bedingung \u21d2 alle Felder unbedingt sichtbar (E5):
            # ein Pflicht-Ziel beh\u00e4lt damit seine Pflicht (kein stiller Opt-out,
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
        if not self.facility:
            return cleaned
        allowed, max_mb, max_bytes = _resolve_upload_constraints(self.facility)
        for field_name, field_obj in self.fields.items():
            if not isinstance(field_obj, forms.FileField):
                continue
            value = cleaned.get(field_name)
            if not value:
                continue
            # MultipleFileField \u2192 Liste; klassisches FileField \u2192 Einzelobjekt.
            uploads = value if isinstance(value, list) else [value]
            for uploaded in uploads:
                if not uploaded:
                    continue
                for error in _file_upload_errors(uploaded, allowed, max_mb, max_bytes):
                    self.add_error(field_name, error)
        # M1/P4 (Refs #1057): Hidden-Required im Anschluss an die File-Checks.
        self._enforce_conditional_required(cleaned)
        return cleaned
