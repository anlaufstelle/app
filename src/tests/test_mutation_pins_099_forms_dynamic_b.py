"""Pin-Tests der Welle L (0.99) — Vollmutation #2174, Batch ``dynamic-b``.

Refs #2174,
Refs #2193.

Modul ``core.forms.dynamic`` — Batch ``dynamic-b`` deckt die Funktionen
``MultipleFileField.__init__``, ``_select_choices``, ``_resolve_upload_constraints``,
``DynamicDataForm.__init__``, ``DynamicDataForm.conditions_payload``,
``DynamicDataForm._condition_values`` und ``DynamicDataForm._enforce_conditional_required``
ab (der Schwester-Batch ``dynamic-a`` deckt die restlichen Funktionen desselben
Moduls in einer eigenen Datei, ``test_mutation_pins_099_forms_dynamic.py``).

Gepinnt werden die 19 nicht-aequivalenten Survivors in zwoelf Tests:

* **Konstruktor-Vertrag ``MultipleFileField``** (``TestMultipleFileFieldInit``)
  — ``__init___mutmut_2``/``_4`` (Default-Widget wird ``None`` statt
  ``MultipleFileInput()`` — ohne explizites ``widget`` faellt die Multi-Upload-
  Faehigkeit auf Djangos Einzel-Datei-Widget zurueck) und ``_7`` (``*args``
  wird beim Weiterreichen an ``Field.__init__`` verschluckt statt durchgereicht
  — ``forms.Field.__init__`` ist rein Keyword-only, ein positionales Argument
  MUSS mit ``TypeError`` durchschlagen, nicht klaglos verschwinden — Fail-loud-
  Prinzip dieses Moduls, vgl. ``clean()``s Facility-Programmierfehler).
* **``_select_choices``: fehlender ``is_active``-Schluessel** (``_17``/``_19``/
  ``_22``) — alle drei tauschen den Default von ``True`` gegen ``None``/(Ein-Arg-
  Form, ebenfalls ``None``)/``False``; alle drei sind gleichermassen falsy, das
  Verhalten ist fuer diesen Fall identisch. Eine Options-Zeile OHNE
  ``is_active``-Schluessel (Alt-/Fremddaten) gilt beim Original als aktiv
  (Default ``True``) und bleibt ohne Zusatz-Eintrag; alle drei Mutanten haengen
  faelschlich einen doppelten ``"… (deaktiviert)"``-Eintrag an, der den
  Original-Eintrag im ``dict(choices)`` ueberschreibt.
* **``conditions_payload``: Cache-Lookup gebrochen** (``_2``/``_8``/``_9``) —
  ``getattr(None, …)`` bzw. ein verfaelschter Attributname sorgen dafuer, dass
  der Lookup NIE den zuvor gesetzten Cache trifft; jeder Aufruf berechnet das
  Payload neu (``build_conditions_payload`` wird bei jedem Aufruf erneut
  ausgefuehrt statt nur beim ersten).
* **``conditions_payload``: Rueckgabewert ohne ``document_type``** (``_12``) —
  ``cached = ""`` statt ``None``; der dokumentierte Vertrag („oder ``None``")
  bricht fuer jeden Aufrufer, der explizit ``is None`` prueft.
* **``_condition_values``: defensiver Zweig ``ft is None``** (``_5``) — ``break``
  statt ``continue`` verwirft nicht nur den betroffenen Slug, sondern ALLE
  nachfolgenden Feld-Werte der Iteration.
* **``_condition_values``: falscher Feldtyp an den Normalizer** (``_7``) —
  ``normalize_value(None, …)`` statt ``normalize_value(ft.field_type, …)``;
  DATE/TIME-Werte werden nicht mehr auf den erwarteten ISO-String normalisiert
  (bleiben rohe ``date``/``time``-Objekte), was die Bedingungs-Auswertung
  (E9-Vergleiche) und damit die Pflicht-Durchsetzung fuer bedingte Zielfelder
  unterlaeuft.
* **``_condition_values``: Decimal-Faelle nicht mehr auf ``float`` gezogen**
  (``_12``) — ``if ft.field_type != NUMBER`` (invertiert) unterlaesst die
  Koerzierung ausgerechnet fuer NUMBER-Felder; ``Decimal("1.1") != 1.1``
  (dokumentierte Falle direkt im Docstring von ``_coerce_condition_number``).
* **``_enforce_conditional_required``: Pflichtpruefung bricht vorzeitig ab**
  (``_31``) — ``break`` statt ``continue``; trifft die Iteration zuerst ein
  Ziel, das ueberspringt (nicht pflichtig/nicht sichtbar/schon fehlerhaft),
  werden ALLE nachfolgenden Ziele NIE geprueft — eine echte Pflichtfeld-
  Verletzung eines spaeteren Ziels wird verschluckt. Echte ``set``-Iteration
  ist hashseed-abhaengig; der Kill-Nachweis erzwingt die Reihenfolge deshalb
  ueber einen ``__and__``-Stub statt sich auf Zufall zu verlassen.
* **``_enforce_conditional_required``: Fehlermeldung verschluckt** (``_44``) —
  ``self.add_error(slug, None)`` statt der echten Feldmeldung;
  ``ValidationError(None)`` liefert das woertliche ``"None"`` statt eines
  brauchbaren Fehlertexts (die Sperre selbst bleibt zwar bestehen, die
  Fehlermeldung ist aber fuer die Fachkraft wertlos).
* **``DynamicDataForm.__init__``: ``_dtf_pk``-Tie-Break** (``_26``/``_27``) —
  ``None`` bzw. ``str(None)`` statt der echten ``DocumentTypeField``-PK;
  ``_dtf_pk`` liefert laut Klassendocstring den deterministischen Tie-Break
  bei Positionsgleichheit in ``grouped_fields()``.
* **``DynamicDataForm.__init__``: ``select_related`` verliert einen Join-Pfad**
  (``_32``/``_33``) — je eine der beiden ``field_template``-Ketten
  (``target_field``/``depends_on_field``) fehlt im ``select_related``; der
  Zugriff darauf loest dann eine zusaetzliche Query aus (N+1) statt aus dem
  bereits geladenen JOIN zu kommen.

Aequivalente Mutanten (nachweislich verhaltensgleich, bewusst NICHT gepinnt):

* ``__init___mutmut_10`` (``self._field_conditions: list[FieldCondition] = None``
  statt ``[]``) — dieses Attribut wird ausschliesslich in ``conditions_payload()``
  gelesen, dort nur im ``else``-Zweig (``document_type is not None``). Dieser
  Zweig ist aber genau dann NIE erreichbar, wenn ``_field_conditions`` noch den
  mutierten Wert traegt: ``__init__`` weist ``_field_conditions`` NACH dem
  fruehen ``if not document_type: return``-Ausstieg neu zu — ist ``document_type``
  gesetzt, ueberschreibt diese Zuweisung den mutierten Wert laengst korrekt,
  bevor ``conditions_payload()`` je aufgerufen wird. Ist ``document_type`` NICHT
  gesetzt, kuerzt ``conditions_payload()`` selbst schon vorher ab
  (``if self.document_type is None: cached = None``) und liest
  ``_field_conditions`` gar nicht erst. Repo-weiter Grep bestaetigt: kein
  weiterer Leser existiert.
* ``_enforce_conditional_required__mutmut_2`` (``targets = self._condition_target_slugs
  | set(self.fields)`` statt ``&``) — fuer jeden Slug AUSSERHALB der echten
  Zielmenge gilt zwingend eines von zwei Dingen: entweder ``ft.is_required`` ist
  ``False`` (die Schleife ueberspringt ihn ueber ``not ft.is_required``), oder
  ``ft.is_required`` ist ``True`` — dann behaelt dieses Feld (anders als ein
  echtes Bedingungsziel) sein normales Django-``required=True``, sodass Djangos
  EIGENE Feld-Validierung (laeuft VOR ``clean()``) eine Leerwert-Verletzung
  bereits als Fehler eingetragen hat (die Schleife ueberspringt ihn ueber
  ``slug in self.errors``). Der um alle Formularfelder erweiterte ``targets``-
  Durchlauf kann also nie einen Fehler erzeugen, den es ohne die Erweiterung
  nicht gaebe.
* ``x__resolve_upload_constraints__mutmut_3`` (``facility_settings = ""`` statt
  ``None`` im ``DoesNotExist``-Zweig) — beide nachfolgenden Verwendungen
  (``raw = … if facility_settings else ""`` und ``max_mb = … if facility_settings
  else DEFAULT_MAX_FILE_SIZE_MB``) sind reine Wahrheitswert-Abfragen; ``""`` ist
  exakt so falsy wie ``None``, ``.allowed_file_types``/``.max_file_size_mb``
  werden im Falsy-Zweig nie gelesen.
* ``x__resolve_upload_constraints__mutmut_12`` (``.lstrip("XX.XX")`` statt
  ``.lstrip(".")``) — der Aufruf laeuft IMMER nach ``.lower()``; ``str.lstrip``
  ist case-sensitiv, das zusaetzliche Gross-``X`` im mutmut-Marker kann auf
  einem bereits kleingeschriebenen String nie zutreffen (empirisch mit
  ``xlsx``/``XLSX``/``.xlsx``/``"  .PDF "`` verifiziert — bytgleiches Ergebnis).

``_encryption_key`` liegt modulweit an: der Mutantenbaum (``/work/anlaufstelle/mutants``)
hat keine ``.env`` und damit keinen ``ENCRYPTION_KEY``; ohne die Fixture waeren
die Pins dort aus dem FALSCHEN Grund rot und der Kill-Nachweis wertlos.
"""

from __future__ import annotations

import datetime as dt
import decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.forms.dynamic import DynamicDataForm, MultipleFileField, MultipleFileInput
from core.models import DocumentType, DocumentTypeField, FieldCondition, FieldTemplate

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


# ---------------------------------------------------------------------------
# Lokale Fixtures (bewusst NICHT in conftest.py — Parallel-Konvention der
# Pin-Dateien, siehe test_mutation_pins_097_forms_facility_settings.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def cond_required_doctype(facility):
    """NUMBER-Quelle ``dauer`` + TEXTAREA-Pflicht-Ziel ``notiz``, Bedingung dauer>10.

    Eigenstaendiges Pendant zu ``test_forms_dynamic.py``s ``cond_doctype`` —
    hier zusaetzlich mit ``is_required=True`` auf dem Ziel, das braucht der
    Kill-Nachweis fuer ``_enforce_conditional_required__mutmut_44``.
    """
    dt_obj = DocumentType.objects.create(facility=facility, name="Bedingt (Pflicht-Ziel, dynamic-b)")
    ft_dauer = FieldTemplate.objects.create(
        facility=facility, name="Dauer (b)", field_type=FieldTemplate.FieldType.NUMBER
    )
    ft_notiz = FieldTemplate.objects.create(
        facility=facility,
        name="Notiz (b)",
        field_type=FieldTemplate.FieldType.TEXTAREA,
        is_required=True,
    )
    DocumentTypeField.objects.create(document_type=dt_obj, field_template=ft_dauer, sort_order=0)
    DocumentTypeField.objects.create(document_type=dt_obj, field_template=ft_notiz, sort_order=1)
    FieldCondition.objects.create(
        target_field=dt_obj.fields.get(field_template__slug=ft_notiz.slug),
        depends_on_field=dt_obj.fields.get(field_template__slug=ft_dauer.slug),
        operator=FieldCondition.Operator.GREATER_THAN,
        value=10,
        group_index=0,
    )
    return dt_obj


class _OrderedTargetSlugs:
    """Test-Helfer (Kill-Nachweis ``_enforce_conditional_required__mutmut_31``).

    Erzwingt eine STABILE Iterationsreihenfolge fuer den Ausdruck
    ``self._condition_target_slugs & set(self.fields)``: echte ``set``-
    Iteration haengt vom (pro Prozess randomisierten) Hash-Seed ab — ohne
    erzwungene Reihenfolge waere der Kill-Nachweis der ``continue``->``break``-
    Mutante zufallsabhaengig statt deterministisch rot/gruen.
    """

    def __init__(self, ordered_slugs):
        self._ordered = list(ordered_slugs)

    def __and__(self, other):
        return [slug for slug in self._ordered if slug in other]

    def __bool__(self):
        return bool(self._ordered)


# ---------------------------------------------------------------------------
# MultipleFileField.__init__ — Konstruktor-Vertrag
# ---------------------------------------------------------------------------


class TestMultipleFileFieldInit:
    """``MultipleFileField.__init__`` — Default-Widget + Argument-Weiterreichung."""

    def test_default_widget_is_multiplefileinput_without_explicit_widget(self):
        """Pinnt ``_2``/``_4`` (``kwargs.setdefault("widget", None)`` bzw. die
        Ein-Arg-Form ``kwargs.setdefault("widget")`` — beide setzen den Default
        auf ``None`` statt ``MultipleFileInput()``)."""
        field = MultipleFileField()

        assert isinstance(field.widget, MultipleFileInput)

    def test_positional_arguments_raise_instead_of_being_dropped(self):
        """Pinnt ``_7`` (``super().__init__(**kwargs)`` statt
        ``super().__init__(*args, **kwargs)`` — ``*args`` wird verschluckt).

        ``forms.Field.__init__`` ist rein Keyword-only (``def __init__(self, *,
        required=True, …)``); ein positionales Argument MUSS deshalb mit
        ``TypeError`` durchschlagen. Die Mutante wuerde es klaglos ignorieren.
        """
        with pytest.raises(TypeError):
            MultipleFileField("unerwartetes-positional-argument")


# ---------------------------------------------------------------------------
# _select_choices — fehlender is_active-Schluessel in options_json
# ---------------------------------------------------------------------------


class TestSelectChoicesMissingIsActiveKey:
    """``_select_choices`` — Options-Zeilen ohne ``is_active``-Schluessel."""

    def test_option_without_is_active_key_defaults_to_active(self, facility):
        """Pinnt ``_17``/``_19``/``_22`` (Default von ``True`` auf ``None``/``None``/
        ``False`` gekippt — alle drei sind gleichermassen falsy).

        Ohne ``is_active``-Schluessel (Alt-/Fremddaten) gilt eine Option als
        aktiv (Default ``True``) und bekommt KEINEN zusaetzlichen
        ``"… (deaktiviert)"``-Eintrag. Alle drei Mutanten haengen den
        Zusatz-Eintrag faelschlich an; er ueberschreibt den echten Eintrag im
        ``dict(choices)`` (letzter Eintrag gewinnt).
        """
        dt_obj = DocumentType.objects.create(facility=facility, name="Auswahl ohne is_active-Schluessel (b)")
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Auswahl (b)",
            field_type=FieldTemplate.FieldType.SELECT,
            options_json=[{"slug": "a", "label": "A"}],  # bewusst OHNE "is_active"
        )
        DocumentTypeField.objects.create(document_type=dt_obj, field_template=ft, sort_order=0)

        form = DynamicDataForm(document_type=dt_obj, facility=facility, initial_data={ft.slug: "a"})

        choices = dict(form.fields[ft.slug].choices)
        assert choices["a"] == "A"


# ---------------------------------------------------------------------------
# DynamicDataForm.conditions_payload — Cache + Rueckgabewert-Vertrag
# ---------------------------------------------------------------------------


class TestConditionsPayloadCacheAndContract:
    """``conditions_payload`` — Lazy-Cache und ``None``-Vertrag ohne document_type."""

    def test_payload_is_cached_not_recomputed_on_second_call(
        self, facility, doc_type_contact, field_condition, monkeypatch
    ):
        """Pinnt ``_2``/``_8``/``_9`` (``getattr``-Cache-Lookup trifft nie den
        gesetzten Cache — jeder Aufruf berechnet das Payload neu)."""
        import core.forms.dynamic as dynamic_module

        original = dynamic_module.build_conditions_payload
        calls: list[int] = []

        def _spy(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)

        monkeypatch.setattr(dynamic_module, "build_conditions_payload", _spy)

        form = DynamicDataForm(document_type=doc_type_contact, facility=facility)
        first = form.conditions_payload()
        second = form.conditions_payload()

        assert first == second
        assert len(calls) == 1

    def test_missing_document_type_returns_none_not_empty_string(self, facility):
        """Pinnt ``_12`` (``cached = ""`` statt ``None`` im document_type=None-Zweig).

        Der Docstring verspricht explizit „oder ``None``" — ein Aufrufer, der
        ``is None`` prueft (z. B. ``TestConditionsPayloadPassThrough`` im
        Bestand), muss sich darauf verlassen koennen.
        """
        form = DynamicDataForm(document_type=None, facility=facility)

        assert form.conditions_payload() is None


# ---------------------------------------------------------------------------
# DynamicDataForm._condition_values
# ---------------------------------------------------------------------------


class TestConditionValues:
    """``_condition_values`` — Normalisierung der Ist-Werte je Feld-Slug."""

    def test_missing_field_template_skips_only_that_field_not_the_rest(self, facility, doc_type_contact):
        """Pinnt ``_5`` (``break`` statt ``continue`` im ``ft is None``-Zweig).

        Simuliert defensiv einen Slug ohne ``FieldTemplate`` (sollte bei
        normaler Konstruktion nicht vorkommen, ist aber genau der Fall, den
        der Zweig abfaengt): der Bug wuerde ALLE nachfolgenden Feld-Werte der
        Iteration verwerfen, nicht nur den betroffenen Slug.
        """
        form = DynamicDataForm(document_type=doc_type_contact, facility=facility)
        assert list(form.fields) == ["dauer", "notiz"], "Testannahme: dauer vor notiz iteriert"
        del form._field_templates["dauer"]

        values = form._condition_values({"dauer": 5, "notiz": "text"})

        assert "dauer" not in values
        assert values["notiz"] == "text"

    def test_date_value_is_normalized_to_isoformat_string(self, facility):
        """Pinnt ``_7`` (``normalize_value(None, …)`` statt ``normalize_value(ft.field_type, …)``).

        Ohne den echten Feldtyp faellt ein DATE-Wert auf den defensiven
        „unbekannter Typ"-Zweig zurueck und bleibt ein rohes ``date``-Objekt
        statt des erwarteten ISO-Strings — das unterlaeuft die
        Bedingungs-Auswertung fuer jedes DATE-Quellfeld.
        """
        dt_obj = DocumentType.objects.create(facility=facility, name="Datum-Quelle (b)")
        ft_date = FieldTemplate.objects.create(
            facility=facility, name="Start (b)", field_type=FieldTemplate.FieldType.DATE
        )
        DocumentTypeField.objects.create(document_type=dt_obj, field_template=ft_date, sort_order=0)

        form = DynamicDataForm(document_type=dt_obj, facility=facility)
        values = form._condition_values({ft_date.slug: dt.date(2026, 1, 15)})

        assert values[ft_date.slug] == "2026-01-15"

    def test_number_value_is_coerced_to_float_not_left_as_decimal(self, facility, doc_type_contact):
        """Pinnt ``_12`` (``if ft.field_type != NUMBER`` — invertiert, koerziert
        NUMBER-Felder dann gerade NICHT mehr).

        ``Decimal("1.1") != 1.1`` (dokumentierte Falle im Docstring von
        ``_coerce_condition_number``) — ohne Koerzierung bleibt der Wert ein
        ``Decimal`` und die Gleichheitspruefung gegen den float-Vergleichswert
        schlaegt fehl.
        """
        form = DynamicDataForm(document_type=doc_type_contact, facility=facility)

        values = form._condition_values({"dauer": decimal.Decimal("1.1"), "notiz": ""})

        assert values["dauer"] == 1.1


# ---------------------------------------------------------------------------
# DynamicDataForm._enforce_conditional_required
# ---------------------------------------------------------------------------


class TestEnforceConditionalRequired:
    """``_enforce_conditional_required`` — Pflichtdurchsetzung sichtbarer Ziele."""

    def test_break_would_swallow_a_later_required_target(self, facility, doc_type_contact):
        """Pinnt ``_31`` (``break`` statt ``continue`` im Skip-Zweig).

        Das erste Ziel im (erzwungenen) Iterationsdurchlauf ist NICHT pflichtig
        (ueberspringt regulaer via ``continue``); das zweite ist pflichtig,
        sichtbar und leer. Bricht die Schleife beim ersten Ziel ab (Mutante),
        wird das zweite NIE geprueft — die Pflichtverletzung verschwindet
        komplett, ``is_valid()`` wird faelschlich ``True``.
        """
        form = DynamicDataForm({"dauer": "", "notiz": ""}, document_type=doc_type_contact, facility=facility)
        # "dauer" bleibt regulaer nicht pflichtig (Fixture-Default); "notiz"
        # wird hier gezielt zur Pflicht gemacht, um den zweiten Loop-Schritt
        # ueberpruefbar zu machen.
        form._field_templates["notiz"].is_required = True
        form._condition_target_slugs = _OrderedTargetSlugs(["dauer", "notiz"])

        assert not form.is_valid()
        assert "notiz" in form.errors

    def test_required_error_uses_the_real_field_message_not_none(self, facility, cond_required_doctype):
        """Pinnt ``_44`` (``self.add_error(slug, None)`` statt der echten
        Feldmeldung — ``ValidationError(None)`` liefert woertlich ``"None"``)."""
        dauer_slug = cond_required_doctype.fields.get(
            field_template__field_type=FieldTemplate.FieldType.NUMBER
        ).field_template.slug
        notiz_slug = cond_required_doctype.fields.get(
            field_template__field_type=FieldTemplate.FieldType.TEXTAREA
        ).field_template.slug

        form = DynamicDataForm(
            {dauer_slug: "15", notiz_slug: ""}, document_type=cond_required_doctype, facility=facility
        )
        expected = str(form.fields[notiz_slug].error_messages["required"])

        assert not form.is_valid()
        assert form.errors[notiz_slug] == [expected]


# ---------------------------------------------------------------------------
# DynamicDataForm.__init__ — _dtf_pk-Tie-Break + select_related-Vollstaendigkeit
# ---------------------------------------------------------------------------


class TestDynamicDataFormInitInternals:
    """``__init__`` — dokumentierte interne Vertraege (Tie-Break, Query-Praefetch)."""

    def test_dtf_pk_tie_break_holds_the_documenttypefield_pk(self, facility, doc_type_contact):
        """Pinnt ``_26`` (``None``) und ``_27`` (``str(None)``) statt ``str(dtf.pk)``.

        ``_dtf_pk`` liefert laut Klassendocstring von ``DynamicDataForm`` den
        deterministischen Tie-Break bei Positionsgleichheit in
        ``grouped_fields()`` — es MUSS die echte ``DocumentTypeField``-PK sein.
        """
        form = DynamicDataForm(document_type=doc_type_contact, facility=facility)
        dtf = doc_type_contact.fields.get(field_template__slug="dauer")

        assert form._dtf_pk["dauer"] == str(dtf.pk)

    def test_field_condition_load_prefetches_both_field_template_chains(
        self, facility, doc_type_contact, field_condition
    ):
        """Pinnt ``_32``/``_33`` (je eine ``select_related``-Kette fehlt —
        N+1-Query beim Zugriff auf ``target_field.field_template`` bzw.
        ``depends_on_field.field_template``).

        Die Konstruktion selbst MUSS im Capture-Fenster liegen, nicht nur der
        Attributzugriff danach: ``__init__`` liest ``c.target_field.field_template``
        bereits selbst (fuer ``_condition_target_slugs``) und cached das Ergebnis
        auf dem Objekt — ein fehlendes ``target_field__field_template`` im
        ``select_related`` faellt daher nur auf, wenn die dabei ausgeloeste
        Zusatz-Query mitgezaehlt wird (verifiziert: 2 Queries mit vollstaendigem
        ``select_related``, 4 mit je einer fehlenden Kette).
        """
        with CaptureQueriesContext(connection) as ctx:
            form = DynamicDataForm(document_type=doc_type_contact, facility=facility)
            assert form._field_conditions, "Fixture muss mindestens eine FieldCondition liefern"
            for condition in form._field_conditions:
                _ = condition.target_field.field_template.slug
                _ = condition.depends_on_field.field_template.slug

        assert len(ctx.captured_queries) == 2
